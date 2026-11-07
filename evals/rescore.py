"""保存済みの評価結果（runs/eval/<日時>/）から、LLM を呼ばずに集計し直す。

  uv run python evals/rescore.py runs/eval/20261107-162538

- ツール：tool-r*/detect.json（段階ごとの候補）を読み直す
- 貼るだけ：base-*/baseline-k*.json（LLM の出力そのもの）から候補を作り直し、引用を原文と照合し直す
- 関門2・フロー修正の結果は、元の summary.json の値をそのまま使う（再実行しない）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "evals"))

from anmoku.evidence import check_quote  # noqa: E402
from anmoku.interviews import load_interviews  # noqa: E402
from run_eval import base_rank, matches, merged_kacho_sato, summarize, tool_pure, tool_rank  # noqa: E402

EXCLUDED = ("社外", "不明", "組織全体")


def baseline_candidates(output: dict) -> list[dict]:
    if "issues" in output:  # 「課題を洗い出して」
        return [{"name": f"{i['title']}｜{i['detail']}", "aliases": i["people"], "quotes": i["quotes"]} for i in output["issues"]]
    return output["candidates"]


def rescore(run_dir: Path) -> list[dict]:
    old = {r["scenario"]: r for r in json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))}
    results = []
    for name, prev in old.items():
        base = ROOT / "evals" / "scenarios" / name
        ans = yaml.safe_load((base / "spec.yaml").read_text(encoding="utf-8"))["answer"]
        ivs = {iv.id: iv for iv in load_interviews(base / "interviews")}
        n = len(ivs)
        tool = []
        for t in prev["tool"]:
            detect = json.loads((run_dir / name / f"tool-r{t['run']}" / "detect.json").read_text(encoding="utf-8"))
            rec = {k: v for k, v in t.items() if k != "stages"} | {"stages": [],
                   "quotes": detect["quotes"]["kept"], "quotes_total": detect["quotes"]["total"],
                   "quotes_dropped": len(detect["quotes"]["dropped"])}
            for st in detect["stages"]:
                c = st["candidates"]
                s = {"k": st["k"], "hubs": [x["name"] for x in c if x.get("hub_hypothesis")],
                     "top": next(({"name": x["name"], "score": x["score"]} for x in c if x["category"] not in EXCLUDED), None),
                     # ツールは、実際の引用に出た呼び方（aliases）だけで判定する
                     "merged": any(merged_kacho_sato([" ".join(x["aliases"])]) for x in c if x["category"] not in EXCLUDED)}
                if ans.get("hub"):
                    s["hub_rank"] = tool_rank(c, ans["hub"]["match"])
                    s["pure"] = tool_pure(c, ans["hub"]["match"])
                    s["hub_hyp_ok"] = any(matches(x["name"] + " " + " ".join(x["aliases"]), ans["hub"]["match"]) for x in c if x.get("hub_hypothesis"))
                rec["stages"].append(s)
            tool.append(rec)
        basel = []
        for b in prev["baseline"]:
            mode = b.get("mode", "directed")
            log = json.loads((run_dir / name / f"base-{mode}-r{b['run']}" / f"baseline-k{b['k']}.json").read_text(encoding="utf-8"))
            cands = baseline_candidates(log["output"])
            total = kept = 0
            bad = []
            for c in cands:
                for q in c["quotes"]:
                    total += 1
                    iv = ivs.get(q["file"].removesuffix(".md"))
                    ok = bool(iv) and check_quote(iv, q["quote"], q["line"]).ok
                    kept += ok
                    if not ok:
                        bad.append(q)
            s = {"mode": mode, "run": b["run"], "k": b["k"], "names": [c["name"] for c in cands],
                 "quotes": kept, "quotes_total": total, "bad_quotes": bad, "cost": b["cost"],
                 "merged": merged_kacho_sato([c["name"] + " " + " ".join(c["aliases"]) for c in cands])}
            if ans.get("hub"):
                s["hub_rank"] = base_rank(cands, ans["hub"]["match"])
            s["decoys_named"] = [d["name"] for d in ans.get("decoys", []) if "match" in d and base_rank(cands, d["match"])]
            basel.append(s)
        results.append(prev | {"tool": tool, "baseline": basel})
    return results


def main() -> None:
    run_dir = Path(sys.argv[1])
    results = rescore(run_dir)
    (run_dir / "summary_rescored.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    md = summarize(results)
    (run_dir / "summary_rescored.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
