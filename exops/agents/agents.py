"""The three agents: Classifier -> Investigator -> Planner.

Design choice ("constrained agency"): the LLM chooses *what* to do, but investigation reads are
driven by an explicit per-type playbook and every write goes through guardrails. That keeps the
system auditable and cheap, and makes behaviour testable.
"""
from __future__ import annotations

import json
from typing import Any

from exops.guardrails.policy import mask_pii
from exops.llm.base import LLMRequest
from exops.llm.router import ModelRouter
from exops.models import Classification, ExceptionEvent, ExceptionType, Plan, PlannedAction, RiskLevel, Usage
from exops.rag.retriever import SOPRetriever
from exops.tools.registry import Tool, planner_catalog, run_tool

CLASSIFY_SYSTEM = """You are the triage agent of an e-commerce order-exception platform.
Classify the exception into exactly one of:
SHIPMENT_STUCK, PAYMENT_ORDER_MISMATCH, DUPLICATE_CHARGE, REFUND_MISMATCH, RTO_RISK, SLA_BREACH_RISK, UNKNOWN.
Rules of thumb: two or more payment captures => DUPLICATE_CHARGE; last scan >= 48h => SHIPMENT_STUCK
(unless it is a COD address-risk case); ETA later than promise with a recent scan => SLA_BREACH_RISK.
Return ONLY JSON: {"exception_type": str, "confidence": float 0-1, "rationale": str}"""

PLAN_SYSTEM = """You are the resolution planner of an e-commerce order-exception platform.
Follow the retrieved SOPs exactly. Use only the write tools in the catalog. At most 3 actions.
Compute monetary amounts from the observations; never invent numbers.
Return ONLY JSON: {"actions": [{"tool": str, "args": {...}, "reason": str}],
"confidence": float 0-1, "cited_sops": [sop ids], "rationale": str}"""

# per-type investigation playbook (read tools)
INVESTIGATE: dict[str, list[str]] = {
    "SHIPMENT_STUCK": ["get_order", "get_tracking"],
    "SLA_BREACH_RISK": ["get_order", "get_tracking"],
    "PAYMENT_ORDER_MISMATCH": ["get_order", "get_payment_ledger", "check_inventory"],
    "DUPLICATE_CHARGE": ["get_order", "get_payment_ledger"],
    "REFUND_MISMATCH": ["get_order", "get_payment_ledger"],
    "RTO_RISK": ["get_order", "get_risk_profile"],
    "UNKNOWN": ["get_order", "get_payment_ledger", "get_tracking", "get_risk_profile"],
}


def _severity(et: ExceptionType, amount: float) -> RiskLevel:
    if et in (ExceptionType.DUPLICATE_CHARGE, ExceptionType.REFUND_MISMATCH, ExceptionType.PAYMENT_ORDER_MISMATCH):
        return RiskLevel.HIGH if amount > 5000 else RiskLevel.MEDIUM
    if et == ExceptionType.SHIPMENT_STUCK:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


class ClassifierAgent:
    def __init__(self, router: ModelRouter):
        self.router = router

    def run(self, ev: ExceptionEvent, usage: Usage, amount: float = 0.0) -> tuple[Classification, bool]:
        safe = mask_pii({"description": ev.description, "source": ev.source, "signals": ev.signals})
        req = LLMRequest("classify", CLASSIFY_SYSTEM, f"Exception event:\n{json.dumps(safe, indent=2)}",
                         context={"event_id": ev.event_id, "signals": ev.signals}, max_tokens=300)
        rr = self.router.run(req, usage)
        d = rr.result.data
        try:
            et = ExceptionType(d.get("exception_type", "UNKNOWN"))
        except ValueError:
            et = ExceptionType.UNKNOWN
        return Classification(exception_type=et, confidence=float(d.get("confidence", 0)),
                              severity=_severity(et, amount), rationale=str(d.get("rationale", "")),
                              model=rr.result.model), rr.escalated


class InvestigatorAgent:
    def __init__(self, registry: dict[str, Tool]):
        self.registry = registry

    def run(self, ev: ExceptionEvent, et: ExceptionType) -> dict[str, Any]:
        obs: dict[str, Any] = {}
        for name in INVESTIGATE.get(et.value, INVESTIGATE["UNKNOWN"]):
            r = run_tool(self.registry, PlannedAction(tool=name, args={"order_id": ev.order_id}))
            obs[name] = r.output
        return obs


class PlannerAgent:
    def __init__(self, router: ModelRouter, retriever: SOPRetriever, registry: dict[str, Tool]):
        self.router, self.retriever, self.registry = router, retriever, registry

    def run(self, ev: ExceptionEvent, cls: Classification, obs: dict[str, Any], usage: Usage) -> tuple[Plan, bool]:
        hits = self.retriever.search(f"{cls.exception_type.value} {ev.description}", k=2)
        sops = "\n\n".join(h.doc.text for h in hits)
        prompt = (
            f"Exception type: {cls.exception_type.value}\nOrder: {ev.order_id}\n\n"
            f"Observations:\n{json.dumps(mask_pii(obs), indent=2)}\n\n"
            f"Retrieved SOPs:\n{sops}\n\nWrite-tool catalog:\n{json.dumps(planner_catalog(self.registry), indent=2)}"
        )
        req = LLMRequest("plan", PLAN_SYSTEM, prompt, max_tokens=700,
                         context={"event_id": ev.event_id, "order_id": ev.order_id,
                                  "exception_type": cls.exception_type.value, "observations": obs,
                                  "sop_ids": [h.doc.doc_id for h in hits]})
        rr = self.router.run(req, usage)
        d = rr.result.data
        actions = [PlannedAction(**a) for a in d.get("actions", [])]
        return Plan(actions=actions, confidence=float(d.get("confidence", 0)), rationale=str(d.get("rationale", "")),
                    cited_sops=list(d.get("cited_sops", [])), model=rr.result.model), rr.escalated
