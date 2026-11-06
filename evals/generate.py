"""評価用データ（語り・公式フロー・設計書・テスト仕様）を生成する。

「生成と評価を分ける」ため、ここで claude に渡すのは spec.yaml の `generation` 部分だけ。
`answer`（正解）は読み込まない。語り手1人ごとに別の `claude -p` 呼び出し（別の文脈）で作る。

使い方:
  uv run python evals/generate.py basic             # 語り・フロー・設計書v1・テスト仕様v1
  uv run python evals/generate.py basic --only design_v2
"""

from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anmoku.flow import flow_from_spec, to_markdown  # noqa: E402
from anmoku.llm import run_claude  # noqa: E402

GEN_MODEL = "claude-opus-5-5"

# 語りに入れてはいけない言葉（答えを漏らさない）
FORBIDDEN = ["フローに載", "隠れた", "ボトルネック", "ハブ", "属人"]

NARRATIVE_SYSTEM = """あなたは、架空の会社の社員へのヒアリング（業務の聞き取り）の文字起こしを書く作家です。
本物の文字起こしらしく、話し言葉・言いよどみ（えーと、なんていうか）・脱線・あいまいな頻度の表現を入れてください。
聞き手は業務改善の担当者で、短い質問やあいづちだけを入れます。
話し手は自分の仕事の段取りを語るだけで、組織の問題を分析したり、まとめて列挙したりはしません。
困りごとや待ちは、愚痴や段取りの話の中に自然に混ぜてください。"""


def narrative_prompt(company: str, n: dict) -> str:
    others = "、".join(n["refer_to_hub"])
    return f"""# 会社の設定（架空）
{company}

# 今回の話し手
- 役割：{n['role']}
- 名前：{n['name']}

# この人が話すこと
{n['brief']}

# 書き方の決まり
- 形式：1行目は「# ヒアリング記録：{n['role']} {n['name']}さん」。2行目は空行。以降、発言ごとに1行で「聞き手：…」「{n['name']}：…」と書く。1つの発言は長くても150字程度で区切り、行を分ける
- 長さ：全体で2,000〜2,800字
- 工事部の佐藤課長のことを話すときは、呼び方は「{others}」だけを使う（合計{n['max_hub_mentions']}回まで）。それ以外の呼び方（フルネーム・役職名の別の言い方）は使わない
- 次の言葉は使わない：{'、'.join(FORBIDDEN)}
- 数値や固有名詞は架空のものだけ。前置きや解説は書かず、文字起こしの本文だけを出力する
"""


# 呼び方の混ざりを機械的に確かめる語（「課長」は経理の山本課長でも使うので対象外）
STRICT_NAMES = ["統括", "佐藤", "田中", "係長", "工程の人"]


def check_narrative(text: str, n: dict) -> list[str]:
    problems = [f"禁止語「{w}」" for w in FORBIDDEN if w in text]
    allowed = "".join(n["refer_to_hub"])
    for name in STRICT_NAMES:
        if name in text and name not in allowed:
            problems.append(f"許可していない呼び方「{name}」")
    n_chars = len(text)
    if not 1500 <= n_chars <= 3600:
        problems.append(f"長さ {n_chars} 字")
    return problems


def gen_narrative(scn: str, company: str, n: dict, out_dir: Path) -> str:
    prompt = narrative_prompt(company, n)
    last = []
    for attempt in range(3):
        res = run_claude(prompt, system=NARRATIVE_SYSTEM, model=GEN_MODEL, label=f"gen-{scn}-{n['id']}")
        text = res.text.strip() + "\n"
        # 「佐藤」は 03 の1回だけ許す
        probs = check_narrative(text, n)
        if n["id"] == "03_genba":
            probs = [p for p in probs if "佐藤" not in p]
        if not probs:
            (out_dir / f"{n['id']}.md").write_text(text, encoding="utf-8")
            return f"{n['id']}: OK ({len(text)}字, ${res.cost_usd:.3f})"
        last = probs
    (out_dir / f"{n['id']}.md").write_text(text, encoding="utf-8")
    return f"{n['id']}: 要確認 {last}"


DESIGNER_SYSTEM = """あなたは社内向けAIエージェントの設計役です。依頼に従って、Markdownで簡潔な設計書を書きます。
見出しは「目的」「対象の問い合わせ」「処理手順」（番号つき）「エスカレーション」「使う用語」など、必要なものを選んでください。
前置きや解説は書かず、文書の本文だけを出力します。"""


def gen_design(scn: str, spec: dict, base: Path, which: str) -> str:
    g = spec["generation"]
    flow_md = (base / "flow.md").read_text(encoding="utf-8")
    if which == "design_v1":
        prompt = f"# 公式の業務フロー\n{flow_md}\n\n# 依頼\n{g['design_request_v1']}"
        out = base / "agent-design_v1.md"
    elif which == "test_v1":
        design = (base / "agent-design_v1.md").read_text(encoding="utf-8")
        prompt = f"# 設計書\n{design}\n\n# 依頼\n{g['test_request_v1']}"
        out = base / "test-spec_v1.md"
    elif which == "design_v2":
        design = (base / "agent-design_v1.md").read_text(encoding="utf-8")
        review = base / "review_for_v2.md"
        rv = f"\n\n# 評価結果\n{review.read_text(encoding='utf-8')}" if review.exists() else ""
        prompt = f"# 公式の業務フロー\n{flow_md}\n\n# 1版目の設計書\n{design}\n\n# 依頼\n{g['design_request_v2']}{rv}"
        out = base / "agent-design_v2.md"
    else:
        raise ValueError(which)
    res = run_claude(prompt, system=DESIGNER_SYSTEM, model=GEN_MODEL, label=f"gen-{scn}-{which}")
    out.write_text(res.text.strip() + "\n", encoding="utf-8")
    return f"{which}: OK (${res.cost_usd:.3f})"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--only", nargs="*", help="flow narratives design_v1 test_v1 design_v2 / 語り手id")
    a = ap.parse_args()
    base = ROOT / "evals" / "scenarios" / a.scenario
    spec = yaml.safe_load((base / "spec.yaml").read_text(encoding="utf-8"))
    g = spec["generation"]  # answer は読まない
    only = set(a.only or ["flow", "narratives", "design_v1", "test_v1"])

    if "flow" in only:
        (base / "flow.md").write_text(to_markdown(flow_from_spec(g["flow"])), encoding="utf-8")
        print("flow: OK")
    targets = [n for n in g["narrators"] if "narratives" in only or n["id"] in only]
    if targets:
        out_dir = base / "interviews"
        out_dir.mkdir(exist_ok=True)
        with ThreadPoolExecutor(max_workers=4) as ex:
            for msg in ex.map(lambda n: gen_narrative(a.scenario, g["company"], n, out_dir), targets):
                print(msg)
    request_keys = {"design_v1": "design_request_v1", "test_v1": "test_request_v1", "design_v2": "design_request_v2"}
    for which, key in request_keys.items():
        if which in only and key in g:
            print(gen_design(a.scenario, spec, base, which))


if __name__ == "__main__":
    main()
