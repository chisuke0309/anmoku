"""比較の測定：語り・フロー・設計書1版目・テスト仕様を Claude に全文貼って「レビューして」と聞くだけの場合。

  uv run python evals/review_baseline.py basic --runs 3

ツールと同じモデル。比べやすいよう出力の形だけ JSON（指摘の一覧）にそろえ、
仕込んだ欠陥（spec.yaml の answer.design_v1）は、ツールの評価と同じキーワード照合で数える。
結果は、どちらに有利でもそのまま runs/eval/review-baseline-<日時>/ と evals/results/ に残す。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anmoku.interviews import load_interviews  # noqa: E402
from anmoku.llm import run_claude  # noqa: E402

PROMPT = """以下は、ある会社の業務ヒアリングの記録、公式の業務フロー、社内向けAIエージェントの設計書とテスト仕様です。
この設計書とテスト仕様をレビューしてください。

# 公式の業務フロー
{flow}

# ヒアリング記録
{interviews}

# 設計書
{design}

# テスト仕様
{test}
"""


class Finding(BaseModel):
    target: str
    location: str
    quote: str
    problem: str
    suggestion: str


class Review(BaseModel):
    findings: list[Finding]


def found_defects(findings: list[dict], ans: dict) -> dict[str, bool]:
    text = [f"{f['location']} {f['quote']} {f['problem']} {f['suggestion']}".replace("二十万", "20万").replace("十万", "10万")
            for f in findings]
    found = {d["id"]: any(all(k in t for k in d["keywords"]) for t in text) for d in ans.get("defects", [])}
    for d in ans.get("test_defects", []):
        found[d["id"]] = any(d["id"] in f["location"] or d["id"] in f["quote"] for f in findings)
    return found


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--model")
    a = ap.parse_args()
    base = ROOT / "evals" / "scenarios" / a.scenario
    ans = yaml.safe_load((base / "spec.yaml").read_text(encoding="utf-8"))["answer"]["design_v1"]
    ivs = load_interviews(base / "interviews")
    body = "\n\n".join(f"## ファイル：{iv.id}.md\n" + "\n".join(f"{i}: {l}" for i, l in enumerate(iv.lines, 1)) for iv in ivs)
    test = base / "test-spec_v1.md"
    prompt = PROMPT.format(flow=(base / "flow.md").read_text(encoding="utf-8"), interviews=body,
                           design=(base / "agent-design_v1.md").read_text(encoding="utf-8"),
                           test=test.read_text(encoding="utf-8") if test.exists() else "（なし）")
    out = ROOT / "runs" / "eval" / f"review-baseline-{time.strftime('%Y%m%d-%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)

    def one(r: int) -> dict:
        res = run_claude(prompt, schema=Review.model_json_schema(), model=a.model, log_dir=out, label=f"{a.scenario}-r{r}")
        fs = Review.model_validate(res.data).model_dump()["findings"]
        return {"run": r, "n_findings": len(fs), "found": found_defects(fs, ans), "cost": res.cost_usd}

    with ThreadPoolExecutor(max_workers=a.runs) as ex:
        recs = list(ex.map(one, range(1, a.runs + 1)))
    ids = list(recs[0]["found"])
    lines = [f"# 貼るだけで「レビューして」と聞いた場合（{a.scenario}・設計書1版目＋テスト仕様 × {a.runs}回）", "",
             "同じモデル。入力は語り・公式フロー・設計書1版目・テスト仕様の全文。仕込んだ欠陥はツールと同じキーワード照合で数えた。", "",
             "| 回 | 指摘の数 | " + " | ".join(ids) + " | 検出数 |", "|" + "---|" * (len(ids) + 3)]
    for r in recs:
        lines.append(f"| {r['run']} | {r['n_findings']} | " + " | ".join("○" if r["found"][i] else "－" for i in ids)
                     + f" | {sum(r['found'].values())}/{len(ids)} |")
    lines += ["", "合計：" + "、".join(f"{i} {sum(r['found'][i] for r in recs)}/{len(recs)}" for i in ids),
              f"費用：${sum(r['cost'] for r in recs):.2f}"]
    md = "\n".join(lines) + "\n"
    (out / "summary.json").write_text(json.dumps(recs, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "summary.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"保存先：{out}")


if __name__ == "__main__":
    main()
