"""In-memory simulators for the systems the agents act on: OMS, payments, logistics, inventory.

In production these would be thin clients over real service APIs. Keeping them behind the same
interface lets the whole platform (and its evals) run offline and deterministically.
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from typing import Any

from exops.models import Order

COURIERS = ["Ekart", "Delhivery", "BlueDart", "XpressBees", "Shadowfax"]


@dataclass
class World:
    orders: dict[str, Order] = field(default_factory=dict)
    # order_id -> list of ledger entries {"type": "CAPTURE"|"REFUND", "amount": float, "txn_id": str}
    ledger: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # order_id -> tracking info
    tracking: dict[str, dict[str, Any]] = field(default_factory=dict)
    # order_id -> customer / risk profile
    risk: dict[str, dict[str, Any]] = field(default_factory=dict)
    inventory: dict[str, bool] = field(default_factory=dict)
    expected_refund: dict[str, float] = field(default_factory=dict)
    # side-effect logs (what the agents actually did)
    tickets: list[dict[str, Any]] = field(default_factory=list)
    notifications: list[dict[str, Any]] = field(default_factory=list)
    replacements: dict[str, str] = field(default_factory=dict)
    holds: set[str] = field(default_factory=set)
    expedited: set[str] = field(default_factory=set)
    address_checks: set[str] = field(default_factory=set)

    def snapshot(self) -> "World":
        return copy.deepcopy(self)

    # ------------------------------------------------------------------ read APIs
    def get_order(self, order_id: str) -> dict[str, Any]:
        o = self.orders.get(order_id)
        if not o:
            raise KeyError(f"order {order_id} not found")
        return o.model_dump()

    def get_payment_ledger(self, order_id: str) -> dict[str, Any]:
        entries = self.ledger.get(order_id, [])
        captured = sum(e["amount"] for e in entries if e["type"] == "CAPTURE")
        refunded = sum(e["amount"] for e in entries if e["type"] == "REFUND")
        return {
            "entries": entries,
            "captured": round(captured, 2),
            "refunded": round(refunded, 2),
            "capture_count": sum(1 for e in entries if e["type"] == "CAPTURE"),
            "expected_refund": self.expected_refund.get(order_id),
        }

    def get_tracking(self, order_id: str) -> dict[str, Any]:
        return dict(self.tracking.get(order_id, {"status": "NOT_SHIPPED"}))

    def get_risk_profile(self, order_id: str) -> dict[str, Any]:
        return dict(self.risk.get(order_id, {}))

    def check_inventory(self, order_id: str) -> dict[str, Any]:
        return {"available": self.inventory.get(order_id, True)}

    # ------------------------------------------------------------------ write APIs
    def raise_courier_ticket(self, order_id: str, issue: str) -> dict[str, Any]:
        tid = f"TKT-{uuid.uuid4().hex[:6].upper()}"
        self.tickets.append({"ticket_id": tid, "order_id": order_id, "issue": issue})
        return {"ticket_id": tid}

    def notify_customer(self, order_id: str, template: str) -> dict[str, Any]:
        self.notifications.append({"order_id": order_id, "template": template})
        return {"sent": True, "template": template}

    def reassign_courier(self, order_id: str, courier: str) -> dict[str, Any]:
        o = self.orders[order_id]
        if o.status == "DELIVERED":
            raise ValueError("cannot reassign a delivered order")
        prev = o.courier
        o.courier = courier
        return {"previous_courier": prev, "courier": courier}

    def expedite_shipment(self, order_id: str) -> dict[str, Any]:
        self.expedited.add(order_id)
        t = self.tracking.setdefault(order_id, {})
        if "eta_hours" in t:
            t["eta_hours"] = max(12.0, t["eta_hours"] * 0.5)
        return {"expedited": True, "new_eta_hours": t.get("eta_hours")}

    def revert_expedite(self, order_id: str) -> dict[str, Any]:
        self.expedited.discard(order_id)
        return {"expedited": False}

    def initiate_refund(self, order_id: str, amount: float, reason: str) -> dict[str, Any]:
        led = self.get_payment_ledger(order_id)
        refundable = led["captured"] - led["refunded"]
        if amount <= 0 or amount > refundable + 0.01:
            raise ValueError(f"refund {amount} exceeds refundable {refundable}")
        txn = f"RF-{uuid.uuid4().hex[:6].upper()}"
        self.ledger.setdefault(order_id, []).append({"type": "REFUND", "amount": round(amount, 2), "txn_id": txn})
        return {"refund_txn": txn, "amount": round(amount, 2), "reason": reason}

    def reconcile_payment(self, order_id: str) -> dict[str, Any]:
        o = self.orders[order_id]
        if not self.inventory.get(order_id, True):
            raise ValueError("inventory unavailable; cannot confirm order")
        prev = o.status
        o.status = "CONFIRMED"
        return {"previous_status": prev, "status": "CONFIRMED"}

    def set_order_status(self, order_id: str, status: str) -> dict[str, Any]:
        self.orders[order_id].status = status
        return {"status": status}

    def create_replacement(self, order_id: str) -> dict[str, Any]:
        rid = f"R-{order_id}"
        self.replacements[order_id] = rid
        return {"replacement_order_id": rid}

    def cancel_replacement(self, order_id: str) -> dict[str, Any]:
        self.replacements.pop(order_id, None)
        return {"cancelled": True}

    def request_address_verification(self, order_id: str) -> dict[str, Any]:
        self.address_checks.add(order_id)
        return {"verification_requested": True}

    def hold_shipment(self, order_id: str) -> dict[str, Any]:
        self.holds.add(order_id)
        return {"on_hold": True}

    def release_hold(self, order_id: str) -> dict[str, Any]:
        self.holds.discard(order_id)
        return {"on_hold": False}
