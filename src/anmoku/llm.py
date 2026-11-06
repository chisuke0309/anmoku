"""Claude Code のヘッドレス実行（`claude -p`）を呼ぶ薄い部品。

- 構造化出力は `--json-schema` で受け取り、呼び出し側で pydantic により再検証する
- 既定ではツールを持たせない（`--tools ""`）。評価役は読み取りツールだけ、修正役は Hook つきで書き込みを許す
- 利用者の設定・CLAUDE.md が混ざらないよう `--setting-sources ""` で起動し、作業フォルダも分ける
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_MODEL = os.environ.get("ANMOKU_MODEL", "claude-sonnet-5-5")


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    data: Any
    text: str
    cost_usd: float
    seconds: float


def claude_bin() -> str:
    path = os.environ.get("ANMOKU_CLAUDE_BIN") or shutil.which("claude")
    if not path:
        raise LLMError("claude コマンドが見つかりません（Claude Code を導入してください）")
    return path


def run_claude(
    prompt: str,
    *,
    system: str | None = None,
    schema: dict | None = None,
    tools: list[str] | None = None,
    model: str | None = None,
    cwd: str | Path | None = None,
    add_dirs: list[str | Path] | None = None,
    settings: dict | None = None,
    retries: int = 1,
    log_dir: Path | None = None,
    label: str = "call",
) -> LLMResult:
    """`claude -p` を1回実行し、構造化出力（schema 指定時）か本文を返す。"""
    args = [
        claude_bin(),
        "-p",
        "--output-format",
        "json",
        "--no-session-persistence",
        "--setting-sources",
        "",
        "--model",
        model or DEFAULT_MODEL,
        "--tools",
        ",".join(tools) if tools else "",
    ]
    if tools:
        args += ["--allowedTools", ",".join(tools), "--permission-mode", "default"]
    if system:
        args += ["--system-prompt", system]
    if schema:
        args += ["--json-schema", json.dumps(schema, ensure_ascii=False)]
    for d in add_dirs or []:
        args += ["--add-dir", str(d)]
    if settings:
        args += ["--settings", json.dumps(settings, ensure_ascii=False)]

    workdir = Path(cwd) if cwd else Path(tempfile.mkdtemp(prefix="anmoku-"))
    last_err = ""
    for attempt in range(retries + 1):
        t0 = time.time()
        proc = subprocess.run(
            args, input=prompt, capture_output=True, text=True, cwd=workdir, timeout=900
        )
        secs = time.time() - t0
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            last_err = f"JSONでない出力 (exit={proc.returncode}): {proc.stdout[:300]} {proc.stderr[:300]}"
            continue
        if out.get("is_error"):
            last_err = f"claude エラー: {str(out.get('result'))[:300]}"
            continue
        data = out.get("structured_output") if schema else out.get("result")
        if schema and data is None:
            last_err = "構造化出力が空です"
            continue
        res = LLMResult(
            data=data,
            text=out.get("result") or "",
            cost_usd=float(out.get("total_cost_usd") or 0.0),
            seconds=secs,
        )
        if log_dir:
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / f"{label}.json").write_text(
                json.dumps(
                    {"label": label, "model": model or DEFAULT_MODEL, "cost_usd": res.cost_usd,
                     "seconds": round(secs, 1), "attempt": attempt, "output": data},
                    ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )
        return res
    raise LLMError(f"{label}: {last_err}")
