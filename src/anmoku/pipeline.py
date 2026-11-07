"""関門1：ハブの検知を、ヒアリング1人分ずつ段階的に実行する。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .extract import extract_one
from .flow import Flow, load_flow
from .interviews import Interview, load_interviews
from .resolve import resolve
from .score import rank
from .signals import scan


def cross_check(ivs: list[Interview], observations: list[dict]) -> dict:
    """ルール（手がかり語）と LLM の観察の食い違いを数える。片方にしか無いものは要確認。"""
    llm_lines = {(o["interview"], o["line"]) for o in observations}
    rule_only, both = [], 0
    rule_lines = set()
    for iv in ivs:
        for h in scan(iv):
            key = (h.interview, h.line)
            if key in rule_lines:
                continue
            rule_lines.add(key)
            if key in llm_lines:
                both += 1
            else:
                rule_only.append({"interview": h.interview, "line": h.line, "cue": h.cue, "text": h.text[:80]})
    llm_only = [o["id"] for o in observations if (o["interview"], o["line"]) not in rule_lines]
    return {"both": both, "rule_only": rule_only, "llm_only": llm_only}


def run_detect(interviews_dir: str | Path, flow_path: str | Path, out_dir: str | Path, *,
               model: str | None = None, reuse: bool = True, workers: int = 4, critic: bool = True) -> dict:
    out = Path(out_dir)
    (out / "extract").mkdir(parents=True, exist_ok=True)
    log_dir = out / "llm"
    ivs = load_interviews(interviews_dir)
    flow = load_flow(flow_path)
    if not ivs:
        raise SystemExit(f"ヒアリング記録がありません: {interviews_dir}")

    # 1) 1人分ずつ抽出（並列・文脈は人ごとに分ける）
    def _extract(iv: Interview) -> dict:
        cache = out / "extract" / f"{iv.id}.json"
        if reuse and cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        r = extract_one(iv, model=model, log_dir=log_dir)
        cache.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        return r

    with ThreadPoolExecutor(max_workers=workers) as ex:
        extracted = list(ex.map(_extract, ivs))

    # 2) 段階実行：k人目までの観察で、別名の統合と点数を出す（各段階は独立なので並列）
    speakers_all = {iv.speaker: iv.title.replace("ヒアリング記録：", "") for iv in ivs}

    def _stage(k: int) -> dict:
        # LLM の結果（別名の統合）だけをキャッシュし、点数は毎回 Python で計算し直す
        cache = out / f"resolve_{k}.json"
        obs = [o for e in extracted[:k] for o in e["observations"]]
        if reuse and cache.exists():
            entities, cost = json.loads(cache.read_text(encoding="utf-8")), 0.0
        else:
            speakers = {iv.speaker: speakers_all[iv.speaker] for iv in ivs[:k]}
            entities, cost = resolve(obs, speakers, flow, model=model, log_dir=log_dir, label=f"resolve-stage{k}")
            cache.write_text(json.dumps(entities, ensure_ascii=False, indent=2), encoding="utf-8")
        ranked = rank(entities, obs, flow)
        st = {
            "k": k,
            "added": {"id": ivs[k - 1].id, "speaker": ivs[k - 1].speaker, "title": ivs[k - 1].title},
            "candidates": ranked,
            "cost_usd": cost,
        }
        return st

    with ThreadPoolExecutor(max_workers=workers) as ex:
        stages = list(ex.map(_stage, range(1, len(ivs) + 1)))

    # 3) 反証役：最後の段階のハブの仮説ごとに、ハブではない理由を探す（読み取り専用）
    critic_cost = 0.0
    if critic:
        from .critic import critique

        final = stages[-1]["candidates"]
        hubs = [c for c in final if c.get("hub_hypothesis")]
        cache = out / "critic.json"
        cached = json.loads(cache.read_text(encoding="utf-8")) if (reuse and cache.exists()) else {}
        for c in hubs:
            key = ",".join(sorted(c["observation_ids"]))
            if key not in cached:
                cached[key] = critique(c, ivs, Path(flow_path), out, model=model, label=f"hub-critic-{c['id']}")
                critic_cost += cached[key]["cost_usd"]
            c["critique"] = cached[key]
            if not cached[key]["still_hub"]:
                c["hub_hypothesis"] = False
        cache.write_text(json.dumps(cached, ensure_ascii=False, indent=2), encoding="utf-8")

    all_obs = [o for e in extracted for o in e["observations"]]
    q_total = sum(e["quote_total"] for e in extracted)
    q_kept = sum(e["quote_kept"] for e in extracted)
    result = {
        "flow": {"title": flow.title, "lanes": flow.lanes},
        "interviews": [{"id": iv.id, "speaker": iv.speaker, "title": iv.title} for iv in ivs],
        "quotes": {"total": q_total, "kept": q_kept,
                   "dropped": [d for e in extracted for d in e["dropped"]]},
        "cross_check": cross_check(ivs, all_obs),
        "terms": [t for e in extracted for t in e["terms"]],
        "stages": stages,
        "cost_usd": round(sum(e.get("cost_usd", 0) for e in extracted) + sum(s["cost_usd"] for s in stages) + critic_cost, 4),
    }
    (out / "detect.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
