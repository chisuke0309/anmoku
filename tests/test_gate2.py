from anmoku.design_review import DesignReview, judge
from anmoku.evidence import norm

LEDGER = [
    {"id": "difficult-property", "label": "難あり物件", "status": "needs_answer", "owner_hint": "統括"},
    {"id": "tax-rule", "label": "消費税", "status": "approved", "owner_hint": "経理"},
]
DESIGN = norm("難あり物件なら上乗せを提案する。難あり物件の基準は確認待ちで、統括に回す。値引きは店長に回す。")


def f(**kw):
    base = {"aspect": "B", "target": "設計書", "location": "手順3", "design_quote": "難あり物件なら上乗せを提案する",
            "problem": "p", "ledger_id": "difficult-property", "flow_basis": None, "pending_acknowledged": False,
            "severity_proposal": "重大", "pass_condition": "c"}
    return base | kw


def ready(status="満たす"):
    return [{"no": i, "status": status, "reason": "r"} for i in range(1, 6)]


def run(findings, readiness=None, uses=None):
    rv = DesignReview.model_validate({"findings": findings, "readiness": readiness or ready(), "definitions_used": uses or []})
    return judge(rv, LEDGER, {"設計書": DESIGN, "テスト仕様": ""})


def test_silent_use_of_pending_definition_is_blocking():
    r = run([f()])
    assert r["verdict"] == "差し戻し" and r["counts"]["重大"] == 1


def test_acknowledged_pending_is_condition():
    r = run([f(design_quote="難あり物件の基準は確認待ちで、統括に回す", pending_acknowledged=True)])
    assert r["verdict"] == "条件つき合格" and r["counts"]["条件"] == 1
    assert r["pending_definitions"] == ["difficult-property"]


def test_fabricated_quote_and_unknown_ledger_are_dropped():
    r = run([f(design_quote="設計書に無い文をでっちあげた"), f(ledger_id="no-such-id")])
    assert r["findings"] == [] and len(r["dropped"]) == 2


def test_approved_definition_is_not_a_finding_and_pass():
    r = run([f(ledger_id="tax-rule")])
    assert r["findings"] == [] and r["verdict"] == "合格"


def test_definitions_used_keeps_pass_conditional():
    r = run([], uses=[{"ledger_id": "difficult-property", "target": "設計書", "design_quote": "難あり物件の基準は確認待ち"}])
    assert r["verdict"] == "条件つき合格"


def test_readiness_unknown_blocks_full_pass():
    assert run([], readiness=ready("不明"))["verdict"] == "条件つき合格"


def test_flow_finding_medium_is_conditional():
    r = run([f(aspect="A", ledger_id=None, design_quote="値引きは店長に回す", severity_proposal="中")])
    assert r["verdict"] == "条件つき合格"
