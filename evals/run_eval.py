"""ツールと「貼るだけ」を、同じシナリオで複数回実行して比べる。

  uv run python evals/run_eval.py basic decoy-defined --runs 3

結果：runs/eval/<日時>/summary.json と summary.md
正解（spec.yaml の answer）は、ここで照合にだけ使う。ツールにも貼るだけにも渡さない。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "evals"))

from anmoku.interviews import load_interviews  # noqa: E402
from anmoku.pipeline import run_detect  # noqa: E402
from baseline import run_baseline  # noqa: E402


def matches(text: str, rule: dict) -> bool:
    if any(w in text for w in rule.get("exclude", [])):
        return any(w in text for w in rule.get("strong", []))
    return any(w in text for w in rule.get("strong", []) + rule.get("weak", []))


def tool_rank(cands: list[dict], rule: dict) -> int | None:
    ranked = [c for c in cands if c["category"] not in ("社外", "不明", "組織全体") and c["score"] > 0]
    for i, c in enumerate(ranked, 1):
        if matches(c["name"] + " " + " ".join(c["aliases"]), rule):
            return i
    return None


def tool_pure(cands: list[dict], rule: dict) -> bool:
    """ハブ候補に、別人（exclude の語）の呼び方が混ざっていないか。"""
    r = tool_rank(cands, rule)
    if r is None:
        return True
    c = [c for c in cands if c["category"] not in ("社外", "不明", "組織全体") and c["score"] > 0][r - 1]
    return not any(any(w in a for w in rule.get("exclude", [])) for a in c["aliases"])


def base_rank(cands: list[dict], rule: dict) -> int | None:
    for i, c in enumerate(cands, 1):
        if matches(c["name"] + " " + " ".join(c["aliases"]), rule):
            return i
    return None


def eval_scenario(name: str, runs: int, out: Path, model: str | None, modes: list[str]) -> dict:
    base = ROOT / "evals" / "scenarios" / name
    spec = yaml.safe_load((base / "spec.yaml").read_text(encoding="utf-8"))
    ans = spec["answer"]
    ivs = load_interviews(base / "interviews")
    flow_text = (base / "flow.md").read_text(encoding="utf-8")
    n = len(ivs)

    def one_tool(r: int) -> dict:
        res = run_detect(base / "interviews", base / "flow.md", out / name / f"tool-r{r}", model=model, reuse=False)
        rec = {"run": r, "quotes": res["quotes"]["kept"], "quotes_total": res["quotes"]["total"], "cost": res["cost_usd"], "stages": []}
        for st in res["stages"]:
            c = st["candidates"]
            s = {"k": st["k"], "hubs": [x["name"] for x in c if x.get("hub_hypothesis")], "top": next(({"name": x["name"], "score": x["score"]} for x in c if x["category"] not in ("社外", "不明", "組織全体")), None)}
            if ans.get("hub"):
                s["hub_rank"] = tool_rank(c, ans["hub"]["match"])
                s["pure"] = tool_pure(c, ans["hub"]["match"])
                s["hub_hyp_ok"] = any(matches(x["name"] + " " + " ".join(x["aliases"]), ans["hub"]["match"]) for x in c if x.get("hub_hypothesis"))
            s["decoys_in_top3"] = [d["name"] for d in ans.get("decoys", []) if (tool_rank(c, d["match"]) or 99) <= 3] if any("match" in d for d in ans.get("decoys", [])) else []
            rec["stages"].append(s)
        return rec

    def one_base(r: int, k: int, mode: str) -> dict:
        res = run_baseline(ivs[:k], flow_text, model=model, log_dir=out / name / f"base-{mode}-r{r}",
                           label=f"baseline-k{k}", mode=mode)
        c = res["candidates"]
        s = {"mode": mode, "run": r, "k": k, "names": [x["name"] for x in c], "quotes": res["quotes"]["kept"],
             "quotes_total": res["quotes"]["total"], "cost": res["cost_usd"]}
        if ans.get("hub"):
            s["hub_rank"] = base_rank(c, ans["hub"]["match"])
        s["decoys_named"] = [d["name"] for d in ans.get("decoys", []) if "match" in d and base_rank(c, d["match"])]
        return s

    with ThreadPoolExecutor(max_workers=6) as ex:
        tool_f = [ex.submit(one_tool, r) for r in range(1, runs + 1)]
        base_f = [ex.submit(one_base, r, k, m) for m in modes for r in range(1, runs + 1) for k in range(1, n + 1)]
        tool = [f.result() for f in tool_f]
        basel = [f.result() for f in base_f]
    return {"scenario": name, "n_interviews": n, "runs": runs, "modes": modes, "tool": tool, "baseline": basel}


def first_rank1(ranks: list[int | None]) -> int | None:
    for k, r in enumerate(ranks, 1):
        if r == 1:
            return k
    return None


ROLE_WORDS = ["フロー", "定義", "属人", "集中", "明文化", "依存", "一人", "1人"]
MODE_LABEL = {"directed": "貼るだけ（ツールと同じ問い）", "general": "貼るだけ（課題を洗い出して）"}


def summarize(results: list[dict]) -> str:
    lines = ["# 評価の結果（ツール vs 貼るだけ）", "",
             "同じモデル・同じ入力（k人目までの語り＋公式フロー）で比べた。正解は照合にだけ使い、どちらにも渡していない。", ""]
    for res in results:
        n, runs = res["n_interviews"], res["runs"]
        modes = res.get("modes", ["directed"])
        lines += [f"## {res['scenario']}（語り{n}本 × {runs}回）", ""]
        has_hub = any("hub_rank" in s for t in res["tool"] for s in t["stages"])
        if has_hub:
            head = "| k人目まで | ツール：ハブが1位 | ツール：ハブの仮説になった | ツール：別人が混ざらない |"
            for m in modes:
                head += f" {MODE_LABEL[m]}：1位 | {MODE_LABEL[m]}：上位3 |"
            lines += [head, "|" + "---|" * (4 + 2 * len(modes))]
            for k in range(1, n + 1):
                t = [t["stages"][k - 1] for t in res["tool"]]
                row = f"| {k} | {sum(s['hub_rank'] == 1 for s in t)}/{len(t)} | {sum(bool(s.get('hub_hyp_ok')) for s in t)}/{len(t)} | {sum(s['pure'] for s in t)}/{len(t)} |"
                for m in modes:
                    b = [b for b in res["baseline"] if b["k"] == k and b.get("mode", "directed") == m]
                    row += f" {sum(x['hub_rank'] == 1 for x in b)}/{len(b)} | {sum((x['hub_rank'] or 99) <= 3 for x in b)}/{len(b)} |"
                lines.append(row)
            lines.append("")
            tf = [first_rank1([s["hub_rank"] for s in t["stages"]]) for t in res["tool"]]
            lines.append(f"- 初めて1位になった人数：ツール {tf}")
            for m in modes:
                bf = [first_rank1([x["hub_rank"] for x in sorted([b for b in res["baseline"] if b["run"] == r and b.get("mode", "directed") == m], key=lambda b: b["k"])]) for r in range(1, runs + 1)]
                lines.append(f"- 初めて1位になった人数：{MODE_LABEL[m]} {bf}（None は最後まで1位にならず）")
            if "general" in modes:
                g = [b for b in res["baseline"] if b.get("mode") == "general" and b["k"] == n]
                ranks = [b["hub_rank"] for b in g]
                lines.append(f"- {MODE_LABEL['general']}で、全員分を渡したときにハブが出てくる課題の順位：{ranks}")
        else:
            head = "| k人目まで | ツール：1位（点数） | ツール：ハブの仮説を出した回数 |"
            dm = [m for m in modes if m == "directed"]
            for m in dm:
                head += f" {MODE_LABEL[m]}：挙げた人 | {MODE_LABEL[m]}：おとりを挙げた回数 |"
            lines += [head, "|" + "---|" * (3 + 2 * len(dm))]
            for k in range(1, n + 1):
                t = [t["stages"][k - 1] for t in res["tool"]]
                tops = "、".join(f"{s['top']['name']}({s['top']['score']})" if s["top"] else "-" for s in t)
                row = f"| {k} | {tops} | {sum(bool(s['hubs']) for s in t)}/{len(t)} |"
                for m in dm:
                    b = [b for b in res["baseline"] if b["k"] == k and b.get("mode", "directed") == m]
                    row += f" {' ／ '.join('・'.join(x['names']) or '（なし）' for x in b)} | {sum(bool(x['decoys_named']) for x in b)}/{len(b)} |"
                lines.append(row)
            lines.append("")
        tq = sum(t["quotes"] for t in res["tool"]), sum(t["quotes_total"] for t in res["tool"])
        lines.append(f"- 引用の実在率：ツール {tq[0]}/{tq[1]}（原文と照合して通った数／LLMが出した数。ツールは通らない引用を自動で捨てる）")
        for m in modes:
            b = [b for b in res["baseline"] if b.get("mode", "directed") == m]
            lines.append(f"- 引用の実在率：{MODE_LABEL[m]} {sum(x['quotes'] for x in b)}/{sum(x['quotes_total'] for x in b)}")
        lines.append(f"- 費用の合計：ツール ${sum(t['cost'] for t in res['tool']):.2f}／貼るだけ ${sum(b['cost'] for b in res['baseline']):.2f}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="+")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--model")
    ap.add_argument("--modes", nargs="+", default=["directed", "general"], help="貼るだけの聞き方")
    a = ap.parse_args()
    out = ROOT / "runs" / "eval" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    results = [eval_scenario(s, a.runs, out, a.model, a.modes) for s in a.scenarios]
    (out / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    md = summarize(results)
    (out / "summary.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"\n保存先：{out}")


if __name__ == "__main__":
    main()
