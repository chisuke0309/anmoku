"""役割ごとの指示文は agents/*.md を正本にする（プラグインのサブエージェントと CLI で同じ指示文を使う）。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[2] / "agents"


@lru_cache
def agent_prompt(name: str) -> str:
    text = (AGENTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    if text.startswith("---"):
        text = text.split("---", 2)[2]
    return text.strip() + "\n"
