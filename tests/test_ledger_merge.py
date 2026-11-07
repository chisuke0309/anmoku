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
