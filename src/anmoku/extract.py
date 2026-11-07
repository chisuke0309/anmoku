"""語り1人分から、引用つきの観察を取り出す（LLM）。人ごとに別の呼び出しにし、文脈を混ぜない。"""

from __future__ import annotations

from pathlib import Path

from .evidence import check_quote
from .interviews import Interview
from .llm import run_claude
from .prompts import agent_prompt
from .schema import Extraction, json_schema

EXTRACT_SYSTEM = agent_prompt("narrative-extractor")  # 正本：agents/narrative-extractor.md


def extract_prompt(iv: Interview) -> str:
    return f"""# ヒアリング記録（話し手：{iv.speaker}）
{iv.numbered()}
"""


def extract_one(iv: Interview, *, model: str | None, log_dir: Path | None) -> dict:
    res = run_claude(
        extract_prompt(iv),
        system=EXTRACT_SYSTEM,
        schema=json_schema(Extraction),
        model=model,
        log_dir=log_dir,
        label=f"extract-{iv.id}",
    )
    ex = Extraction.model_validate(res.data)
    return verify(iv, ex) | {"cost_usd": res.cost_usd}


def verify(iv: Interview, ex: Extraction) -> dict:
    """引用の実在チェック。原文に無い引用は捨て、捨てた数を記録する。"""
    kept_obs, kept_terms, dropped = [], [], []
    for i, o in enumerate(ex.observations, start=1):
        c = check_quote(iv, o.quote, o.line)
        rec = o.model_dump() | {"id": f"{iv.id}#o{i}", "interview": iv.id, "speaker": iv.speaker, "line": c.line}
        (kept_obs if c.ok else dropped).append(rec | ({"fix": c.reason} if c.reason and c.ok else {"drop": c.reason} if not c.ok else {}))
    for i, t in enumerate(ex.terms, start=1):
        c = check_quote(iv, t.quote, t.line)
        rec = t.model_dump() | {"id": f"{iv.id}#t{i}", "interview": iv.id, "speaker": iv.speaker, "line": c.line}
        (kept_terms if c.ok else dropped).append(rec | ({} if c.ok else {"drop": c.reason}))
    total = len(ex.observations) + len(ex.terms)
    return {
        "interview": iv.id,
        "speaker": iv.speaker,
        "observations": kept_obs,
        "terms": kept_terms,
        "dropped": dropped,
        "quote_total": total,
        "quote_kept": total - len(dropped),
    }
