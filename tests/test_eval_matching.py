import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from run_eval import matches, merged_kacho_sato  # noqa: E402

RULE = {"strong": ["統括", "佐藤"], "weak": ["課長"], "exclude": ["山本", "経理"]}


def test_negated_other_person_is_not_excluded():
    assert matches("課長（値引き相談先。経理の山本課長とは別の人とみられる） 課長 あの人", RULE)
    assert matches("課長（値引き相談先。山本課長とは別人と読める）", RULE)


def test_yamamoto_himself_is_excluded():
    assert not matches("経理の山本課長 山本課長", RULE)


def test_merge_requires_both_in_one_candidate():
    assert merged_kacho_sato(["佐藤さん（統括） 統括 佐藤さん 課長（値引き相談先。経理の山本課長とは別人と判断）"])
    assert not merged_kacho_sato(["佐藤さん 佐藤", "課長（値引き相談の相手。経理の山本課長とは別の人とみられる） 課長"])
    assert not merged_kacho_sato(["佐藤さん 経理の山本課長"])
