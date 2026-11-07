"""反証役：ハブの仮説ごとに「ハブではない理由」を探させ、根拠を原文と照合して反映する。

強い反証（根拠が実在するもの）があれば、その候補は「ハブの仮説」から外し、理由を残す。
"""

from __future__ import annotations

from pathlib import Path

from .evidence import check_quote, norm
from .fixflow import guard_settings
from .interviews import Interview
from .llm import run_claude
from .prompts import agent_prompt
from .schema import Critique

CRITIC_SYSTEM = agent_prompt("hub-critic")  # 正本：agents/hub-critic.md


def critique(cand: dict, ivs: list[Interview], flow_path: Path, out: Path, *, model: str | None, label: str) -> dict:
    ivdir = ivs[0].path.parent.resolve()
    ev = "\n".join(f"- {e['interview']}.md:{e['line']}「{e['quote']}」（呼び方：{e['target']}／{e['kind']}）" for e in cand["evidence"])
    prompt = f"""# ハブの仮説
{cand['name']}（呼び方：{'・'.join(cand['aliases'])}）
点数：{cand['formula']}
同じ相手とした理由：{cand.get('reason', '')}

# 根拠にした引用
{ev}

# 読めるファイル
- 業務フロー：{flow_path.resolve()}
- 語り（フォルダ）：{ivdir}（{', '.join(iv.id + '.md' for iv in ivs)}）
"""
    res = run_claude(prompt, system=CRITIC_SYSTEM, tools=["Read", "Grep", "Glob"], schema=Critique.model_json_schema(),
                     model=model, cwd=out, add_dirs=[ivdir, flow_path.resolve().parent], settings=guard_settings(),
                     env={"ANMOKU_ROLE": "evaluator"}, log_dir=out / "llm", label=label)
    c = Critique.model_validate(res.data)
    by_id = {iv.id: iv for iv in ivs}
    flow_text = norm(flow_path.read_text(encoding="utf-8"))
    kept = []
    for r in c.counter_reasons:
        ok = False
        if r.file and r.quote and r.line:
            iv = by_id.get(r.file.removesuffix(".md"))
            ok = bool(iv) and check_quote(iv, r.quote, r.line).ok
        elif r.quote:
            ok = len(norm(r.quote)) >= 2 and norm(r.quote) in flow_text
        if ok:
            kept.append(r.model_dump())
    strength = c.strength if kept else "なし"
    withdrawn = strength == "強い" and not c.still_hub
    return {"strength": strength, "still_hub": not withdrawn, "reasons": kept,
            "dropped": len(c.counter_reasons) - len(kept), "cost_usd": res.cost_usd}
