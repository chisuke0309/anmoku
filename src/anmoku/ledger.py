"""物差し：定義台帳（1つの用語・判断基準を1ファイル）と、ハブ宛ての質問5つ。

LLM は語りに出た用語の観察をまとめ、定義の案と質問を出すだけ。
状態（下書き／回答待ち／承認済み）と質問の優先順位は Python が決める。承認は人だけが行う。
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import yaml

from .llm import run_claude
from .prompts import agent_prompt
from .schema import LedgerOut, json_schema

LEDGER_SYSTEM = agent_prompt("ledger-steward")  # 正本：agents/ledger-steward.md


def ledger_prompt(terms: list[dict], hubs: list[dict], flow_lanes: list[str], existing: list[dict] | None = None) -> str:
    t = "\n".join(
        f"{x['id']} | 話し手:{x['speaker']} | {x['kind']} | {x['label']} | 知っている人:{x.get('holder') or '-'} | 言葉になっている:{x['defined']} | 「{x['quote']}」"
        for x in terms
    )
    h = "\n".join(f"- {c['name']}（呼び方：{'・'.join(c['aliases'])}／点数 {c['score']}）" for c in hubs)
    ex = "\n".join(f"- {x['id']}：{x['label']}（{ {'approved': '承認済み', 'draft': '下書き', 'needs_answer': '回答待ち'}.get(x.get('status'), x.get('status'))}）"
                   f"定義：{x.get('definition') or '未確定'}" for x in (existing or [])) or "（なし）"
    return f"""# 公式フローのレーン
{'、'.join(flow_lanes)}

# 今ある定義台帳（会社が持っているもの）
{ex}

# ハブの候補（仮説）
{h}

# 用語・判断基準の観察（ID | 話し手 | 種類 | 呼び方 | 知っている人 | 言葉になっているか | 引用）
{t}
"""


def load_existing(*dirs: Path | None) -> dict[str, dict]:
    """今ある定義台帳を読む。あとに渡したフォルダ（前回の出力）が優先（承認を引き継ぐため）。"""
    found: dict[str, dict] = {}
    for d in dirs:
        if d and d.exists():
            for f in sorted(d.glob("*.yaml")):
                doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
                if doc.get("id"):
                    found[doc["id"]] = {**found.get(doc["id"], {}), **doc}
    return found


def build_ledger(detect: dict, out_dir: Path, *, model: str | None = None, existing_dir: Path | None = None) -> dict:
    """定義台帳を作る。今ある台帳（existing_dir）と前回の出力の承認・定義は引き継ぐ。

    - 台帳にあり・承認済み：定義と状態はそのまま。語りの根拠だけを足す
    - 台帳にあるが未確定：語りの根拠と質問で更新する
    - 台帳に無い：新しい候補として加える（origin="new"）
    """
    final = detect["stages"][-1]["candidates"]
    hubs = [c for c in final if c.get("hub_hypothesis")][:3]
    terms = detect["terms"]
    existing = load_existing(existing_dir, out_dir / "ledger")
    case_ids = set(load_existing(existing_dir)) if existing_dir else set()
    res = run_claude(
        ledger_prompt(terms, hubs, detect["flow"]["lanes"], list(existing.values())),
        system=LEDGER_SYSTEM,
        schema=json_schema(LedgerOut),
        model=model,
        log_dir=out_dir / "llm",
        label="ledger",
    )
    proposal = LedgerOut.model_validate(res.data)
    result = merge_ledger(proposal, terms, existing, case_ids, hubs)
    result["cost_usd"] = res.cost_usd
    write_ledger(result["items"], out_dir / "ledger")
    (out_dir / "questions.md").write_text(render_questions(result["questions"], hubs), encoding="utf-8")
    (out_dir / "ledger.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


# 数・割合・金額・期間の言い方（例：二割五分、十万、3日）。承認済みの定義に無い数は「定義に無い基準」とみなす
QUANTITY = re.compile(r"[0-9０-９一二三四五六七八九十百千万何]+\s*(?:割|分|万|千|円|日|件|%|％|パーセント|時間|週|か月|ヶ月|カ月|年|人|回)")


def adds_quantity(quote: str, definition: str | None) -> bool:
    """引用に、承認済みの定義に書かれていない数・割合・金額があるか（一般的な機械の確認。シナリオに依らない）。"""
    d = unicodedata.normalize("NFKC", definition or "")
    return any(m.group(0).replace(" ", "") not in d for m in QUANTITY.finditer(unicodedata.normalize("NFKC", quote)))


def _evidence(ev: list[dict]) -> list[dict]:
    return [{"source": f"interviews/{e['interview']}.md", "line": e["line"], "quote": e["quote"], "speaker": e["speaker"]} for e in ev]


def merge_ledger(proposal: LedgerOut, terms: list[dict], existing: dict[str, dict], case_ids: set[str], hubs: list[dict]) -> dict:
    """LLM の提案と今ある台帳を合わせる（同じ入力なら同じ結果）。

    承認済みの項目の根拠には、その定義を直接支える観察だけを残す。LLM が「定義に無い基準を足している」とした観察と、
    定義に無い数・割合・金額を含む観察は、承認済みの側から外し「台帳に無い」側に出す（他の新しい項目に無ければ新しく作る）。
    """
    by_id = {t["id"]: t for t in terms}
    plists = (("item", proposal.items), ("implicit_rule", proposal.implicit_rules))
    approved_ids = {i for i, o in existing.items() if o.get("status") == "approved"}
    # 承認済みでない提案が使っている観察（外した観察の行き先があるかの確認に使う）
    covered = {r for _, pl in plists for p in pl if p.id not in approved_ids for r in p.evidence_refs if r in by_id}
    items: list[dict] = []
    seen: set[str] = set()
    spill: list[dict] = []
    for kind_group, plist in plists:
        for p in plist:
            if p.id in seen:
                continue
            old = existing.get(p.id)
            if old and old.get("status") == "approved":
                refs = [r for r in dict.fromkeys(p.evidence_refs + p.extends_refs) if r in by_id]
                if not refs:
                    continue
                seen.add(p.id)
                direct = [r for r in p.evidence_refs if r in by_id and r not in p.extends_refs
                          and not adds_quantity(by_id[r]["quote"], old.get("definition"))]
                moved = [r for r in refs if r not in direct]
                # 承認済みは人が決めたもの。定義と状態は変えず、定義を直接支える語りの根拠だけを足す
                items.append({**old, "group": old.get("group", kind_group), "evidence": _evidence([by_id[r] for r in direct]),
                              "questions": [], "origin": "existing" if p.id in case_ids else "carried",
                              "moved_evidence": moved})
                rest = [r for r in moved if r not in covered]
                if rest:
                    spill.append({"from": p, "old": old, "refs": rest, "group": kind_group})
                continue
            ev = [by_id[r] for r in p.evidence_refs if r in by_id]
            if not ev:
                continue  # 根拠が実在しない項目は捨てる
            seen.add(p.id)
            evidence = _evidence(ev)
            # 状態は Python が決める：語りに言葉になった根拠があり、定義案があるときだけ下書き
            status = "draft" if p.definition and any(e["defined"] for e in ev) else "needs_answer"
            items.append({
                "id": p.id,
                "label": old.get("label", p.label) if old else p.label,
                "kind": p.kind,
                "group": kind_group,
                "definition": p.definition if status == "draft" else None,
                "definition_hint": None if status == "draft" else p.definition,
                "status": status,
                "owner_hint": p.owner_hint,
                "evidence": evidence,
                "questions": p.questions[:3],
                "used_by": old.get("used_by", []) if old else [],
                "origin": ("existing" if p.id in case_ids else "carried") if old else "new",
            })
    # 承認済みの定義に無い基準で、他のどの項目にも入っていないもの → 台帳に無い新しい候補（回答待ち）
    for sp in spill:
        p, old = sp["from"], sp["old"]
        nid = f"{p.id}-not-in-ledger"
        if nid in seen:
            continue
        seen.add(nid)
        ev = [by_id[r] for r in sp["refs"]]
        label = p.label if p.label != old.get("label") else f"{old.get('label', p.id)}（台帳に無い基準）"
        qs = p.questions[:3] or [f"「{old.get('label', p.id)}」で、台帳の定義（{old.get('definition')}）に書かれていない基準・範囲・例外を、例とあわせて教えてください。"]
        items.append({"id": nid, "label": label, "kind": "rule", "group": sp["group"], "definition": None,
                      "definition_hint": p.definition if p.definition and p.definition != old.get("definition") else None,
                      "status": "needs_answer", "owner_hint": p.owner_hint, "evidence": _evidence(ev), "questions": qs,
                      "used_by": [], "origin": "new", "split_from": p.id})
    # 語りに出てこなかった、今ある台帳の項目もそのまま残す
    for id_, old in existing.items():
        if id_ not in seen:
            items.append({**old, "group": old.get("group", "item"), "evidence": old.get("evidence", []),
                          "questions": old.get("questions", []), "origin": "existing" if id_ in case_ids else "carried",
                          "not_in_narratives": True})
    questions = top_questions([i for i in items if i["status"] != "approved"], hubs)
    return {"items": items, "questions": questions, "hubs": [h["name"] for h in hubs],
            "counts": {"existing": sum(i["origin"] == "existing" for i in items), "new": sum(i["origin"] == "new" for i in items)}}


def write_ledger(items: list[dict], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for old in directory.glob("*.yaml"):
        old.unlink()
    for it in items:
        doc = {k: it.get(k) for k in ("id", "label", "kind", "definition", "status", "owner_hint", "evidence", "questions", "used_by")}
        doc["origin"] = it.get("origin", "new")
        doc["note"] = it.get("note") if it.get("origin") == "existing" and it.get("note") else "仮説。状態の承認は人が行う（anmoku approve）"
        (directory / f"{it['id']}.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")


def load_ledger(directory: Path) -> list[dict]:
    return [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.yaml"))]


def priority(item: dict, hub_names: list[str]) -> float:
    """質問の優先度：回答待ち＞下書き、言及した人数・件数が多いほど、ハブが答えを持つほど高い。"""
    speakers = len({e["speaker"] for e in item["evidence"]})
    p = speakers * 2 + len(item["evidence"])
    if item["status"] == "needs_answer":
        p += 5
    if item.get("owner_hint") and any(h and (item["owner_hint"] in h or h in item["owner_hint"]) for h in hub_names):
        p += 6
    if item.get("group") == "implicit_rule":
        p += 1
    return p


def top_questions(items: list[dict], hubs: list[dict], n: int = 5) -> list[dict]:
    hub_names = [h["name"] for h in hubs]
    ranked = sorted(items, key=lambda it: (-priority(it, hub_names), it["id"]))
    picked: list[dict] = []
    # まず項目ごとに1問ずつ、足りなければ2問目以降
    for round_ in range(3):
        for it in ranked:
            if len(picked) >= n:
                break
            if len(it["questions"]) > round_:
                picked.append({"ledger_id": it["id"], "label": it["label"], "to": it.get("owner_hint") or "（要確認）",
                               "question": it["questions"][round_], "priority": priority(it, hub_names),
                               "evidence": it["evidence"][:2]})
    return picked[:n]


def render_questions(qs: list[dict], hubs: list[dict]) -> str:
    lines = ["# 次のヒアリングで聞く質問（優先順・5つまで）", "",
             "この5つに答えが出れば、定義台帳の「回答待ち」が埋まり、エージェント化を判断する条件がそろいます。",
             "ハブの候補は仮説です。人を評価するためではなく、役割と判断を引き継げる形にするために聞きます。", ""]
    if hubs:
        lines.append(f"ハブの候補（仮説）：{'、'.join(h['name'] for h in hubs)}")
        lines.append("")
    for i, q in enumerate(qs, 1):
        ev = "／".join(f"{e['source']}:{e['line']}「{e['quote']}」" for e in q["evidence"])
        lines += [f"{i}. **{q['question']}**", f"   - 聞く相手：{q['to']}　対象：{q['label']}（{q['ledger_id']}）", f"   - 根拠：{ev}", ""]
    return "\n".join(lines)


def approve(directory: Path, item_id: str, definition: str) -> dict:
    """人が回答を確かめたあとに、定義を承認済みにする。"""
    p = directory / f"{item_id}.yaml"
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    doc["definition"] = definition
    doc["status"] = "approved"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return doc


# ---------- 質問への答えの記録（仮説を事実へ引き上げる往復の1歩目） ----------
def answers_path(out: Path) -> Path:
    return out / "answers.json"


def load_answers(out: Path) -> list[dict]:
    p = answers_path(out)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def record_answer(out: Path, no: int, answer: str, by: str, date: str) -> dict:
    """質問5つのうち no 番目（1始まり）への答えを記録する。定義台帳とは別のファイルに残し、台帳を作り直しても消えない。

    答えを記録しても、定義は自動では承認しない。確かめた人が anmoku approve で承認する。
    """
    ledger = json.loads((out / "ledger.json").read_text(encoding="utf-8"))
    qs = ledger["questions"]
    if not 1 <= no <= len(qs):
        raise SystemExit(f"質問の番号は 1〜{len(qs)} で指定してください")
    q = qs[no - 1]
    rec = {"no": no, "ledger_id": q["ledger_id"], "question": q["question"], "answer": answer, "by": by, "date": date}
    answers = [a for a in load_answers(out) if not (a["ledger_id"] == q["ledger_id"] and a["question"] == q["question"])]
    answers.append(rec)
    answers_path(out).write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec
