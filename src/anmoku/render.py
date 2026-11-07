"""見える化画面のデータ作りと、HTML への埋め込み。

段階ごとに LLM が付ける相手のIDは変わりうるので、観察IDの重なりで前の段階の候補とつなぎ、
画面で「同じ候補の点数が跳ね上がる」「別名がまとまる」様子を追えるようにする（同じ入力なら同じ結果）。
"""

from __future__ import annotations

import json
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent / "viewer_template.html"
TOP_N = 6


def link_stages(stages: list[dict]) -> list[dict]:
    prev: list[dict] = []
    next_key = 0
    out = []
    for st in stages:
        cands = []
        for c in st["candidates"]:
            if c["category"] in ("社外", "不明", "組織全体"):
                continue
            ids = set(c["observation_ids"])
            overlaps = sorted(
                ((len(ids & set(p["observation_ids"])), p) for p in prev if ids & set(p["observation_ids"])),
                key=lambda x: (-x[0], x[1]["key"]),
            )
            if overlaps:
                key = overlaps[0][1]["key"]
                prev_score = overlaps[0][1]["score"]
                # 「新しい呼び方」は、つながる前の段階の同じ候補に無かった呼び方
                prev_aliases = set(overlaps[0][1]["aliases"])
                had_prev = True
            else:
                key, next_key = f"c{next_key}", next_key + 1
                prev_score, prev_aliases, had_prev = 0.0, set(), False
            cands.append(c | {
                "key": key,
                "had_prev": had_prev,
                "prev_score": prev_score,
                "new_aliases": [a for a in c["aliases"] if a not in prev_aliases],
                "new_evidence": [e for e in c["evidence"] if e["interview"] == st["added"]["id"]],
            })
        # 同じ key が重なったら（1つの候補が2つに割れたとき）、小さい方に新しい key を振る
        seen: set[str] = set()
        for c in cands:
            if c["key"] in seen:
                c["key"], next_key = f"c{next_key}", next_key + 1
                c["prev_score"], c["new_aliases"], c["had_prev"] = 0.0, list(c["aliases"]), False
            seen.add(c["key"])
        out.append(st | {"candidates": cands})
        prev = cands
    return out


def split_aliases(aliases: dict[str, int]) -> tuple[dict[str, int], int]:
    """呼び方を「名指しされた呼び方」と「名指しなし（不明・明示なし・言った人など）の件数」に分ける。"""
    from .score import is_unknown

    named = {a: n for a, n in aliases.items() if not is_unknown(a)}
    return named, sum(n for a, n in aliases.items() if is_unknown(a))


def viewer_data(detect: dict, title: str = "") -> dict:
    stages = link_stages(detect["stages"])
    slim = []
    for st in stages:
        for c in st["candidates"]:
            named, unnamed = split_aliases(c["aliases"])
            c["aliases"], c["unnamed"] = named, unnamed
            c["new_aliases"] = [a for a in c["new_aliases"] if a in named]
            # 「まとめた」は、前の段階からある候補に、名指しされた新しい呼び方が加わったときだけ出す
            # （名指しなしの観察が移ってきただけでは、まとめたことにしない）
            c["merged_from"] = c["new_aliases"] if c["had_prev"] else []
        slim.append({
            "k": st["k"],
            "added": st["added"],
            "candidates": [
                {k: c[k] for k in ("key", "name", "aliases", "absence", "absence_reason", "speakers", "deps",
                                   "waits", "score", "formula", "hub_hypothesis", "reason", "merged_from", "prev_score",
                                   "new_aliases", "evidence", "new_evidence", "unnamed")} | ({"critique": c["critique"]} if c.get("critique") else {})
                for c in st["candidates"][:TOP_N]
            ],
        })
    max_score = max((c["score"] for st in slim for c in st["candidates"]), default=1) or 1
    return {
        "title": title or detect["flow"]["title"],
        "lanes": detect["flow"]["lanes"],
        "interviews": detect["interviews"],
        "quotes": {"total": detect["quotes"]["total"], "kept": detect["quotes"]["kept"]},
        "max_score": max_score,
        "stages": slim,
    }


def write_viewer(detect: dict, out_path: Path, title: str = "", extra: dict | None = None) -> Path:
    data = viewer_data(detect, title) | (extra or {})
    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__ANMOKU_DATA__*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    )
    out_path.write_text(html, encoding="utf-8")
    return out_path


def _strip_provisional(s: str) -> str:
    return s.replace("（仮）", "").replace("(仮)", "").strip()


def _hits_hub(lane: str, owner: str, hub: dict) -> bool:
    """AI が足した行のレーン（または担当）が、ハブの呼び方に当たるか。
    役職名だけの呼び方（「課長」など）は、レーン名と完全に一致するときだけ当たりとする（「山本課長」に当てないため）。"""
    from .score import GENERIC_TITLES, HONORIFICS

    if hub["name"] and hub["name"] in owner:
        return True
    for a in hub["aliases"]:
        stem = HONORIFICS.sub("", a).strip()
        if not stem:
            continue
        if lane == a or lane == stem:
            return True
        if stem not in GENERIC_TITLES and stem in lane:
            return True
    return False


def tidy_lanes(after: list[dict], before: list[dict], hubs: list[dict]) -> list[dict]:
    """画面②のレーンを整える（表示だけ。flow_v2.md は変えない）。

    - 公式フローにある行は変えない
    - AI が足した行で、レーンが今あるレーン名に（仮）が付いただけなら、今あるレーンに入れる（行の「仮」の表示は残す）
    - それ以外で、レーンや担当がハブの呼び方に当たるなら、ハブの表示名の1レーンにまとめる
    """
    official = {(r["lane"], r["task"]) for r in before}
    lanes = {r["lane"] for r in before}
    out = []
    for r in after:
        if (r["lane"], r["task"]) in official:
            out.append(r)
            continue
        lane = _strip_provisional(r["lane"])
        if lane in lanes:
            out.append(r | {"lane": lane, "provisional": True})
            continue
        hub = next((h for h in hubs if _hits_hub(lane, r.get("owner", ""), h)), None)
        out.append(r | {"lane": f"{hub['name']}（仮）", "provisional": True} if hub else r)
    return out


def _read(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def write_dashboard(out: Path, case: Path | None = None) -> Path:
    """anmoku-out/ にある結果をすべて集めて、1枚の画面（index.html）にする。"""
    import yaml

    detect = _read(out / "detect.json")
    if not detect:
        raise SystemExit("detect.json がありません。先に anmoku detect を実行してください")
    ledger = _read(out / "ledger.json")
    if ledger and (out / "ledger").exists():
        # 承認（anmoku approve）を反映するため、状態は YAML の現在値を使う
        current = {d["id"]: d for d in (yaml.safe_load(p.read_text(encoding="utf-8")) for p in (out / "ledger").glob("*.yaml"))}
        for it in ledger["items"]:
            if it["id"] in current:
                it["status"] = current[it["id"]]["status"]
                it["definition"] = current[it["id"]].get("definition")
                it["used_by"] = current[it["id"]].get("used_by", [])
    fix = _read(out / "fix.json")
    reviews = [_read(p) for p in sorted(out.glob("review_*.json"))]
    flows = {}
    if fix:
        from .flow import load_flow

        case_flow = out / "flow_v2.md"
        base_path = next((p for p in ((case / "flow.md") if case else None, out.parent / "flow.md") if p and p.exists()), None)
        if base_path and case_flow.exists():
            before = [r.__dict__ for r in load_flow(base_path).rows]
            hubs = [{"name": c["name"], "aliases": list(split_aliases(c["aliases"])[0])}
                    for c in detect["stages"][-1]["candidates"] if c.get("hub_hypothesis")]
            flows = {"before": before,
                     "after": tidy_lanes([r.__dict__ | {"provisional": r.provisional} for r in load_flow(case_flow).rows], before, hubs)}
    # ヒアリング記録の全文（画面で、人を選ぶと読めるようにする）
    iv_dir = next((d for d in ((case / "interviews") if case else None, out.parent / "interviews") if d and d.exists()), None)
    transcripts = {}
    if iv_dir:
        for iv in detect["interviews"]:
            f = iv_dir / f"{iv['id']}.md"
            if f.exists():
                transcripts[iv["id"]] = f.read_text(encoding="utf-8").splitlines()
    from .flow import load_flow
    from .ledger import load_answers, load_existing

    # 入力として読み込んだもの：今ある業務フローと、今ある定義台帳
    base_flow = (case / "flow.md") if case else out.parent / "flow.md"
    inputs = {
        "flow": [r.__dict__ for r in load_flow(base_flow).rows] if base_flow.exists() else [],
        "flow_file": base_flow.name,
        "ledger": list(load_existing((case / "ledger") if case else out.parent / "ledger").values()),
    }
    extra = {
        "inputs": inputs,
        "answers": load_answers(out),
        "transcripts": transcripts,
        "flows": flows,
        "ledger": ledger,
        "fix": {k: fix[k] for k in ("status", "rounds", "added_rows", "mermaid_before", "mermaid_after", "all_added_provisional")} if fix else None,
        "reviews": reviews,
    }
    return write_viewer(detect, out / "index.html", extra=extra)
