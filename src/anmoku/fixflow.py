"""関門1の後半：フローの修正（修正役）→ 再確認（評価役）→ 人の承認。

- 修正役：フローのファイル（flow_v2.md）にだけ書ける。Hook（hooks/guard_write.py）で機械的に止める
- 評価役：読み取りツールだけ。修正役の考えた経緯は渡さず、修正版のフローと語りだけを渡す
- 修正版で追加した行には「仮」を付け、人が次のヒアリングで確かめたら外す
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .evidence import check_quote
from .flow import load_flow, to_mermaid
from .interviews import load_interviews
from .llm import run_claude
from .prompts import agent_prompt

REPO = Path(__file__).resolve().parents[2]
GUARD = REPO / "hooks" / "guard_write.py"


def guard_settings() -> dict:
    return {"hooks": {"PreToolUse": [{"matcher": "Write|Edit|MultiEdit|NotebookEdit|Bash",
                                      "hooks": [{"type": "command", "command": f"python3 '{GUARD}'"}]}]}}


FIXER_SYSTEM = agent_prompt("flow-fixer")  # 正本：agents/flow-fixer.md

EVAL_SYSTEM = agent_prompt("flow-evaluator")  # 正本：agents/flow-evaluator.md


class FlowIssue(BaseModel):
    kind: Literal["矛盾", "直しすぎ", "不足"]
    severity: Literal["重大", "軽微"]
    row: str = Field(description="対象の行（タスク名）。不足のときは足すべき内容")
    problem: str
    file: str = Field(description="根拠の語りのファイル名（例：03_genba.md）")
    line: int
    quote: str


class FlowReview(BaseModel):
    verdict: Literal["承認してよい", "直す必要あり"]
    checked_rows: list[str] = Field(description="確認した『仮』の行")
    issues: list[FlowIssue]


def build_findings(detect: dict, ledger: dict | None) -> str:
    final = detect["stages"][-1]["candidates"]
    hubs = [c for c in final if c.get("hub_hypothesis")]
    lines = ["# 評価役の指摘（関門1）", ""]
    for h in hubs:
        lines.append(f"## フローに無い役割（ハブの仮説）：{h['name']}（呼び方：{'・'.join(h['aliases'])}／点数 {h['score']}）")
        topics: dict[str, list[dict]] = {}
        for e in h["evidence"]:
            topics.setdefault(e["kind"], []).append(e)
        for e in h["evidence"][:12]:
            lines.append(f"- {e['kind']}：{e['interview']}.md:{e['line']}「{e['quote']}」")
        lines.append("")
    if ledger:
        rules = [i for i in ledger["items"] if i.get("group") == "implicit_rule"]
        if rules:
            lines.append("## フローに無い決まりごと")
            for r in rules:
                ev = r["evidence"][0]
                lines.append(f"- {r['label']}（{ev['source'].split('/')[-1]}:{ev['line']}「{ev['quote']}」）")
    return "\n".join(lines) + "\n"


def run_fix(case: Path, out: Path, *, model: str | None = None, max_rounds: int = 2) -> dict:
    detect = json.loads((out / "detect.json").read_text(encoding="utf-8"))
    ledger_path = out / "ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else None
    v1 = case / "flow.md"
    v2 = out / "flow_v2.md"
    shutil.copyfile(v1, v2)
    findings = build_findings(detect, ledger)
    (out / "findings_gate1.md").write_text(findings, encoding="utf-8")

    rounds = []
    instruction = f"{findings}\n# 直すファイル\n{v2.resolve()}\n\n上の指摘にもとづいて、このファイルを直してください。"
    cost = 0.0
    for rnd in range(1, max_rounds + 1):
        # 1) 修正役：flow_v2.md にだけ書ける（Hook で機械的に止める）
        fix = run_claude(
            instruction,
            system=FIXER_SYSTEM, tools=["Read", "Edit"], model=model, cwd=out,
            settings=guard_settings(), permission_mode="acceptEdits",
            env={"ANMOKU_ROLE": "fixer", "ANMOKU_WRITABLE": str(v2.resolve())},
            log_dir=out / "llm", label=f"flow-fixer-r{rnd}",
        )
        # 2) 評価役：修正版と語りだけを渡す（修正役の説明は渡さない）
        review = run_flow_review(case, out, model=model, label=f"flow-evaluator-r{rnd}")
        cost += fix.cost_usd + review["cost_usd"]
        rounds.append({"round": rnd, "fixer_summary": fix.text, "review": review})
        if not review["verdict"].startswith("直す"):
            break
        issues = "\n".join(
            f"- [{i['severity']}・{i['kind']}] {i['row']}：{i['problem']}（{i['file']}:{i['line']}「{i['quote']}」）" for i in review["issues"])
        instruction = (f"# 評価役の再確認での指摘\n{issues}\n\n# 直すファイル\n{v2.resolve()}\n\n"
                       "指摘に従って、このファイルの『仮』の行を直してください。既存の公式の行は、担当が語りと食い違う場合だけ"
                       "備考に「語りでは〜（要確認）」と書き足してよい（消さない）。")
    fix_text, review = rounds[-1]["fixer_summary"], rounds[-1]["review"]

    base, new = load_flow(v1), load_flow(v2)
    added = [r for r in new.rows if (r.lane, r.task) not in {(b.lane, b.task) for b in base.rows}]
    removed = [r for r in base.rows if (r.lane, r.task) not in {(n.lane, n.task) for n in new.rows}]
    result = {
        "status": "人の承認待ち",
        "rounds": rounds,
        "fixer_summary": fix_text,
        "added_rows": [r.__dict__ for r in added],
        "removed_rows": [r.__dict__ for r in removed],
        "all_added_provisional": all(r.provisional for r in added),
        "review": review,
        "mermaid_before": to_mermaid(base),
        "mermaid_after": to_mermaid(new, base=base),
        "cost_usd": round(cost, 4),
    }
    (out / "fix.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "flow_compare.md").write_text(
        "# 業務フロー 改善前と改善後（仮）\n\n黄色の点線＝「仮」で追加した行（次のヒアリングで確かめる）。\n\n"
        f"## 改善前（公式）\n\n```mermaid\n{result['mermaid_before']}```\n\n## 改善後（仮・人の承認待ち）\n\n```mermaid\n{result['mermaid_after']}```\n",
        encoding="utf-8")
    return result


def run_flow_review(case: Path, out: Path, *, model: str | None = None, label: str = "flow-evaluator") -> dict:
    v2 = (out / "flow_v2.md").resolve()
    ivdir = (case / "interviews").resolve()
    res = run_claude(
        f"# 確認するフロー\n{v2}\n\n# ヒアリングの語り（フォルダ）\n{ivdir}\n\nフローと語りを読んで確認してください。",
        system=EVAL_SYSTEM, tools=["Read", "Grep", "Glob"], schema=FlowReview.model_json_schema(),
        model=model, cwd=out, add_dirs=[ivdir], settings=guard_settings(),
        env={"ANMOKU_ROLE": "evaluator"}, log_dir=out / "llm", label=label,
    )
    rv = FlowReview.model_validate(res.data)
    ivs = {iv.id: iv for iv in load_interviews(ivdir)}
    issues = []
    for i in rv.issues:
        iv = ivs.get(i.file.removesuffix(".md"))
        ok = bool(iv) and check_quote(iv, i.quote, i.line).ok
        if ok:
            issues.append(i.model_dump())
    # 判定は Python：根拠の確かな「重大」が1件でもあれば直す必要あり
    blocking = [i for i in issues if i["severity"] == "重大"]
    verdict = "直す必要あり" if blocking else "承認してよい（人の承認待ち）"
    return {"verdict": verdict, "llm_verdict": rv.verdict, "checked_rows": rv.checked_rows, "issues": issues,
            "dropped_issues": len(rv.issues) - len(issues), "cost_usd": res.cost_usd}


def approve_flow(out: Path) -> Path:
    """人が承認した版を flow_approved.md として残す（「仮」の印はそのまま。確かめた行から人が外す）。"""
    dst = out / "flow_approved.md"
    shutil.copyfile(out / "flow_v2.md", dst)
    fx = out / "fix.json"
    if fx.exists():
        d = json.loads(fx.read_text(encoding="utf-8"))
        d["status"] = "人が承認済み"
        fx.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return dst
