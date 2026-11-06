"""引用の実在チェック。LLM の引用が原文に無ければ捨てる（言い換え・捏造を機械的に止める）。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .interviews import Interview

_DROP = re.compile(r"[\s　「」『』、。,.!！?？…・ー〜~\-]")


def norm(s: str) -> str:
    return _DROP.sub("", unicodedata.normalize("NFKC", s))


@dataclass
class Check:
    ok: bool
    line: int          # 正しい行番号（行がずれていたら直す）
    reason: str = ""


def check_quote(iv: Interview, quote: str, line: int) -> Check:
    q = norm(quote)
    if len(q) < 4:
        return Check(False, line, "引用が短すぎる")
    if q in norm(iv.line(line)):
        return Check(True, line)
    # 行番号のずれは近い行から探して直す
    for n in sorted(range(1, len(iv.lines) + 1), key=lambda n: abs(n - line)):
        if q in norm(iv.lines[n - 1]):
            return Check(True, n, f"行番号を {line}→{n} に修正")
    return Check(False, line, "原文に無い引用")
