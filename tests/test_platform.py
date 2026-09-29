from fastapi.testclient import TestClient

from exops.config import get_settings
from exops.data.synth import generate
from exops.eval import harness
from exops.guardrails import policy
from exops.llm.base import extract_json
from exops.models import Classification, ExceptionType, Plan, PlannedAction, RiskLevel, Status
from exops.pipeline import Platform
from exops.rag.retriever import SOPRetriever


def _platform(n=60, seed=3):
    ds = generate(n=n, seed=seed)
    return ds, Platform(ds.world)


def test_retriever_finds_right_sop():
    r = SOPRetriever.from_dir()
    assert r.search("duplicate charge two captures refund")[0].doc.doc_id == "SOP-PAY-02"
    assert r.search("courier scan stuck in transit hub")[0].doc.doc_id == "SOP-LOG-01"


def test_extract_json_tolerates_fences():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": 2} hope that helps') == {"a": 2}


def test_pipeline_processes_all_and_never_auto_executes_risky_cases():
    ds, p = _platform()
    for s in ds.samples:
        c = p.process(s.event)
        if s.truth.needs_approval:
            assert c.status in (Status.PENDING_APPROVAL, Status.ESCALATED), s.event.event_id
    assert p.summary()["cases"] == len(ds.samples)


def test_duplicate_charge_refunds_exactly_the_extra_capture():
    ds, p = _platform(n=120)
    s = next(x for x in ds.samples if x.truth.exception_type == ExceptionType.DUPLICATE_CHARGE)
    c = p.process(s.event)
    if c.status == Status.PENDING_APPROVAL:
        p.approve(c.case_id, "tester")
    led = p.world.get_payment_ledger(s.event.order_id)
    assert abs(led["captured"] - led["refunded"] - p.world.orders[s.event.order_id].amount) < 0.01


def test_saga_rollback_restores_state():
    ds, p = _platform(n=120)
    s = next(x for x in ds.samples if x.truth.exception_type == ExceptionType.SLA_BREACH_RISK
             and "reassign_courier" in x.truth.expected_actions)
    before = p.world.orders[s.event.order_id].courier
    c = p.process(s.event)
    assert c.status == Status.AUTO_RESOLVED
    assert p.world.orders[s.event.order_id].courier != before
    p.rollback(c.case_id)
    assert p.world.orders[s.event.order_id].courier == before


def test_guardrail_blocks_overrefund():
    ds, p = _platform()
    oid = next(o for o, x in p.world.orders.items() if x.payment_mode == "PREPAID")
    obs = {"get_order": p.world.get_order(oid), "get_payment_ledger": p.world.get_payment_ledger(oid)}
    cls = Classification(exception_type=ExceptionType.REFUND_MISMATCH, confidence=0.99, severity=RiskLevel.HIGH,
                         rationale="", model="t")
    plan = Plan(actions=[PlannedAction(tool="initiate_refund", args={"order_id": oid, "amount": 10 ** 7,
                                                                      "reason": "x"})],
                confidence=0.99, rationale="", model="t")
    v = policy.evaluate(plan, cls, p.registry, obs, get_settings())
    assert not v.allowed


def test_pii_is_masked():
    out = policy.mask_pii({"customer_phone": "+91 9876543210", "text": "reach me at 9876543210, x@y.com"})
    assert "9876543210" not in str(out) and "x@y.com" not in str(out)


def test_eval_gate_passes():
    rep = harness.run(n=120, seed=5, judge_sample=10)
    assert rep["passed"], rep["gates"]


def test_api_smoke():
    from exops.api.app import app

    c = TestClient(app)
    assert c.post("/api/demo/seed?n=20").status_code == 200
    cases = c.get("/api/cases").json()
    assert len(cases) == 20
    q = c.get("/api/cases?status=PENDING_APPROVAL").json()
    if q:
        r = c.post(f"/api/cases/{q[0]['case_id']}/approve", json={"approver": "t"})
        assert r.json()["status"] in ("APPROVED_RESOLVED", "ROLLED_BACK", "FAILED")
    detail = c.get(f"/api/cases/{cases[0]['case_id']}").json()
    assert detail["audit"][0]["stage"] == "received"
