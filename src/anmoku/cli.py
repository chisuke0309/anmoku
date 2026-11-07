"""anmoku のコマンド。案件フォルダには interviews/（語り）と flow.md（公式フロー）を置く。

  anmoku detect  <案件>                 関門1：ハブの検知（1人ずつ段階実行）
  anmoku ledger  <案件>                 定義台帳と、ハブ宛ての質問5つ
  anmoku fix     <案件>                 フローの修正（修正役）→ 再確認（評価役）
  anmoku approve-flow <案件>            人が修正版フローを承認する
  anmoku review  <案件> <設計書.md> [--test <テスト仕様.md>]   関門2：設計書の評価
  anmoku approve <案件> <定義ID> "<定義>"  人が回答を確かめた定義を承認済みにする
  anmoku view    <案件>                 画面（HTML）を作り直す
  anmoku run     <案件> [--design ...] [--test ...]   detect → ledger → fix → view（→ review）

結果は <案件>/anmoku-out/ に出る（--out で変更可）。表示はすべて仮説。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _out(case: Path, out: str | None) -> Path:
    p = Path(out) if out else case / "anmoku-out"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _load(out: Path, name: str) -> dict | None:
    p = out / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def cmd_detect(case: Path, out: Path, a) -> dict:
    from .pipeline import run_detect
    from .report import print_detect_summary

    res = run_detect(case / "interviews", case / "flow.md", out, model=a.model, reuse=not a.fresh)
    print_detect_summary(res, sys.stdout)
    return res


def cmd_ledger(case: Path, out: Path, a) -> dict:
    from .ledger import build_ledger

    detect = _load(out, "detect.json")
    if not detect:
        raise SystemExit("先に anmoku detect を実行してください")
    r = build_ledger(detect, out, model=a.model)
    st = {"needs_answer": "回答待ち", "draft": "下書き", "approved": "承認済み"}
    print(f"定義台帳：{len(r['items'])}件（{out / 'ledger'}）")
    for it in r["items"]:
        print(f"  [{st[it['status']]}] {it['label']}  → 答えを持つ人（仮説）：{it.get('owner_hint') or '-'}")
    print(f"\n次のヒアリングで聞く質問（{out / 'questions.md'}）")
    for i, q in enumerate(r["questions"], 1):
        print(f"  {i}. {q['question']}（→ {q['to']}）")
    return r


def cmd_fix(case: Path, out: Path, a) -> dict:
    from .fixflow import run_fix

    r = run_fix(case, out, model=a.model)
    for rd in r["rounds"]:
        print(f"{rd['round']}回目：評価役の確認 → {rd['review']['verdict']}（重大 {sum(i['severity'] == '重大' for i in rd['review']['issues'])}件）")
    print(f"追加した行：{len(r['added_rows'])}（すべて「仮」：{r['all_added_provisional']}）／消した行：{len(r['removed_rows'])}")
    print(f"修正版：{out / 'flow_v2.md'}　改善前後の図：{out / 'flow_compare.md'}")
    print("人が内容を確かめたら：anmoku approve-flow <案件>")
    return r


def cmd_review(case: Path, out: Path, design: str, test: str | None, a) -> dict:
    from .design_review import run_review

    r = run_review(case, out, design, test_name=test, model=a.model)
    c = r["counts"]
    print(f"判定：{r['verdict']}（重大{c['重大']}・中{c['中']}・条件{c['条件']}・軽{c['軽']}）　{out / ('review_' + Path(design).stem + '.md')}")
    return r


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="anmoku", description="定義されていない役割を、仮説として見つける")
    ap.add_argument("--model", help="使うモデル（既定：claude-sonnet-5-5、環境変数 ANMOKU_MODEL でも指定可）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, help_):
        p = sub.add_parser(name, help=help_)
        p.add_argument("case", help="interviews/ と flow.md がある案件フォルダ")
        p.add_argument("--out")
        return p

    d = add("detect", "関門1：ハブの検知（1人ずつ段階実行）")
    d.add_argument("--fresh", action="store_true", help="前回の結果を使わずに実行し直す")
    add("ledger", "定義台帳と質問5つ")
    add("fix", "フローの修正と再確認")
    add("approve-flow", "人が修正版フローを承認する")
    rv = add("review", "関門2：設計書の評価")
    rv.add_argument("design")
    rv.add_argument("--test")
    ap_ = add("approve", "人が定義を承認済みにする")
    ap_.add_argument("item_id")
    ap_.add_argument("definition")
    add("view", "画面を作り直す")
    r = add("run", "detect → ledger → fix → view（設計書があれば review も）")
    r.add_argument("--design", nargs="*", default=[])
    r.add_argument("--test")
    r.add_argument("--fresh", action="store_true")
    r.add_argument("--approve-flow", action="store_true", help="修正版フローをそのまま承認して設計書の評価へ進む（デモ・評価用）")

    a = ap.parse_args(argv)
    case = Path(a.case)
    out = _out(case, a.out)

    if a.cmd == "detect":
        cmd_detect(case, out, a)
    elif a.cmd == "ledger":
        cmd_ledger(case, out, a)
    elif a.cmd == "fix":
        cmd_fix(case, out, a)
    elif a.cmd == "approve-flow":
        from .fixflow import approve_flow

        print(f"承認しました：{approve_flow(out)}（「仮」の行は、次のヒアリングで確かめてから外してください）")
    elif a.cmd == "review":
        cmd_review(case, out, a.design, a.test, a)
    elif a.cmd == "approve":
        from .ledger import approve

        doc = approve(out / "ledger", a.item_id, a.definition)
        print(f"承認しました：{doc['label']} ＝ {doc['definition']}")
    elif a.cmd == "run":
        cmd_detect(case, out, a)
        print()
        cmd_ledger(case, out, a)
        print()
        cmd_fix(case, out, a)
        if a.design:
            if a.approve_flow:
                from .fixflow import approve_flow

                approve_flow(out)
            for i, dsg in enumerate(a.design):
                print()
                cmd_review(case, out, dsg, a.test if i == 0 else None, a)
    if a.cmd in ("detect", "ledger", "fix", "approve-flow", "review", "approve", "view", "run"):
        from .render import write_dashboard

        html = write_dashboard(out, case)
        print(f"\n画面：{html}")


if __name__ == "__main__":
    main()
