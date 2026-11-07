from anmoku.ledger import merge_ledger
from anmoku.schema import LedgerOut

TERMS = [
    {"id": "01#t1", "interview": "01", "line": 16, "quote": "値引きが入るやつは店長の承認が要る", "speaker": "小林", "defined": True},
    {"id": "04#t1", "interview": "04", "line": 20, "quote": "何年以上とか決まってるわけじゃない", "speaker": "伊藤", "defined": False},
]
EXISTING = {
    "discount-approval": {"id": "discount-approval", "label": "値引きの承認", "kind": "rule", "status": "approved",
                          "definition": "値引きは店長が承認する", "owner_hint": "店長", "used_by": []},
    "site-survey": {"id": "site-survey", "label": "現地調査", "kind": "term", "status": "approved",
                    "definition": "施主宅で採寸する", "owner_hint": "営業担当", "used_by": []},
}


def prop(**kw):
    base = {"id": "x", "label": "x", "kind": "term", "definition": None, "status": "needs_answer",
            "owner_hint": "統括", "evidence_refs": [], "questions": ["q?"]}
    return base | kw


def run(items):
    return merge_ledger(LedgerOut(items=[prop(**i) for i in items], implicit_rules=[]), TERMS, EXISTING,
                        set(EXISTING), [{"name": "統括"}])


def test_approved_existing_item_keeps_definition_and_status():
    r = run([{"id": "discount-approval", "label": "値引き", "definition": "LLMの別の定義", "evidence_refs": ["01#t1"]}])
    it = next(i for i in r["items"] if i["id"] == "discount-approval")
    assert it["status"] == "approved" and it["definition"] == "値引きは店長が承認する"
    assert it["origin"] == "existing" and len(it["evidence"]) == 1
    assert all(q["ledger_id"] != "discount-approval" for q in r["questions"])  # 承認済みには質問しない


def test_new_item_is_marked_new():
    r = run([{"id": "difficult-property", "label": "難あり物件", "evidence_refs": ["04#t1"]}])
    it = next(i for i in r["items"] if i["id"] == "difficult-property")
    assert it["origin"] == "new" and it["status"] == "needs_answer"
    assert r["counts"] == {"existing": 2, "new": 1}


def test_existing_item_not_in_narratives_is_kept():
    r = run([])
    it = next(i for i in r["items"] if i["id"] == "site-survey")
    assert it["not_in_narratives"] and it["status"] == "approved"


def test_proposal_without_real_evidence_is_dropped():
    r = run([{"id": "made-up", "label": "作り話", "evidence_refs": ["99#t9"]}])
    assert all(i["id"] != "made-up" for i in r["items"])


# ---------- 承認済みの定義の根拠には、定義を直接支える観察だけ ----------
TERMS2 = TERMS + [
    {"id": "04#t2", "interview": "04", "line": 28, "quote": "二割五分くらい残ってればいいかな", "speaker": "伊藤", "defined": True},
    {"id": "04#t3", "interview": "04", "line": 26, "quote": "このくらいまでなら大丈夫かなっていうのは、自分の中であるんです", "speaker": "伊藤", "defined": False},
]


def run2(items, rules=()):
    return merge_ledger(LedgerOut(items=[prop(**i) for i in items], implicit_rules=[prop(**i) for i in rules]),
                        TERMS2, EXISTING, set(EXISTING), [{"name": "統括"}])


def test_llm_marked_extension_moves_to_new_item():
    r = run2([{"id": "discount-approval", "label": "値引き（どこまで自分で）", "evidence_refs": ["01#t1"], "extends_refs": ["04#t3"],
               "questions": ["自分で決めてよい値引きの範囲は？"]}])
    ap = next(i for i in r["items"] if i["id"] == "discount-approval")
    assert [e["line"] for e in ap["evidence"]] == [16]
    new = next(i for i in r["items"] if i.get("split_from") == "discount-approval")
    assert new["origin"] == "new" and new["status"] == "needs_answer" and [e["line"] for e in new["evidence"]] == [26]
    assert any(q["ledger_id"] == new["id"] for q in r["questions"])  # 回答待ちとして質問にも出る


def test_quantity_not_in_approved_definition_is_moved_even_if_llm_put_it_in_evidence():
    # LLM が承認済みの根拠に入れても、定義に無い数（二割五分）を含む観察は外す
    r = run2([{"id": "discount-approval", "evidence_refs": ["01#t1", "04#t2"]}])
    ap = next(i for i in r["items"] if i["id"] == "discount-approval")
    assert [e["line"] for e in ap["evidence"]] == [16] and ap["status"] == "approved"
    new = next(i for i in r["items"] if i.get("split_from") == "discount-approval")
    assert [e["line"] for e in new["evidence"]] == [28] and new["definition"] is None


def test_moved_evidence_already_in_another_new_item_is_not_duplicated():
    r = run2([{"id": "discount-approval", "evidence_refs": ["01#t1"], "extends_refs": ["04#t2"]},
              {"id": "discount-guideline", "label": "値引きの目安", "evidence_refs": ["04#t2"]}])
    assert not any(i.get("split_from") for i in r["items"])
    assert next(i for i in r["items"] if i["id"] == "discount-guideline")["origin"] == "new"


def test_quantity_written_in_definition_stays_as_support():
    from anmoku.ledger import adds_quantity
    assert not adds_quantity("十万を超えたら一声かける", "追加が十万を超えたら現場監督に一声かける")
    assert adds_quantity("三万とか五万なら", "追加が十万を超えたら現場監督に一声かける")
    assert not adds_quantity("値引きが入るやつは店長の承認が要る", "値引きは店長が承認する")
