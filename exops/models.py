"""Core domain model for the Order Exception Intelligence Platform."""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ExceptionType(str, Enum):
    SHIPMENT_STUCK = "SHIPMENT_STUCK"
    PAYMENT_ORDER_MISMATCH = "PAYMENT_ORDER_MISMATCH"  # money captured, order not confirmed
    DUPLICATE_CHARGE = "DUPLICATE_CHARGE"
    REFUND_MISMATCH = "REFUND_MISMATCH"
    RTO_RISK = "RTO_RISK"  # return-to-origin risk (bad address / risky COD)
    SLA_BREACH_RISK = "SLA_BREACH_RISK"
    UNKNOWN = "UNKNOWN"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Status(str, Enum):
    NEW = "NEW"
    AUTO_RESOLVED = "AUTO_RESOLVED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED_RESOLVED = "APPROVED_RESOLVED"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class Order(BaseModel):
    order_id: str
    customer_id: str
    customer_phone: str
    address: str
    pincode: str
    amount: float
    payment_mode: str  # PREPAID | COD
    status: str  # CREATED | CONFIRMED | SHIPPED | IN_TRANSIT | DELIVERED | CANCELLED | PAYMENT_PENDING
    courier: str | None = None
    awb: str | None = None
    promised_by_hours: float = 72.0  # hours from now until promised delivery
    created_hours_ago: float = 0.0


class ExceptionEvent(BaseModel):
    """A raw signal emitted by upstream monitors (OMS, payments, logistics)."""

    event_id: str = Field(default_factory=lambda: f"EXC-{uuid.uuid4().hex[:8].upper()}")
    order_id: str
    source: str  # oms | payments | logistics | risk
    description: str
    signals: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)


class Classification(BaseModel):
    exception_type: ExceptionType
    confidence: float
    severity: RiskLevel
    rationale: str
    model: str


class PlannedAction(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class Plan(BaseModel):
    actions: list[PlannedAction]
    confidence: float
    rationale: str
    cited_sops: list[str] = Field(default_factory=list)
    model: str


class GuardrailVerdict(BaseModel):
    allowed: bool
    requires_approval: bool
    violations: list[str] = Field(default_factory=list)
    approval_reasons: list[str] = Field(default_factory=list)


class ActionResult(BaseModel):
    tool: str
    args: dict[str, Any]
    ok: bool
    output: dict[str, Any] = Field(default_factory=dict)
    compensation: PlannedAction | None = None


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_inr: float = 0.0
    latency_ms: float = 0.0
    calls: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost_inr += other.cost_inr
        self.latency_ms += other.latency_ms
        self.calls += other.calls


class CaseRecord(BaseModel):
    """Everything the platform knows about one exception, end to end."""

    case_id: str = Field(default_factory=lambda: f"CASE-{uuid.uuid4().hex[:8].upper()}")
    event: ExceptionEvent
    status: Status = Status.NEW
    classification: Classification | None = None
    observations: dict[str, Any] = Field(default_factory=dict)
    plan: Plan | None = None
    verdict: GuardrailVerdict | None = None
    results: list[ActionResult] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    escalations: list[str] = Field(default_factory=list)  # model-router escalations
    started_at: float = Field(default_factory=time.time)
    resolved_at: float | None = None
    notes: list[str] = Field(default_factory=list)
