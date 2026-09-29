"""Deterministic guardrail + policy layer. The LLM proposes; policy disposes.

Hard violations block an action outright. Soft rules route the case to a human approval queue.
"""
from __future__ import annotations

import re
from typing import Any

from exops.config import Settings
from exops.models import Classification, GuardrailVerdict, Plan, RiskLevel
from exops.tools.registry import Tool

_PHONE = re.compile(r"(\+?91[\s-]?)?[6-9]\d{9}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

# which write tools each exception type may use (defence in depth against off-SOP actions)
ALLOWED: dict[str, set[str]] = {
    "SHIPMENT_STUCK": {"raise_courier_ticket", "notify_customer", "create_replacement"},
    "SLA_BREACH_RISK": {"reassign_courier", "expedite_shipment", "notify_customer"},
    "PAYMENT_ORDER_MISMATCH": {"reconcile_payment", "initiate_refund", "notify_customer"},
    "DUPLICATE_CHARGE": {"initiate_refund", "notify_customer"},
    "REFUND_MISMATCH": {"initiate_refund", "notify_customer"},
    "RTO_RISK": {"request_address_verification", "hold_shipment", "notify_customer"},
}


def mask_pii(obj: Any) -> Any:
    """Recursively mask phone numbers / emails before anything is sent to a model."""
    if isinstance(obj, str):
        return _EMAIL.sub("<email>", _PHONE.sub("<phone>", obj))
    if isinstance(obj, dict):
        return {k: ("<phone>" if "phone" in k else mask_pii(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_pii(v) for v in obj]
    return obj


def evaluate(plan: Plan, cls: Classification, registry: dict[str, Tool], observations: dict[str, Any],
             s: Settings) -> GuardrailVerdict:
    v: list[str] = []
    approve: list[str] = []
    allowed = ALLOWED.get(cls.exception_type.value, set())
    led = observations.get("get_payment_ledger", {})
    refundable = led.get("captured", 0) - led.get("refunded", 0)
    order_status = observations.get("get_order", {}).get("status")
    seen: set[tuple] = set()
    writes = 0

    if not plan.actions:
        approve.append("empty plan")
    for a in plan.actions:
        tool = registry.get(a.tool)
        if tool is None or "internal" in tool.tags:
            v.append(f"unknown or internal tool '{a.tool}'")
            continue
        if not tool.write:
            continue
        writes += 1
        key = (a.tool, tuple(sorted((k, str(x)) for k, x in a.args.items())))
        if key in seen:
            v.append(f"duplicate action {a.tool}")
        seen.add(key)
        if a.tool not in allowed:
            v.append(f"{a.tool} not permitted for {cls.exception_type.value}")
        if a.args.get("order_id") != observations.get("get_order", {}).get("order_id"):
            v.append(f"{a.tool} targets a different order")
        if a.tool == "initiate_refund":
            amt = float(a.args.get("amount", 0))
            if amt <= 0:
                v.append("refund amount must be positive")
            elif amt > refundable + 0.01:
                v.append(f"refund Rs {amt:.2f} exceeds refundable Rs {refundable:.2f}")
            elif amt > s.auto_refund_limit_inr:
                approve.append(f"refund Rs {amt:.0f} > auto limit Rs {s.auto_refund_limit_inr:.0f}")
        if a.tool in ("reassign_courier", "hold_shipment") and order_status == "DELIVERED":
            v.append(f"{a.tool} on a delivered order")
        if tool.risk == RiskLevel.HIGH and a.tool != "initiate_refund":
            approve.append(f"{a.tool} is HIGH risk")
    if {"initiate_refund", "create_replacement"} <= {a.tool for a in plan.actions}:
        v.append("refund AND replacement on the same order")
    if writes > s.max_write_actions_per_case:
        v.append(f"{writes} write actions > limit {s.max_write_actions_per_case}")
    if cls.exception_type.value == "UNKNOWN":
        approve.append("unrecognised exception type")
    if cls.confidence < s.min_auto_confidence:
        approve.append(f"classification confidence {cls.confidence:.2f} < {s.min_auto_confidence}")
    if plan.confidence < s.min_auto_confidence:
        approve.append(f"plan confidence {plan.confidence:.2f} < {s.min_auto_confidence}")

    return GuardrailVerdict(allowed=not v, requires_approval=bool(approve), violations=v, approval_reasons=approve)
