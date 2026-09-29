"""FastAPI service + ops dashboard.

    uvicorn exops.api.app:app --reload     ->  http://localhost:8000
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from exops.data.synth import generate
from exops.eval import harness
from exops.guardrails.policy import mask_pii
from exops.models import CaseRecord, ExceptionEvent
from exops.pipeline import Platform

app = FastAPI(title="Order Exception Intelligence Platform", version="0.1.0")
STATE: dict[str, Any] = {}


def _seed(n: int = 60, seed: int = 42) -> Platform:
    ds = generate(n=n, seed=seed)
    p = Platform(ds.world)
    for s in ds.samples:
        p.process(s.event)
    STATE["platform"] = p
    return p


def platform() -> Platform:
    return STATE.get("platform") or _seed()


def _view(c: CaseRecord) -> dict[str, Any]:
    return {
        "case_id": c.case_id, "event_id": c.event.event_id, "order_id": c.event.order_id,
        "description": c.event.description, "status": c.status.value,
        "type": c.classification.exception_type.value if c.classification else None,
        "confidence": c.classification.confidence if c.classification else None,
        "severity": c.classification.severity.value if c.classification else None,
        "actions": [a.model_dump() for a in c.plan.actions] if c.plan else [],
        "cited_sops": c.plan.cited_sops if c.plan else [],
        "approval_reasons": c.verdict.approval_reasons if c.verdict else [],
        "violations": c.verdict.violations if c.verdict else [],
        "models": [m for m in [c.classification.model if c.classification else None,
                               c.plan.model if c.plan else None] if m],
        "cost_inr": round(c.usage.cost_inr, 4), "latency_ms": round(c.usage.latency_ms, 1),
        "notes": c.notes,
    }


class Decision(BaseModel):
    approver: str = "ops-lead"
    reason: str = ""


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return (Path(__file__).parent / "static" / "dashboard.html").read_text()


@app.post("/api/demo/seed")
def seed(n: int = 60, seed: int = 42) -> dict[str, Any]:
    return _seed(n, seed).summary()


@app.post("/api/events")
def ingest(ev: ExceptionEvent) -> dict[str, Any]:
    p = platform()
    if ev.order_id not in p.world.orders:
        raise HTTPException(404, f"unknown order {ev.order_id}")
    return _view(p.process(ev))


@app.get("/api/metrics")
def metrics() -> dict[str, Any]:
    return platform().summary()


@app.get("/api/cases")
def cases(status: str | None = None) -> list[dict[str, Any]]:
    cs = platform().cases.values()
    return [_view(c) for c in cs if status is None or c.status.value == status]


@app.get("/api/cases/{case_id}")
def case(case_id: str) -> dict[str, Any]:
    p = platform()
    if case_id not in p.cases:
        raise HTTPException(404)
    return {**_view(p.cases[case_id]), "observations": mask_pii(p.cases[case_id].observations),
            "audit": p.audit.trail(case_id)}


@app.post("/api/cases/{case_id}/approve")
def approve(case_id: str, d: Decision) -> dict[str, Any]:
    try:
        return _view(platform().approve(case_id, d.approver))
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/cases/{case_id}/reject")
def reject(case_id: str, d: Decision) -> dict[str, Any]:
    return _view(platform().reject(case_id, d.approver, d.reason))


@app.post("/api/cases/{case_id}/rollback")
def rollback(case_id: str, d: Decision) -> dict[str, Any]:
    return _view(platform().rollback(case_id, d.approver))


@app.post("/api/eval")
def run_eval(n: int = 150, seed: int = 11) -> dict[str, Any]:
    rep = harness.run(n=n, seed=seed, judge_sample=30)
    rep.pop("failures", None)
    return rep
