#!/usr/bin/env python3
"""PreToolUse フック：役割ごとに書き込みを機械的に止める（標準ライブラリだけで動く）。

- 評価役（flow-evaluator / design-evaluator / hub-critic、または ANMOKU_ROLE=evaluator）：書き込みはすべて禁止
- 修正役（flow-fixer、または ANMOKU_ROLE=fixer）：フローのファイル（*flow*.md、ANMOKU_WRITABLE で指定可）だけ許可。Bash は禁止
- それ以外：何もしない（通常の許可の流れ）

止めるときは終了コード2で、理由を標準エラーに出す（Claude Code のフック仕様）。
"""
import fnmatch
import json
import os
import sys

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
EVALUATORS = ("flow-evaluator", "design-evaluator", "hub-critic")
FIXERS = ("flow-fixer",)


def role(payload: dict) -> str:
    agent = (payload.get("agent_type") or "").split(":")[-1]
    if agent in EVALUATORS:
        return "evaluator"
    if agent in FIXERS:
        return "fixer"
    return os.environ.get("ANMOKU_ROLE", "")


def block(msg: str) -> None:
    print(f"[anmoku] {msg}", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    r = role(payload)
    tool = payload.get("tool_name", "")
    if not r:
        return
    if r == "evaluator" and (tool in WRITE_TOOLS or tool == "Bash"):
        block(f"評価役は読み取り専用です（{tool} は使えません）。指摘は出力として返してください。")
    if r == "fixer":
        if tool == "Bash":
            block("修正役は Bash を使えません。フローのファイルだけを Edit/Write で直してください。")
        if tool in WRITE_TOOLS:
            path = (payload.get("tool_input") or {}).get("file_path") or (payload.get("tool_input") or {}).get("notebook_path") or ""
            allowed = os.environ.get("ANMOKU_WRITABLE", "*flow*.md")
            name = os.path.basename(path)
            if not any(fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(name, pat) for pat in allowed.split(os.pathsep)):
                block(f"修正役が書けるのはフローのファイル（{allowed}）だけです。{path} には書けません。")


if __name__ == "__main__":
    main()
