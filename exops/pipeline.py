"""Orchestrator: event -> classify -> investigate -> plan -> guardrails -> execute | approve queue.

Execution follows the saga pattern: each write records its compensating action; if a later step
fails, completed steps are compensated in reverse order.
"""
from __future__ import annotations

import time
from typing import Any

from exops.agents.agents import ClassifierAgent, InvestigatorAgent, PlannerAgent
from exops.audit import AuditLog
from exops.config import Settings, get_settings
from exops.guardrails import policy
from exops.llm.router import BudgetExceeded, ModelRouter
from exops.models import ActionResult, CaseRecord, ExceptionEvent, Status
from exops.rag.retriever import SOPRetriever
from exops.tools.backends import World
from exops.tools.registry import build_registry, run_tool


class Platform:
    def __init__(self, world: World, settings: Settings | None = None, audit_path: str = ":memory:"):
        self.s = settings or get_settings()
        self.world = world
        self.registry = build_registry(world)
        self.router = ModelRouter(self.s)
        self.retriever = SOPRetriever.from_dir()
        self.classifier = ClassifierAgent(self.router)
        self.investigator = InvestigatorAgent(self.registry)
        self.planner = PlannerAgent(self.router, self.retriever, self.registry)
        self.audit = AuditLog(audit_path)
        self.cases: dict[str, CaseRecord] = {}

    # ------------------------------------------------------------------ main flow
    def process(self, ev: ExceptionEvent) -> CaseRecord:
        case = CaseRecord(event=ev)
        self.cases[case.case_id] = case
        self.audit.record(case.case_id, "received", "system", ev.model_dump())
        try:
            order = self.world.orders.get(ev.order_id)
            amount = order.amount if order else 0.0

            cls, esc = self.classifier.run(ev, case.usage, amount)
            case.classification = cls
            if esc:
                case.escalations.append("classify")
            self.audit.record(case.case_id, "classified", cls.model, cls.model_dump())

            case.observations = self.investigator.run(ev, cls.exception_type)
            self.audit.record(case.case_id, "investigated", "investigator", {"reads": list(case.observations)})

            plan, esc = self.planner.run(ev, cls, case.observations, case.usage)
            case.plan = plan
            if esc:
                case.escalations.append("plan")
            self.audit.record(case.case_id, "planned", plan.model, plan.model_dump())

            verdict = policy.evaluate(plan, cls, self.registry, case.observations, self.s)
            case.verdict = verdict
            self.audit.record(case.case_id, "guardrails", "policy", verdict.model_dump())
        except BudgetExceeded as e:
            case.status = Status.ESCALATED
            case.notes.append(str(e))
            self.audit.record(case.case_id, "escalated", "budget", {"reason": str(e)})
            return case

        if not verdict.allowed:
            case.status = Status.ESCALATED
            case.notes.append("blocked by guardrails: " + "; ".join(verdict.violations))
            self.audit.record(case.case_id, "escalated", "policy", {"violations": verdict.violations})
        elif verdict.requires_approval:
            case.status = Status.PENDING_APPROVAL
            self.audit.record(case.case_id, "queued_for_approval", "policy", {"reasons": verdict.approval_reasons})
        else:
            self._execute(case, actor="agent")
            if case.status == Status.NEW:
                case.status = Status.AUTO_RESOLVED
        return case

    # ------------------------------------------------------------------ human in the loop
    def pending(self) -> list[CaseRecord]:
        return [c for c in self.cases.values() if c.status == Status.PENDING_APPROVAL]

    def approve(self, case_id: str, approver: str) -> CaseRecord:
        case = self.cases[case_id]
        if case.status != Status.PENDING_APPROVAL:
            raise ValueError(f"{case_id} is {case.status}, not pending approval")
        self.audit.record(case_id, "approved", approver, {})
        self._execute(case, actor=approver)
        if case.status == Status.PENDING_APPROVAL:
            case.status = Status.APPROVED_RESOLVED
        return case

    def reject(self, case_id: str, approver: str, reason: str = "") -> CaseRecord:
        case = self.cases[case_id]
        case.status = Status.REJECTED
        case.resolved_at = time.time()
        self.audit.record(case_id, "rejected", approver, {"reason": reason})
        return case

    def rollback(self, case_id: str, actor: str = "operator") -> CaseRecord:
        case = self.cases[case_id]
        self._compensate(case, [r for r in case.results if r.ok], actor)
        case.status = Status.ROLLED_BACK
        return case

    # ------------------------------------------------------------------ internals
    def _execute(self, case: CaseRecord, actor: str) -> None:
        assert case.plan is not None
        done: list[ActionResult] = []
        for a in case.plan.actions:
            r = run_tool(self.registry, a)
            case.results.append(r)
            self.audit.record(case.case_id, "action", actor, r.model_dump())
            if not r.ok:
                self._compensate(case, done, "saga")
                case.status = Status.ROLLED_BACK if done else Status.FAILED
                case.notes.append(f"{a.tool} failed: {r.output.get('error')}")
                case.resolved_at = time.time()
                return
            done.append(r)
        case.resolved_at = time.time()

    def _compensate(self, case: CaseRecord, done: list[ActionResult], actor: str) -> None:
        for r in reversed(done):
            if r.compensation:
                cr = run_tool(self.registry, r.compensation)
                self.audit.record(case.case_id, "compensated", actor, cr.model_dump())

    # ------------------------------------------------------------------ reporting
    def summary(self) -> dict[str, Any]:
        cs = list(self.cases.values())
        n = len(cs) or 1
        by: dict[str, int] = {}
        for c in cs:
            by[c.status.value] = by.get(c.status.value, 0) + 1
        auto = by.get("AUTO_RESOLVED", 0)
        lat = sorted(c.usage.latency_ms for c in cs) or [0]
        return {
            "cases": len(cs),
            "by_status": by,
            "auto_resolution_rate": round(auto / n, 3),
            "pending_approval": by.get("PENDING_APPROVAL", 0),
            "total_llm_cost_inr": round(sum(c.usage.cost_inr for c in cs), 2),
            "avg_cost_inr": round(sum(c.usage.cost_inr for c in cs) / n, 4),
            "p50_latency_ms": lat[len(lat) // 2],
            "p95_latency_ms": lat[min(len(lat) - 1, int(len(lat) * 0.95))],
            "escalation_rate": round(sum(1 for c in cs if c.escalations) / n, 3),
        }
