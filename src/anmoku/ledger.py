"""物差し：定義台帳（1つの用語・判断基準を1ファイル）と、ハブ宛ての質問5つ。

LLM は語りに出た用語の観察をまとめ、定義の案と質問を出すだけ。
状態（下書き／回答待ち／承認済み）と質問の優先順位は Python が決める。承認は人だけが行う。
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from .llm import run_claude
from .prompts import agent_prompt
from .schema import LedgerOut, json_schema

LEDGER_SYSTEM = agent_prompt("ledger-steward")  # 正本：agents/ledger-steward.md


def ledger_prompt(terms: list[dict], hubs: list[dict], flow_lanes: list[str]) -> str:
    t = "\n".join(
        f"{x['id']} | 話し手:{x['speaker']} | {x['kind']} | {x['label']} | 知っている人:{x.get('holder') or '-'} | 言葉になっている:{x['defined']} | 「{x['quote']}」"
        for x in terms
    )
    h = "\n".join(f"- {c['name']}（呼び方：{'・'.join(c['aliases'])}／点数 {c['score']}）" for c in hubs)
    return f"""# 公式フローのレーン
{'、'.join(flow_lanes)}

# ハブの候補（仮説）
{h}

# 用語・判断基準の観察（ID | 話し手 | 種類 | 呼び方 | 知っている人 | 言葉になっているか | 引用）
{t}
"""


def build_ledger(detect: dict, out_dir: Path, *, model: str | None = None) -> dict:
    final = detect["stages"][-1]["candidates"]
    hubs = [c for c in final if c.get("hub_hypothesis")][:3]
    terms = detect["terms"]
    res = run_claude(
        ledger_prompt(terms, hubs, detect["flow"]["lanes"]),
        system=LEDGER_SYSTEM,
        schema=json_schema(LedgerOut),
        model=model,
        log_dir=out_dir / "llm",
        label="ledger",
    )
    proposal = LedgerOut.model_validate(res.data)
    by_id = {t["id"]: t for t in terms}
    items = []
    for kind_group, plist in (("item", proposal.items), ("implicit_rule", proposal.implicit_rules)):
        for p in plist:
            ev = [by_id[r] for r in p.evidence_refs if r in by_id]
            if not ev:
                continue  # 根拠が実在しない項目は捨てる
            # 状態は Python が決める：語りに言葉になった根拠があり、定義案があるときだけ下書き
            status = "draft" if p.definition and any(e["defined"] for e in ev) else "needs_answer"
            items.append({
                "id": p.id,
                "label": p.label,
                "kind": p.kind,
                "group": kind_group,
                "definition": p.definition if status == "draft" else None,
                "definition_hint": None if status == "draft" else p.definition,
                "status": status,
                "owner_hint": p.owner_hint,
                "evidence": [{"source": f"interviews/{e['interview']}.md", "line": e["line"], "quote": e["quote"], "speaker": e["speaker"]} for e in ev],
                "questions": p.questions[:3],
                "used_by": [],
            })
    write_ledger(items, out_dir / "ledger")
    questions = top_questions(items, hubs)
    (out_dir / "questions.md").write_text(render_questions(questions, hubs), encoding="utf-8")
    result = {"items": items, "questions": questions, "hubs": [h["name"] for h in hubs], "cost_usd": res.cost_usd}
    (out_dir / "ledger.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def write_ledger(items: list[dict], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for old in directory.glob("*.yaml"):
        old.unlink()
    for it in items:
        doc = {k: it[k] for k in ("id", "label", "kind", "definition", "status", "owner_hint", "evidence", "questions", "used_by")}
        doc["note"] = "仮説。状態の承認は人が行う（anmoku approve）"
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
