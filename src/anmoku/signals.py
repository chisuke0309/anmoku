"""日本語の手がかり語による、網を広く張った候補拾い（ルール側）。

LLM の観察と突き合わせ、片方にしか出ないものを「要確認」にする。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .interviews import Interview

CUES: dict[str, str] = {
    "に聞": "確認", "に確認": "確認", "に相談": "相談", "に一声": "相談", "に投げ": "確認",
    "に回": "確認", "に上げ": "確認", "の承認": "承認", "承認待ち": "待ち", "待ち": "待ち",
    "待た": "待ち", "返事": "待ち", "次第": "判断", "を通さ": "承認", "通してから": "承認",
    "しか分から": "判断", "じゃないと決め": "判断", "じゃないと分から": "判断", "決めてもら": "判断",
    "がいないと": "判断", "結局": "判断", "に聞かないと": "確認", "に言われ": "判断",
    "の判断": "判断", "に決め": "判断", "ＯＫ": "承認", "OK": "承認", "オッケー": "承認",
}
_PATTERN = re.compile("|".join(re.escape(k) for k in sorted(CUES, key=len, reverse=True)))


@dataclass
class CueHit:
    interview: str
    line: int
    cue: str
    kind: str
    text: str


def scan(iv: Interview) -> list[CueHit]:
    hits: list[CueHit] = []
    for n, l in enumerate(iv.lines, start=1):
        if l.startswith("聞き手"):
            continue
        for m in _PATTERN.finditer(l):
            hits.append(CueHit(iv.id, n, m.group(0), CUES[m.group(0)], l))
    return hits
