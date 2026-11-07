"""2人目の時点で「課長」と「佐藤」を同じ相手にまとめたかを測る（追加の測定）。

  uv run python evals/stage2_merge.py measure --runs 10   # anmoku・貼るだけ（同じ問い）とも10回ずつ
  uv run python evals/stage2_merge.py past                 # runs/eval にある過去の実行をすべて集計

- 回数は先に10回と決めて測る。結果を見て増やさない
- anmoku は語り01・02だけの案件を作って detect する（反証役は呼ばない。統合の判定には使わないため）
- 貼るだけは baseline.py の「ツールと同じ問い」を k=2 で呼ぶ
- 判定は run_eval.merged_kacho_sato（anmoku は実際の引用に出た呼び方、貼るだけは候補名と別名）
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "evals"))

from anmoku.interviews import load_interviews  # noqa: E402
from anmoku.pipeline import run_detect  # noqa: E402
from baseline import run_baseline  # noqa: E402
from run_eval import merged_kacho_sato  # noqa: E402

BASE = ROOT / "evals" / "scenarios" / "basic"
EXCLUDED = ("社外", "不明", "組織全体")


def tool_merged(stage: dict) -> bool:
    return any(merged_kacho_sato([" ".join(c["aliases"])]) for c in stage["candidates"] if c["category"] not in EXCLUDED)


def base_merged(cands: list[dict]) -> bool:
    return merged_kacho_sato([c["name"] + " " + " ".join(c.get("aliases", [])) for c in cands])


def measure(runs: int) -> None:
    out = ROOT / "runs" / "eval" / f"stage2-{time.strftime('%Y%m%d-%H%M%S')}"
    case = out / "case"
    (case / "interviews").mkdir(parents=True)
    ivs = load_interviews(BASE / "interviews")[:2]
    for iv in ivs:
        shutil.copyfile(iv.path, case / "interviews" / iv.path.name)
    shutil.copyfile(BASE / "flow.md", case / "flow.md")
    flow_text = (BASE / "flow.md").read_text(encoding="utf-8")

    def one_tool(r: int) -> dict:
        res = run_detect(case / "interviews", case / "flow.md", out / f"tool-r{r}", reuse=False, critic=False)
        st = res["stages"][-1]
        return {"run": r, "merged": tool_merged(st), "cost": res["cost_usd"]}

    def one_base(r: int) -> dict:
        res = run_baseline(ivs, flow_text, model=None, log_dir=out / f"base-r{r}", label="baseline-k2", mode="directed")
        return {"run": r, "merged": base_merged(res["candidates"]), "names": [c["name"] for c in res["candidates"]], "cost": res["cost_usd"]}

    with ThreadPoolExecutor(max_workers=6) as ex:
        tf = [ex.submit(one_tool, r) for r in range(1, runs + 1)]
        bf = [ex.submit(one_base, r) for r in range(1, runs + 1)]
        tool, base = [f.result() for f in tf], [f.result() for f in bf]
    res = {"runs": runs, "tool": tool, "baseline": base}
    (out / "summary.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    md = (f"# 2人目の時点の統合（追加の測定・{runs}回ずつ・{time.strftime('%Y-%m-%d %H:%M')}）\n\n"
          f"- anmoku：{sum(t['merged'] for t in tool)}/{runs}\n"
          f"- 貼るだけ（ツールと同じ問い）：{sum(b['merged'] for b in base)}/{runs}\n"
          f"- 費用：anmoku ${sum(t['cost'] for t in tool):.2f}／貼るだけ ${sum(b['cost'] for b in base):.2f}\n")
    (out / "summary.md").write_text(md, encoding="utf-8")
    print(md + f"\n保存先：{out}")


def past() -> None:
    """runs/eval/<日時>/basic にある過去の実行を、実行ごとに集計する。"""
    rows = []
    for d in sorted((ROOT / "runs" / "eval").glob("2*/basic")):
        t = [p for p in sorted(d.glob("tool-r*")) if (p / "detect.json").exists()]
        b = sorted(d.glob("base-directed-r*")) + sorted(d.glob("base-r*"))
        tm = [tool_merged(json.loads((p / "detect.json").read_text(encoding="utf-8"))["stages"][1]) for p in t]
        bm = []
        for p in b:
            f = p / "baseline-k2.json"
            if f.exists():
                bm.append(base_merged(json.loads(f.read_text(encoding="utf-8"))["output"]["candidates"]))
        rows.append({"run_dir": d.parent.name, "tool": tm, "baseline": bm})
    for r in rows:
        print(f"{r['run_dir']}: anmoku {sum(r['tool'])}/{len(r['tool'])}  貼るだけ（同じ問い） {sum(r['baseline'])}/{len(r['baseline'])}")
    print(f"合計: anmoku {sum(sum(r['tool']) for r in rows)}/{sum(len(r['tool']) for r in rows)}  "
          f"貼るだけ {sum(sum(r['baseline']) for r in rows)}/{sum(len(r['baseline']) for r in rows)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["measure", "past"])
    ap.add_argument("--runs", type=int, default=10)
    a = ap.parse_args()
    measure(a.runs) if a.cmd == "measure" else past()


if __name__ == "__main__":
    main()
