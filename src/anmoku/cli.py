"""anmoku のコマンド。

  anmoku detect  <案件フォルダ>   # 関門1：ハブの検知（段階実行）→ <案件>/anmoku-out/detect.json と viewer.html
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _case_paths(case: Path, out: str | None) -> tuple[Path, Path, Path]:
    return case / "interviews", case / "flow.md", Path(out) if out else case / "anmoku-out"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="anmoku", description="定義されていない役割を、仮説として見つける")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("detect", help="関門1：ハブの検知（1人ずつ段階実行）")
    d.add_argument("case", help="interviews/ と flow.md がある案件フォルダ")
    d.add_argument("--out")
    d.add_argument("--model")
    d.add_argument("--fresh", action="store_true", help="前回の結果を使わずに実行し直す")

    a = ap.parse_args(argv)
    if a.cmd == "detect":
        from .pipeline import run_detect
        from .report import print_detect_summary

        ivdir, flow, out = _case_paths(Path(a.case), a.out)
        res = run_detect(ivdir, flow, out, model=a.model, reuse=not a.fresh)
        print_detect_summary(res, sys.stdout)
        from .render import write_viewer

        html = write_viewer(res, out / "viewer.html")
        print(f"画面：{html}")


if __name__ == "__main__":
    main()
