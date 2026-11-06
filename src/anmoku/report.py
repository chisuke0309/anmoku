"""結果の文字表示とレポート。すべて「仮説」として出す。"""

from __future__ import annotations

from typing import TextIO


def print_detect_summary(res: dict, f: TextIO) -> None:
    q = res["quotes"]
    f.write(f"引用の実在：{q['kept']}/{q['total']}（原文に無い引用は捨てた）\n")
    for st in res["stages"]:
        f.write(f"\n== {st['k']}人目まで（＋{st['added']['title']}）==\n")
        for c in [c for c in st["candidates"] if c["category"] not in ("社外", "不明", "組織全体")][:5]:
            alias = "・".join(f"{a}×{n}" for a, n in c["aliases"].items())
            f.write(f"  {'[ハブの仮説]' if c.get('hub_hypothesis') else '[候補]      '} {c['score']:>5}  {c['name']}  {{{alias}}}  {c['formula']}  ／{c['absence_reason']}\n")
    f.write(f"\n費用の目安：${res['cost_usd']}\n")
