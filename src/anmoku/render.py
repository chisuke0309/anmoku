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
                merged_from = [p["name"] for _, p in overlaps[1:]]
                prev_score = overlaps[0][1]["score"]
                prev_aliases = set().union(*[set(p["aliases"]) for _, p in overlaps])
            else:
                key, next_key = f"c{next_key}", next_key + 1
                merged_from, prev_score, prev_aliases = [], 0.0, set()
            cands.append(c | {
                "key": key,
                "merged_from": merged_from,
                "prev_score": prev_score,
                "new_aliases": [a for a in c["aliases"] if a not in prev_aliases],
                "new_evidence": [e for e in c["evidence"] if e["interview"] == st["added"]["id"]],
            })
        # 同じ key が重なったら（1つの候補が2つに割れたとき）、小さい方に新しい key を振る
        seen: set[str] = set()
        for c in cands:
            if c["key"] in seen:
                c["key"], next_key = f"c{next_key}", next_key + 1
                c["prev_score"], c["new_aliases"] = 0.0, list(c["aliases"])
            seen.add(c["key"])
        out.append(st | {"candidates": cands})
        prev = cands
    return out


def viewer_data(detect: dict, title: str = "") -> dict:
    stages = link_stages(detect["stages"])
    slim = []
    for st in stages:
        slim.append({
            "k": st["k"],
            "added": st["added"],
            "candidates": [
                {k: c[k] for k in ("key", "name", "aliases", "absence", "absence_reason", "speakers", "deps",
                                   "waits", "score", "formula", "hub_hypothesis", "reason", "merged_from", "prev_score",
                                   "new_aliases", "evidence", "new_evidence")}
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
