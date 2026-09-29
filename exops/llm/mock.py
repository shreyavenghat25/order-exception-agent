"""Deterministic mock LLM so the platform, demo and evals run offline with zero API keys.

It is deliberately *imperfect* in a realistic way:
  * "small" tier  -> soft heuristic scoring + noise; uncertain on ambiguous signals and on money moves.
  * "large" tier  -> applies the SOP thresholds exactly; rarely wrong, costs more.
That makes the model router, confidence gating and the eval harness meaningful.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from typing import Any

from exops.llm.base import LLMRequest, LLMResult, estimate_tokens

TYPES = ["SHIPMENT_STUCK", "PAYMENT_ORDER_MISMATCH", "DUPLICATE_CHARGE", "REFUND_MISMATCH", "RTO_RISK",
         "SLA_BREACH_RISK"]
SOP_FOR = {
    "SHIPMENT_STUCK": "SOP-LOG-01", "SLA_BREACH_RISK": "SOP-LOG-02", "PAYMENT_ORDER_MISMATCH": "SOP-PAY-01",
    "DUPLICATE_CHARGE": "SOP-PAY-02", "REFUND_MISMATCH": "SOP-PAY-03", "RTO_RISK": "SOP-RISK-01",
}


def _rng(*parts: Any) -> random.Random:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def soft_scores(s: dict[str, Any]) -> dict[str, float]:
    scan = s.get("last_scan_hours")
    sc = {t: 0.0 for t in TYPES}
    if scan is not None:
        if scan >= 48:
            sc["SHIPMENT_STUCK"] = 2.0 + (scan - 48) / 50
        elif scan >= 24:
            sc["SHIPMENT_STUCK"] = 0.8 + (scan - 24) / 24 * 1.2
    if s.get("eta_hours") and s.get("promised_by_hours") and s["eta_hours"] > s["promised_by_hours"]:
        sc["SLA_BREACH_RISK"] = 1.5 + (1.0 if scan is not None and scan < 24 else 0) - (
            1.0 if scan is not None and scan >= 48 else 0)
    if s.get("payment_captured") and s.get("order_status") == "PAYMENT_PENDING":
        sc["PAYMENT_ORDER_MISMATCH"] = 2.0
    if s.get("capture_count", 1) >= 2:
        sc["DUPLICATE_CHARGE"] = 3.0
        sc["PAYMENT_ORDER_MISMATCH"] -= 1.5
    if s.get("gateway_retry"):
        sc["DUPLICATE_CHARGE"] += 1.0
    if s.get("refund_gap", 0) > 0:
        sc["REFUND_MISMATCH"] = 3.0
    if s.get("cod") and s.get("address_score", 1) < 0.6:
        sc["RTO_RISK"] = 2.5
    return sc


def sop_exact(s: dict[str, Any]) -> str:
    scan = s.get("last_scan_hours")
    if s.get("capture_count", 1) >= 2:
        return "DUPLICATE_CHARGE"
    if s.get("refund_gap", 0) > 0:
        return "REFUND_MISMATCH"
    if s.get("payment_captured") and s.get("order_status") == "PAYMENT_PENDING":
        return "PAYMENT_ORDER_MISMATCH"
    if s.get("cod") and s.get("address_score", 1) < 0.6:
        return "RTO_RISK"
    if scan is not None and scan >= 48:
        return "SHIPMENT_STUCK"
    if s.get("eta_hours") and s.get("promised_by_hours") and s["eta_hours"] > s["promised_by_hours"]:
        return "SLA_BREACH_RISK"
    return "UNKNOWN"


class MockProvider:
    def __init__(self, model: str = "mock-small"):
        self.model = model
        self.large = "large" in model

    # ------------------------------------------------------------------ public
    def complete(self, req: LLMRequest) -> LLMResult:
        data = getattr(self, f"_{req.task}")(req.context)
        raw = json.dumps(data)
        in_tok = estimate_tokens(req.system + req.prompt)
        out_tok = estimate_tokens(raw)
        r = _rng(self.model, req.task, req.context.get("event_id", ""))
        latency = (900 if self.large else 250) + r.uniform(0, 400 if self.large else 120) + out_tok * (
            12 if self.large else 4)
        return LLMResult(data, raw, self.model, in_tok, out_tok, round(latency, 1))

    # ------------------------------------------------------------------ tasks
    def _classify(self, ctx: dict[str, Any]) -> dict[str, Any]:
        s = ctx["signals"]
        r = _rng(self.model, "classify", ctx.get("event_id"))
        if self.large:
            label = sop_exact(s)
            if r.random() < 0.02:  # rare slip
                label = r.choice(TYPES)
            conf = round(r.uniform(0.86, 0.97), 3)
            why = f"Applied SOP thresholds to signals {sorted(s)} -> {label}."
        else:
            sc = {t: v + r.gauss(0, 0.35) for t, v in soft_scores(s).items()}
            z = sum(math.exp(v / 0.5) for v in sc.values())
            probs = {t: math.exp(v / 0.5) / z for t, v in sc.items()}
            label = max(probs, key=probs.get)
            conf = round(probs[label], 3)
            if max(soft_scores(s).values()) <= 0:
                label, conf = "UNKNOWN", 0.3
            why = f"Strongest signals point to {label}."
        return {"exception_type": label, "confidence": conf, "rationale": why}

    def _plan(self, ctx: dict[str, Any]) -> dict[str, Any]:
        et = ctx["exception_type"]
        oid = ctx["order_id"]
        obs = ctx.get("observations", {})
        order = obs.get("get_order", {})
        led = obs.get("get_payment_ledger", {})
        trk = obs.get("get_tracking", {})
        risk = obs.get("get_risk_profile", {})
        inv = obs.get("check_inventory", {"available": True})
        r = _rng(self.model, "plan", ctx.get("event_id"))
        A: list[dict[str, Any]] = []

        def act(tool: str, why: str, **args: Any) -> None:
            A.append({"tool": tool, "args": {"order_id": oid, **args}, "reason": why})

        if et == "SHIPMENT_STUCK":
            if trk.get("last_scan_hours", 0) >= 120:
                act("create_replacement", "last scan >=120h: presumed lost")
                act("notify_customer", "inform replacement", template="replacement_created")
            else:
                act("raise_courier_ticket", "stuck at hub", issue=f"No scan at {trk.get('hub', 'hub')}")
                act("notify_customer", "apologise for delay", template="delay_apology")
        elif et == "PAYMENT_ORDER_MISMATCH":
            if inv.get("available", True):
                act("reconcile_payment", "payment captured, stock available")
                act("notify_customer", "confirm order", template="order_confirmed")
            else:
                amt = round(led.get("captured", 0) - led.get("refunded", 0), 2)
                act("initiate_refund", "not fulfillable", amount=amt, reason="order_not_fulfillable")
                act("notify_customer", "refund notice", template="refund_initiated")
        elif et == "DUPLICATE_CHARGE":
            amt = round(led.get("captured", 0) - order.get("amount", 0) - led.get("refunded", 0), 2)
            act("initiate_refund", "extra capture", amount=amt, reason="duplicate_capture")
            act("notify_customer", "duplicate refund notice", template="duplicate_refund")
        elif et == "REFUND_MISMATCH":
            amt = round((led.get("expected_refund") or 0) - led.get("refunded", 0), 2)
            act("initiate_refund", "refund gap", amount=amt, reason="refund_gap")
            act("notify_customer", "refund adjusted", template="refund_adjusted")
        elif et == "RTO_RISK":
            act("request_address_verification", "low address quality")
            if risk.get("address_score", 1) < 0.4:
                act("hold_shipment", "address score < 0.4")
            else:
                act("notify_customer", "nudge to prepaid", template="prepaid_nudge")
        elif et == "SLA_BREACH_RISK":
            if trk.get("courier_on_time_rate", 1) < 0.8:
                new = "Ekart" if order.get("courier") == "BlueDart" else "BlueDart"
                act("reassign_courier", "courier on-time rate < 0.80", courier=new)
            else:
                act("expedite_shipment", "courier reliable, upgrade lane")
            if trk.get("eta_hours", 0) - order.get("promised_by_hours", 0) > 24:
                act("notify_customer", "ETA >24h past promise", template="delay_heads_up")

        high_stakes = any(a["tool"] in ("initiate_refund", "create_replacement") for a in A)
        if self.large:
            conf = round(r.uniform(0.88, 0.97), 3)
        else:
            # small model: occasionally forgets the customer notification, and is unsure about money moves
            if len(A) > 1 and r.random() < 0.06:
                A = [a for a in A if a["tool"] != "notify_customer"]
            conf = round(r.uniform(0.62, 0.74) if high_stakes else r.uniform(0.78, 0.92), 3)
        return {"actions": A, "confidence": conf, "cited_sops": [SOP_FOR.get(et, "POLICY-00")],
                "rationale": f"Followed {SOP_FOR.get(et, 'POLICY-00')} for {et}."}

    def _judge(self, ctx: dict[str, Any]) -> dict[str, Any]:
        et = ctx["exception_type"]
        cited_ok = SOP_FOR.get(et) in ctx.get("cited_sops", [])
        has_notify = any(a == "notify_customer" for a in ctx.get("actions", []))
        customer_facing = et != "RTO_RISK" or "notify_customer" in ctx.get("actions", [])
        score = 5
        if not cited_ok:
            score -= 2
        if customer_facing and not has_notify and et not in ("SLA_BREACH_RISK",):
            score -= 1
        if not ctx.get("actions"):
            score -= 2
        return {"score": max(1, score), "reason": "SOP adherence and customer communication check."}
