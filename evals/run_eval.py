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


def eval_scenario(name: str, runs: int, out: Path, model: str | None, modes: list[str], full: bool = False) -> dict:
    base = ROOT / "evals" / "scenarios" / name
    spec = yaml.safe_load((base / "spec.yaml").read_text(encoding="utf-8"))
    ans = spec["answer"]
    ivs = load_interviews(base / "interviews")
    flow_text = (base / "flow.md").read_text(encoding="utf-8")
    n = len(ivs)

    def gate2(rec: dict, tool_out: Path) -> None:
        """関門2：定義台帳 → フロー修正（承認）→ 設計書の評価。仕込んだ欠陥をキーワードで照合する。"""
        from anmoku.design_review import run_review
        from anmoku.fixflow import approve_flow, run_fix
        from anmoku.ledger import build_ledger

        detect = json.loads((tool_out / "detect.json").read_text(encoding="utf-8"))
        build_ledger(detect, tool_out, model=model)
        fx = run_fix(base, tool_out, model=model)
        approve_flow(tool_out)
        rec["fix"] = {"rounds": len(fx["rounds"]), "final": fx["rounds"][-1]["review"]["verdict"],
                      "added": len(fx["added_rows"]), "all_provisional": fx["all_added_provisional"], "removed": len(fx["removed_rows"])}
        rec["reviews"] = {}
        for key, dname, tname in (("design_v1", "agent-design_v1.md", "test-spec_v1.md"), ("design_v2", "agent-design_v2.md", None)):
            if key not in ans or not (base / dname).exists():
                continue
            rv = run_review(base, tool_out, dname, test_name=tname if tname and (base / tname).exists() else None, model=model)
            text = [f"{f['location']} {f['design_quote']} {f['problem']} {f['pass_condition']} {f.get('flow_basis') or ''}" for f in rv["findings"]]
            text += [f"{x['item']} {x['status']} {x['reason']}" for x in rv["readiness"] if x["status"] != "満たす"]
            text = [t.replace("二十万", "20万").replace("十万", "10万") for t in text]
            # 欠陥ごとのキーワードが「すべて」1つの指摘に入っていれば検出とみなす（粗い照合。判定の正しさは summary.json で人が確かめる）
            found = {d["id"]: any(all(k in t for k in d["keywords"]) for t in text) for d in ans[key].get("defects", [])}
            for d in ans[key].get("test_defects", []):
                found[d["id"]] = any(d["id"] in f["location"] for f in rv["findings"])
            wrong = [w for w in ans[key].get("must_not_flag", []) if any(w in t for t in text)]
            rec["reviews"][key] = {"verdict": rv["verdict"], "expected": ans[key]["expected_verdict"], "counts": rv["counts"],
                                   "found": found, "must_not_flag_hits": wrong, "n_findings": len(rv["findings"]),
                                   "dropped": len(rv["dropped"])}
            rec["cost"] += rv["cost_usd"]
        rec["cost"] += fx["cost_usd"]

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
        rec["critique"] = [{"name": c["name"], "strength": c["critique"]["strength"], "still_hub": c["critique"]["still_hub"]}
                           for c in res["stages"][-1]["candidates"] if c.get("critique")]
        if full:
            gate2(rec, out / name / f"tool-r{r}")
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
        crit = [c for t in res["tool"] for c in t.get("critique", [])]
        if crit:
            lines.append(f"- 反証役：{len(crit)}件の仮説を確認し、取り下げ {sum(not c['still_hub'] for c in crit)}件（強さ：{'・'.join(c['strength'] for c in crit)}）")
        fixes = [t["fix"] for t in res["tool"] if "fix" in t]
        if fixes:
            lines.append(f"- フロー修正：往復回数 {[f['rounds'] for f in fixes]}／最後の評価 {[f['final'] for f in fixes]}／追加行がすべて「仮」{sum(f['all_provisional'] for f in fixes)}/{len(fixes)}／既存行を消した回 {sum(f['removed'] > 0 for f in fixes)}")
        for key in ("design_v1", "design_v2"):
            rs = [t["reviews"][key] for t in res["tool"] if key in t.get("reviews", {})]
            if not rs:
                continue
            ids = list(rs[0]["found"])
            det = "、".join(f"{i} {sum(r['found'][i] for r in rs)}/{len(rs)}" for i in ids)
            lines.append(f"- 関門2 {key}：判定 {[r['verdict'] for r in rs]}（正解：{rs[0]['expected']}）／欠陥の検出 {det or '-'}"
                         + (f"／誤指摘（{'・'.join(sorted({w for r in rs for w in r['must_not_flag_hits']})) or 'なし'}）" if any('must_not_flag_hits' in r for r in rs) else ""))
        lines.append(f"- 費用の合計：ツール ${sum(t['cost'] for t in res['tool']):.2f}／貼るだけ ${sum(b['cost'] for b in res['baseline']):.2f}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="+")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--model")
    ap.add_argument("--modes", nargs="*", default=["directed", "general"], help="貼るだけの聞き方（空なら比べない）")
    ap.add_argument("--full", action="store_true", help="定義台帳・フロー修正・設計書の評価（関門2）まで回す")
    a = ap.parse_args()
    out = ROOT / "runs" / "eval" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    results = [eval_scenario(s, a.runs, out, a.model, a.modes, a.full) for s in a.scenarios]
    (out / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    md = summarize(results)
    (out / "summary.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"\n保存先：{out}")


if __name__ == "__main__":
    main()
