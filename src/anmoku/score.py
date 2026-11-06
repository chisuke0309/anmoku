"""点数（説明できる式）。同じ入力なら同じ結果になる。

点数 = フローに無い度合い ×（言及した人数×2 ＋ 依存の件数 ＋ 待ちの件数×1.5）

- フローに無い度合い：レーンがある＝0、タスク名にだけ出てくる＝0.5、どこにも無い＝1
- 依存：確認・相談・承認・判断・差し戻しの観察
- 待ち：種類が「待ち」か、待ちの表現がある観察
"""

from __future__ import annotations

import re

from .flow import Flow
from .schema import DEPENDENCY_KINDS

# 役職名だけの呼び方は、フローの担当欄との文字列照合に使わない（別人の「課長」に当たってしまうため）
GENERIC_TITLES = {"課長", "係長", "部長", "主任", "社長", "専務", "上", "上長", "あの人", "リーダー"}
HONORIFICS = re.compile(r"(さん|様|くん|ちゃん)$")

W_SPEAKERS, W_DEPS, W_WAITS = 2.0, 1.0, 1.5

# 相手が名指しされていない呼び方（順位からは外し、統合先の根拠としてだけ使う）
UNKNOWN = re.compile(r"^[（(]?(不明|明示なし|なし|誰か|不特定)")
EXCLUDED = {"社外", "不明", "組織全体"}


def is_unknown(alias: str) -> bool:
    return bool(UNKNOWN.match(alias.strip()))


def absence(entity: dict, aliases: list[str], flow: Flow) -> tuple[float, str]:
    if entity.get("flow_lane"):
        return 0.0, f"レーン「{entity['flow_lane']}」がある"
    for a in aliases:
        name = HONORIFICS.sub("", a.strip())
        if not name or name in GENERIC_TITLES:
            continue
        if name in flow.lanes:
            return 0.0, f"レーン「{name}」がある"
        for lane in flow.lanes:
            owner = next(r.owner for r in flow.rows if r.lane == lane)
            if len(name) >= 2 and name in owner:
                return 0.0, f"レーン「{lane}」の担当（{owner}）"
    if entity.get("flow_task"):
        return 0.5, f"タスク「{entity['flow_task']}」にだけ出てくる"
    return 1.0, "フローのどこにも無い"


def metrics(obs: list[dict]) -> dict:
    speakers = sorted({o["interview"] for o in obs})
    deps = sum(1 for o in obs if o["kind"] in DEPENDENCY_KINDS)
    waits = sum(1 for o in obs if o["kind"] == "待ち" or o.get("wait"))
    return {"speakers": len(speakers), "speaker_ids": speakers, "deps": deps, "waits": waits}


def score(absence_value: float, m: dict) -> float:
    return round(absence_value * (m["speakers"] * W_SPEAKERS + m["deps"] * W_DEPS + m["waits"] * W_WAITS), 2)


def rank(entities: list[dict], observations: list[dict], flow: Flow) -> list[dict]:
    """相手ごとに点数と内訳を出し、点数順に並べる。社外は順位から外す。"""
    by_id = {o["id"]: o for o in observations}
    out = []
    for e in entities:
        obs = [by_id[i] for i in e["observation_ids"] if i in by_id]
        aliases: dict[str, int] = {}
        for o in obs:
            aliases[o["target"]] = aliases.get(o["target"], 0) + 1
        a_val, a_reason = absence(e, list(aliases), flow)
        m = metrics(obs)
        category = e["category"]
        if aliases and all(is_unknown(a) for a in aliases):
            category = "不明"
        elif e["name"].startswith("（組織全体）"):
            category = "組織全体"
        s = score(a_val, m) if category not in EXCLUDED else 0.0
        out.append(e | {
            "category": category,
            "aliases": aliases,
            "absence": a_val,
            "absence_reason": a_reason,
            **m,
            "score": s,
            "formula": f"{a_val} ×（{m['speakers']}人×2 ＋ 依存{m['deps']} ＋ 待ち{m['waits']}×1.5）＝ {s}",
            "evidence": [
                {"interview": o["interview"], "line": o["line"], "quote": o["quote"], "kind": o["kind"], "target": o["target"]}
                for o in obs
            ],
        })
    out.sort(key=lambda x: (-x["score"], x["category"] in EXCLUDED, -len(x["evidence"]), x["id"]))
    return out
