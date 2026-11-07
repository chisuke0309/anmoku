"""関門2：別のAIが作ったエージェント設計書（＋テスト仕様）を、承認済みのフローと定義台帳で評価する。

評価役は読み取り専用（Read/Grep/Glob だけ・Hook でも書き込みを止める）。
LLM は「設計書のどこが、どの物差しに照らして問題か」を引用つきで出すだけ。
重さの一部（定義台帳に頼る指摘）と、合格／条件つき合格／差し戻しの判定は Python が決める。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field

from .evidence import norm
from .fixflow import guard_settings
from .llm import run_claude
from .prompts import agent_prompt

READINESS = [
    "判断基準が言葉になっているか",
    "例外の頻度と、まちがえたときの影響が分かっているか",
    "正解データ（過去の問い合わせと答え）があるか",
    "エスカレーション先と責任者が決まっているか",
    "効果を測る方法が決まっているか",
]

SYSTEM = agent_prompt("design-evaluator")  # 正本：agents/design-evaluator.md


class Finding(BaseModel):
    aspect: Literal["A", "B", "D"]
    target: Literal["設計書", "テスト仕様"]
    location: str = Field(description="設計書の見出し・手順番号、またはテストID")
    design_quote: str
    problem: str
    ledger_id: Optional[str] = Field(default=None, description="観点B・Dで照らした定義台帳のID")
    flow_basis: Optional[str] = Field(default=None, description="観点Aで照らしたフローの行（タスク名）や決まりごと")
    pending_acknowledged: bool = False
    severity_proposal: Literal["重大", "中", "軽"]
    pass_condition: str = Field(description="合格の条件（何が決まり、設計書に何が書かれればよいか）")


class Readiness(BaseModel):
    no: int = Field(description="1〜5")
    status: Literal["満たす", "満たさない", "不明"]
    reason: str
    question: Optional[str] = None


class DefinitionUse(BaseModel):
    ledger_id: str = Field(description="設計書（またはテスト仕様）が頼っている定義台帳のID")
    target: Literal["設計書", "テスト仕様"]
    design_quote: str = Field(description="その定義に頼っている箇所の原文そのままの引用")


class DesignReview(BaseModel):
    findings: list[Finding]
    readiness: list[Readiness]
    definitions_used: list[DefinitionUse] = Field(default_factory=list, description="設計書が使っている定義台帳の項目すべて（指摘の有無に関係なく）")


def _ledger_brief(ledger: list[dict]) -> str:
    return "\n".join(f"- {x['id']}：{x['label']}（状態：{x['status']}／定義：{x.get('definition') or '未確定'}／答えを持つ人：{x.get('owner_hint') or '-'}）"
                     for x in ledger)


def review_design(design: Path, flow: Path, ledger_dir: Path, out: Path, *, test_spec: Path | None = None,
                  model: str | None = None, label: str = "design-evaluator") -> dict:
    ledger = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(ledger_dir.glob("*.yaml"))]
    files = [design.resolve(), flow.resolve()] + ([test_spec.resolve()] if test_spec else [])
    prompt = f"""# 読むファイル
- 設計書：{design.resolve()}
- 業務フロー（人の承認済み。「仮」は未確認の追加）：{flow.resolve()}
{f'- テスト仕様：{test_spec.resolve()}' if test_spec else '- テスト仕様：なし（観点Dは出さない）'}

# 定義台帳（物差し）
{_ledger_brief(ledger)}

# 本番前チェック（readiness の no は次の番号）
""" + "\n".join(f"{i}. {t}" for i, t in enumerate(READINESS, 1)) + "\n\nファイルを読んで評価してください。"
    res = run_claude(
        prompt, system=SYSTEM, tools=["Read", "Grep", "Glob"], schema=DesignReview.model_json_schema(),
        model=model, cwd=out, add_dirs=sorted({str(f.parent) for f in files}), settings=guard_settings(),
        env={"ANMOKU_ROLE": "evaluator"}, log_dir=out / "llm", label=label,
    )
    rv = DesignReview.model_validate(res.data)
    texts = {"設計書": norm(design.read_text(encoding="utf-8")),
             "テスト仕様": norm(test_spec.read_text(encoding="utf-8")) if test_spec else ""}
    return judge(rv, ledger, texts) | {"design": design.name, "test_spec": test_spec.name if test_spec else None,
                                       "cost_usd": res.cost_usd}


def judge(rv: DesignReview, ledger: list[dict], texts: dict[str, str]) -> dict:
    """引用を検証し、重さと判定を決める（同じ入力なら同じ結果）。"""
    by_id = {x["id"]: x for x in ledger}
    kept, dropped = [], []
    for f in rv.findings:
        d = f.model_dump()
        q = norm(f.design_quote)
        if len(q) < 4 or q not in texts.get(f.target, ""):
            dropped.append(d | {"drop": "設計書・テスト仕様に無い引用"})
            continue
        item = by_id.get(f.ledger_id) if f.ledger_id else None
        if f.aspect in ("B", "D") and f.ledger_id and not item:
            dropped.append(d | {"drop": "定義台帳に無いID"})
            continue
        sev = f.severity_proposal
        status = item["status"] if item else None
        if f.aspect == "B" and item:
            if status == "approved":
                dropped.append(d | {"drop": "承認済みの定義に照らしているので指摘にしない"})
                continue
            # 承認されていない定義に頼る：未確定と書かずにエージェントが当てはめるときだけ重大。明記して人に回していれば「条件」
            sev = "重大" if (not f.pending_acknowledged and f.severity_proposal == "重大") else "条件"
        if f.aspect == "D" and item and status != "approved":
            sev = "重大"
        kept.append(d | {"severity": sev, "ledger_status": status,
                         "ledger_owner": item.get("owner_hint") if item else None})
    order = {"重大": 0, "中": 1, "条件": 2, "軽": 3}
    kept.sort(key=lambda x: (order[x["severity"]], x["aspect"], x["location"]))
    counts = {k: sum(1 for f in kept if f["severity"] == k) for k in order}
    readiness = [r.model_dump() | {"item": READINESS[r.no - 1]} for r in rv.readiness if 1 <= r.no <= 5]
    pending_ids = {f["ledger_id"] for f in kept if f["ledger_id"] and f["ledger_status"] != "approved"}
    uses = []
    for u in rv.definitions_used:
        if u.ledger_id in by_id and len(norm(u.design_quote)) >= 4 and norm(u.design_quote) in texts.get(u.target, ""):
            uses.append(u.model_dump() | {"status": by_id[u.ledger_id]["status"]})
            if by_id[u.ledger_id]["status"] != "approved":
                pending_ids.add(u.ledger_id)
    pending = sorted(pending_ids)
    if counts["重大"] > 0:
        verdict = "差し戻し"
    elif counts["中"] > 0 or counts["条件"] > 0 or pending or any(r["status"] != "満たす" for r in readiness):
        verdict = "条件つき合格"
    else:
        verdict = "合格"
    return {"verdict": verdict, "counts": counts, "findings": kept, "dropped": dropped,
            "readiness": readiness, "pending_definitions": pending, "definitions_used": uses}


def render_review(r: dict, ledger: list[dict]) -> str:
    by_id = {x["id"]: x for x in ledger}
    c = r["counts"]
    lines = [f"# 設計書の評価（関門2）：{r['design']}", "",
             f"**判定：{r['verdict']}**（重大{c['重大']}・中{c['中']}・条件{c['条件']}・軽{c['軽']}）", "",
             "評価に使った物差し：人が承認した業務フロー（「仮」の行を含む）と定義台帳。判定の規則は Python で固定（重大か中が1件でもあれば差し戻し、"
             "承認されていない定義に頼る部分が残るあいだは合格にしない）。", ""]
    for f in r["findings"]:
        basis = ""
        if f["ledger_id"]:
            it = by_id.get(f["ledger_id"], {})
            basis = f"定義「{it.get('label', f['ledger_id'])}」＝{ {'needs_answer': '回答待ち', 'draft': '下書き', 'approved': '承認済み'}.get(f['ledger_status'], f['ledger_status'])}"
            if it.get("evidence"):
                e = it["evidence"][0]
                basis += f"／根拠 {e['source'].split('/')[-1]}:{e['line']}「{e['quote']}」"
                if len(it["evidence"]) > 1:
                    basis += f" ほか{len(it['evidence']) - 1}件"
        elif f["flow_basis"]:
            basis = f"フロー：{f['flow_basis']}"
        lines += [f"- [{f['severity']}]（観点{f['aspect']}）{f['target']} {f['location']}「{f['design_quote']}」",
                  f"  - 問題：{f['problem']}"]
        if basis:
            lines.append(f"  - 根拠：{basis}")
        lines.append(f"  - 合格の条件：{f['pass_condition']}")
    lines += ["", "## 本番前チェック（5項目）", "", "| 項目 | 判定 | 理由 | 聞くこと |", "|---|---|---|---|"]
    for x in r["readiness"]:
        lines.append(f"| {x['item']} | {x['status']} | {x['reason']} | {x.get('question') or ''} |")
    if r["pending_definitions"]:
        lines += ["", f"## 合格に残る条件：承認されていない定義 {len(r['pending_definitions'])}件", "",
                  "| 定義 | 状態 | 答えを持つ人（仮説） |", "|---|---|---|"]
        st = {"needs_answer": "回答待ち", "draft": "下書き", "approved": "承認済み"}
        for i in r["pending_definitions"]:
            it = by_id.get(i, {})
            lines.append(f"| {it.get('label', i)} | {st.get(it.get('status'), it.get('status'))} | {it.get('owner_hint') or '-'} |")
        lines += ["", "質問は答えを持つ人宛てに作成済み（questions.md）。回答を確かめて `anmoku approve` で承認すると、この条件が外れます。"]
    lines += ["", "> 本番前チェックの項目は、公開されているAIエージェント導入のチェックリスト（X のアカウント `@mardehaym` の投稿）を参考に、自分の言葉で5項目に絞ったもの。"]
    return "\n".join(lines) + "\n"


def mark_used_by(ledger_dir: Path, r: dict) -> None:
    """設計書が頼っている定義に、used_by を書き足す（どの設計書のどこが、その定義の回答を待っているか）。"""
    refs = [f for f in r["findings"] if f.get("ledger_id")] + [
        {"ledger_id": u["ledger_id"], "target": u["target"], "location": u["design_quote"][:20]} for u in r.get("definitions_used", [])]
    for f in refs:
        if not f.get("ledger_id"):
            continue
        p = ledger_dir / f"{f['ledger_id']}.yaml"
        if not p.exists():
            continue
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        ref = f"{r['design'] if f['target'] == '設計書' else r['test_spec']}#{f['location']}"
        if ref not in doc.get("used_by", []):
            doc.setdefault("used_by", []).append(ref)
        p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")


def run_review(case: Path, out: Path, design_name: str, *, test_name: str | None = None, model: str | None = None) -> dict:
    flow = out / "flow_approved.md"
    if not flow.exists():
        flow = out / "flow_v2.md" if (out / "flow_v2.md").exists() else case / "flow.md"
    design = case / design_name
    test = case / test_name if test_name else None
    r = review_design(design, flow, out / "ledger", out, test_spec=test, model=model,
                      label=f"design-evaluator-{design.stem}")
    r["flow_used"] = flow.name
    ledger = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted((out / "ledger").glob("*.yaml"))]
    mark_used_by(out / "ledger", r)
    (out / f"review_{design.stem}.json").write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / f"review_{design.stem}.md").write_text(render_review(r, ledger), encoding="utf-8")
    return r


def rejudge(case: Path, out: Path, design_name: str, *, test_name: str | None = None) -> dict:
    """保存済みの評価役の出力（llm/ の記録）から、引用の検証と判定だけをやり直す（LLM は呼ばない）。"""
    design = case / design_name
    test = case / test_name if test_name else None
    log = json.loads((out / "llm" / f"design-evaluator-{design.stem}.json").read_text(encoding="utf-8"))
    rv = DesignReview.model_validate(log["output"])
    ledger = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted((out / "ledger").glob("*.yaml"))]
    texts = {"設計書": norm(design.read_text(encoding="utf-8")),
             "テスト仕様": norm(test.read_text(encoding="utf-8")) if test else ""}
    r = judge(rv, ledger, texts) | {"design": design.name, "test_spec": test.name if test else None, "cost_usd": 0.0}
    (out / f"review_{design.stem}.json").write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / f"review_{design.stem}.md").write_text(render_review(r, ledger), encoding="utf-8")
    return r
