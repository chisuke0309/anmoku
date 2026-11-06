"""ヒアリング記録（Markdown）の読み込み。行番号は1始まりで、引用の根拠に使う。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Interview:
    id: str          # ファイル名（拡張子なし）。順番は名前順
    path: Path
    title: str
    speaker: str     # 話し手の名前（見出しから推定）
    lines: list[str]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def numbered(self) -> str:
        return "\n".join(f"{i:04d}| {l}" for i, l in enumerate(self.lines, start=1))

    def line(self, n: int) -> str:
        return self.lines[n - 1] if 1 <= n <= len(self.lines) else ""


def load_interview(path: Path) -> Interview:
    lines = path.read_text(encoding="utf-8").splitlines()
    title = next((l[2:].strip() for l in lines if l.startswith("# ")), path.stem)
    m = re.search(r"([^\s　]+)さん\s*$", title)
    speaker = m.group(1) if m else title
    return Interview(id=path.stem, path=path, title=title, speaker=speaker, lines=lines)


def load_interviews(directory: str | Path) -> list[Interview]:
    return [load_interview(p) for p in sorted(Path(directory).glob("*.md"))]
