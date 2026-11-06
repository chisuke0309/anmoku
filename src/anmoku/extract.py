"""語り1人分から、引用つきの観察を取り出す（LLM）。人ごとに別の呼び出しにし、文脈を混ぜない。"""

from __future__ import annotations

from pathlib import Path

from .evidence import check_quote
from .interviews import Interview
from .llm import run_claude
from .schema import Extraction, json_schema

EXTRACT_SYSTEM = """あなたは業務ヒアリングの記録を読む観察役です。判断や評価はせず、原文に書かれていることだけを構造化します。

# 取り出すもの
1. observations：話し手が、ほかの人・役割・部署・仕組み（台帳など）に対して
   確認する・相談する・承認をもらう・判断してもらう・返事を待つ・差し戻される・連絡を受ける、という発言。
   - 1つの発言に複数の相手がいれば、相手ごとに分ける
   - target は原文の呼び方そのまま（「課長」「佐藤さん」「店長」など）。推測で名前を補わない
   - target_hint には、その相手が誰かを見分ける手がかり（「経理の」「現場を回っている」「工事の中身を決める」など）を原文から短く書く
   - 聞き手の発言からは取り出さない。お客さん（施主）や職人さんとのやりとりも、社内の確認・判断に関係するものだけにする
2. terms：その職場で使われているが、中身（基準・範囲・金額）がはっきりしない用語・判断基準・指標。
   例：「難しい物件」「優先」「なんとなくの目安」「小さい追加」。言葉になっている基準（例：○万円を超えたら）も取り出し、defined を true にする

# 引用の決まり
- quote は、指定した line の行の中にある連続した文字列を、一字一句そのまま写す（10〜60字程度）。言い換え・要約・つなぎ合わせは禁止
- 行番号は各行の先頭の数字（例：0012| → 12）
"""


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
