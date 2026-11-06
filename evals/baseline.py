"""ベースライン：「Claude に全文を貼って1回聞くだけ」の場合。

ツールと同じモデル・同じ入力（k人目までの語り＋公式フロー）で、1回のプロンプトで候補を出させる。
比べやすいよう出力の形だけ JSON にそろえ、引用はツールと同じ方法で原文と照合する。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anmoku.evidence import check_quote  # noqa: E402
from anmoku.interviews import Interview  # noqa: E402
from anmoku.llm import run_claude  # noqa: E402

BASELINE_PROMPT = """以下は、ある会社の業務ヒアリングの記録と、公式の業務フローです。
業務フローに役割として載っていないのに、問い合わせや判断が集まっていて、まわりが返事を待っている人はいますか。
いれば、可能性の高い順に最大3人挙げてください。人によって呼び方が違う場合は、同じ人としてまとめてください。
それぞれ、根拠となる発言を、ファイル名と行番号つきで原文から引用してください。いなければ空の一覧を返してください。

# 公式の業務フロー
{flow}

# ヒアリング記録
{interviews}
"""


class Quote(BaseModel):
    file: str
    line: int
    quote: str


class Candidate(BaseModel):
    name: str
    aliases: list[str]
    reason: str
    quotes: list[Quote]


class BaselineOut(BaseModel):
    candidates: list[Candidate]
    note: Optional[str] = None


# 貼るだけ（一般）：業務改善の担当者がふだんやる聞き方。何を探すかは指定しない
GENERAL_PROMPT = """以下は、ある会社の業務ヒアリングの記録と、公式の業務フローです。
業務改善の観点で、課題を重要な順に最大10件挙げてください。
それぞれ、関係する人（呼び方のまま）と、根拠となる発言をファイル名と行番号つきで原文から引用してください。

# 公式の業務フロー
{flow}

# ヒアリング記録
{interviews}
"""


class Issue(BaseModel):
    title: str
    detail: str
    people: list[str]
    quotes: list[Quote]


class GeneralOut(BaseModel):
    issues: list[Issue]


def run_baseline(ivs: list[Interview], flow_text: str, *, model: str | None, log_dir: Path | None, label: str,
                 mode: str = "directed") -> dict:
    """mode: directed＝ツールと同じ問いを直接聞く／general＝「課題を洗い出して」と聞く"""
    body = "\n\n".join(f"## ファイル：{iv.id}.md\n" + "\n".join(f"{i}: {l}" for i, l in enumerate(iv.lines, 1)) for iv in ivs)
    prompt, model_cls = (BASELINE_PROMPT, BaselineOut) if mode == "directed" else (GENERAL_PROMPT, GeneralOut)
    res = run_claude(
        prompt.format(flow=flow_text, interviews=body),
        schema=model_cls.model_json_schema(),
        model=model,
        log_dir=log_dir,
        label=label,
    )
    out = model_cls.model_validate(res.data)
    if mode == "general":
        # 課題を「候補」と同じ形にそろえる：名前＝課題名＋詳細、別名＝関係する人
        out = BaselineOut(candidates=[
            Candidate(name=f"{i.title}｜{i.detail}", aliases=i.people, reason=i.detail, quotes=i.quotes) for i in out.issues
        ])
    by_id = {iv.id: iv for iv in ivs}
    total = kept = 0
    cands = []
    for c in out.candidates:
        qs = []
        for q in c.quotes:
            total += 1
            iv = by_id.get(q.file.removesuffix(".md"))
            ok = bool(iv) and check_quote(iv, q.quote, q.line).ok
            kept += ok
            qs.append(q.model_dump() | {"ok": ok})
        cands.append(c.model_dump() | {"quotes": qs})
    return {"candidates": cands, "quotes": {"total": total, "kept": kept}, "cost_usd": res.cost_usd}
