"""別名の統合。LLM が「どの観察が同じ相手か」を根拠つきで提案し、Python が反映・検証する。

「課長」のように同じ呼び方でも別人（工事の課長と経理の課長）がありうるので、
呼び方の文字列ではなく、観察1件ずつを相手に割り当てる。
"""

from __future__ import annotations

import re
from pathlib import Path

from .flow import Flow
from .llm import run_claude
from .schema import Resolution, json_schema

RESOLVE_SYSTEM = """あなたは、複数人のヒアリングに出てくる「相手」の呼び方を整理する役です。
観察の一覧を読み、同じ相手を指す観察をまとめてください。

# 決まり
- 呼び方が違っても、所属・役割・話題・行動（例：日中は現場を回っている、工事の中身を決める）が一致すれば同じ相手としてよい。根拠を reason に書く
- 同じ呼び方（例：「課長」）でも、所属や話題が違えば別の相手に分ける。迷ったら分ける
- 公式フローのレーン一覧を見て、その相手が担当しているレーンがあれば flow_lane にレーン名を一覧から完全一致で書く。
  レーンは無いがタスク名にだけ出てくる場合は flow_task に書く。どこにも無ければ両方 null。推測で当てはめない
- category：社内の人・役割／社外（お客さん・協力会社など）／物・仕組み（台帳・システムなど）
- 1つの相手は1人の人か1つの役割・仕組みにする。「工務と店長」のように複数の相手を合わせた相手は作らない。
  1つの観察が複数の相手を指すときは、主に判断している方に割り当てる
- 会社全体・部署全体の漠然とした呼び方（「うち」「会社」「元請けさん」「事務所」など、特定の人や役割を指さないもの）は category を「物・仕組み」にせず「社外」にもせず、
  display_name の先頭に「（組織全体）」と付ける
- 相手が名指しされていない観察（「不明」「明示なし」など）は、手がかりから同じ相手と言えるときだけ、その相手に割り当てる
- すべての観察IDを、ちょうど1つの相手に割り当てる
"""


def resolve_prompt(observations: list[dict], speakers: dict[str, str], flow: Flow) -> str:
    lanes = "\n".join(
        f"- レーン「{lane}」 担当：{next(r.owner for r in flow.rows if r.lane == lane)} ／ タスク："
        + "、".join(r.task for r in flow.rows if r.lane == lane)
        for lane in flow.lanes
    )
    who = "\n".join(f"- {k}：{v}" for k, v in speakers.items())
    obs = "\n".join(
        f"{o['id']} | 話し手:{o['speaker']} | 相手:{o['target']} | 手がかり:{o.get('target_hint') or '-'} | {o['kind']} | {o['topic']} | 「{o['quote']}」"
        for o in observations
    )
    return f"""# 公式フローのレーン
{lanes}

# 話し手
{who}

# 観察（ID | 話し手 | 相手の呼び方 | 手がかり | 種類 | 話題 | 引用）
{obs}
"""


def resolve(observations: list[dict], speakers: dict[str, str], flow: Flow, *,
            model: str | None, log_dir: Path | None, label: str) -> tuple[list[dict], float]:
    if not observations:
        return [], 0.0
    res = run_claude(
        resolve_prompt(observations, speakers, flow),
        system=RESOLVE_SYSTEM,
        schema=json_schema(Resolution),
        model=model,
        log_dir=log_dir,
        label=label,
    )
    return apply(Resolution.model_validate(res.data), observations, flow), res.cost_usd


def apply(resolution: Resolution, observations: list[dict], flow: Flow) -> list[dict]:
    """提案を検証して反映する。存在しない観察IDやレーン名は捨て、割り当て漏れは呼び方ごとに補う。"""
    by_id = {o["id"]: o for o in observations}
    assigned: set[str] = set()
    entities: list[dict] = []
    lanes = set(flow.lanes)
    tasks = [r.task for r in flow.rows]
    used_ids: set[str] = set()
    for p in resolution.entities:
        members = [i for i in p.observation_ids if i in by_id and i not in assigned]
        if not members:
            continue
        assigned.update(members)
        eid = p.entity_id
        while eid in used_ids:
            eid += "-x"
        used_ids.add(eid)
        entities.append({
            "id": eid,
            "name": p.display_name,
            "category": p.category,
            "reason": p.reason,
            "flow_lane": p.flow_lane if p.flow_lane in lanes else None,
            "flow_task": p.flow_task if p.flow_task and any(p.flow_task in t or t in p.flow_task for t in tasks) else None,
            "observation_ids": members,
        })
    # 割り当て漏れ：呼び方ごとに1つの相手として補う（統合はしない）
    leftovers: dict[str, list[str]] = {}
    for o in observations:
        if o["id"] not in assigned:
            leftovers.setdefault(o["target"], []).append(o["id"])
    for target, ids in leftovers.items():
        eid = "unresolved-" + re.sub(r"\W+", "-", target)
        entities.append({"id": eid, "name": target, "category": "社内の人・役割", "reason": "統合の提案なし",
                         "flow_lane": None, "flow_task": None, "observation_ids": ids})
    return entities
