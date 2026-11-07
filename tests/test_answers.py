import json

import pytest

from anmoku.ledger import load_answers, record_answer


def _ledger(tmp_path):
    (tmp_path / "ledger.json").write_text(json.dumps({"questions": [
        {"ledger_id": "difficult-property", "question": "難ありの条件は？"},
        {"ledger_id": "contingency-amount", "question": "予備費はどう決める？"},
    ]}, ensure_ascii=False), encoding="utf-8")


def test_record_and_overwrite_same_question(tmp_path):
    _ledger(tmp_path)
    record_answer(tmp_path, 2, "最初の答え", "佐藤課長", "2026-11-10")
    record_answer(tmp_path, 2, "確かめ直した答え", "佐藤課長", "2026-11-12")
    record_answer(tmp_path, 1, "築30年以上", "佐藤課長", "2026-11-12")
    a = load_answers(tmp_path)
    assert len(a) == 2
    assert next(x for x in a if x["no"] == 2)["answer"] == "確かめ直した答え"


def test_out_of_range(tmp_path):
    _ledger(tmp_path)
    with pytest.raises(SystemExit):
        record_answer(tmp_path, 6, "x", "y", "2026-11-10")
