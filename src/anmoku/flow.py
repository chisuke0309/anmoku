"""業務フロー（Markdown表）の読み書きと、Mermaid図への変換。

フローの形式（1行＝1タスク、上から順に流れる）:

| レーン | 担当 | タスク | 備考 |
|---|---|---|---|
| 営業 | 営業担当 | 見積作成 | |
| 工事統括（仮） | 佐藤課長（仮） | 値引きの事前相談（仮・要確認） | 根拠: 01:12 |

「仮」を含むレーン・タスクは、まだ確かめていない追加として扱う。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

PROVISIONAL = "仮"


@dataclass
class FlowRow:
    lane: str
    owner: str
    task: str
    note: str = ""

    @property
    def provisional(self) -> bool:
        return any(PROVISIONAL in s for s in (self.lane, self.owner, self.task))


@dataclass
class Flow:
    title: str
    rows: list[FlowRow] = field(default_factory=list)

    @property
    def lanes(self) -> list[str]:
        seen: list[str] = []
        for r in self.rows:
            if r.lane not in seen:
                seen.append(r.lane)
        return seen

    def owners(self) -> list[str]:
        seen: list[str] = []
        for r in self.rows:
            if r.owner not in seen:
                seen.append(r.owner)
        return seen


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def parse_flow(text: str) -> Flow:
    title = ""
    rows: list[FlowRow] = []
    header: list[str] | None = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# ") and not title:
            title = s[2:].strip()
            continue
        if not s.startswith("|"):
            header = None if not s else header
            continue
        cells = _cells(s)
        if header is None:
            header = cells
            continue
        if all(re.fullmatch(r":?-{2,}:?", c or "--") for c in cells):
            continue
        rec = dict(zip(header, cells + [""] * (len(header) - len(cells))))
        lane = rec.get("レーン", "")
        task = rec.get("タスク", "")
        if not lane and not task:
            continue
        rows.append(FlowRow(lane=lane, owner=rec.get("担当", ""), task=task, note=rec.get("備考", "")))
    return Flow(title=title, rows=rows)


def load_flow(path: str | Path) -> Flow:
    return parse_flow(Path(path).read_text(encoding="utf-8"))


def flow_from_spec(spec_flow: dict) -> Flow:
    rows = [
        FlowRow(lane=l["lane"], owner=l["owner"], task=t)
        for l in spec_flow["lanes"]
        for t in l["tasks"]
    ]
    return Flow(title=spec_flow["title"], rows=rows)


def to_markdown(flow: Flow) -> str:
    out = [f"# {flow.title}", "", "| レーン | 担当 | タスク | 備考 |", "|---|---|---|---|"]
    for r in flow.rows:
        out.append(f"| {r.lane} | {r.owner} | {r.task} | {r.note} |")
    return "\n".join(out) + "\n"


def to_mermaid(flow: Flow, base: Flow | None = None) -> str:
    """レーンごとの subgraph に並べた Mermaid 図。base を渡すと、base に無い行を色分けする。"""
    base_keys = {(r.lane, r.task) for r in base.rows} if base else None
    lines = ["flowchart LR"]
    ids: list[str] = []
    for li, lane in enumerate(flow.lanes):
        lines.append(f'  subgraph L{li}["{lane}"]')
        lines.append("    direction TB")
        for ri, r in enumerate(flow.rows):
            if r.lane != lane:
                continue
            nid = f"T{ri}"
            lines.append(f'    {nid}["{r.task}"]')
        lines.append("  end")
    for ri in range(len(flow.rows)):
        ids.append(f"T{ri}")
    # 上から順の流れ
    for a, b in zip(ids, ids[1:]):
        lines.append(f"  {a} --> {b}")
    lines.append("  classDef prov fill:#fff3c4,stroke:#c98a00,stroke-dasharray: 5 3,color:#5a3d00")
    lines.append("  classDef added fill:#ffe0d6,stroke:#c0392b,color:#5a1a10")
    for ri, r in enumerate(flow.rows):
        if base_keys is not None and (r.lane, r.task) not in base_keys:
            lines.append(f"  class T{ri} {'prov' if r.provisional else 'added'}")
        elif r.provisional:
            lines.append(f"  class T{ri} prov")
    return "\n".join(lines) + "\n"
