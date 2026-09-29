"""Tool catalog exposed to agents.

Each tool declares: read vs write, a risk level, whether it is reversible, and how to compensate it.
The guardrail layer and the executor both rely on this metadata, so agents never touch raw APIs.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from exops.models import ActionResult, PlannedAction, RiskLevel
from exops.tools.backends import COURIERS, World


@dataclass
class Tool:
    name: str
    description: str
    params: dict[str, str]
    write: bool
    risk: RiskLevel
    reversible: bool
    fn: Callable[..., dict[str, Any]]
    compensate: Callable[[dict[str, Any], dict[str, Any]], PlannedAction | None] | None = None
    tags: list[str] = field(default_factory=list)

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "params": self.params,
            "write": self.write,
            "risk": self.risk.value,
            "reversible": self.reversible,
        }


def build_registry(world: World) -> dict[str, Tool]:
    tools = [
        # ---------------- read tools (safe, used for investigation)
        Tool("get_order", "Fetch order header: status, amount, courier, promise.", {"order_id": "str"},
             False, RiskLevel.LOW, True, world.get_order),
        Tool("get_payment_ledger", "Captures/refunds for the order and expected refund amount.", {"order_id": "str"},
             False, RiskLevel.LOW, True, world.get_payment_ledger),
        Tool("get_tracking", "Courier tracking: last scan age, hub, ETA.", {"order_id": "str"},
             False, RiskLevel.LOW, True, world.get_tracking),
        Tool("get_risk_profile", "Address quality score, COD history, past RTOs.", {"order_id": "str"},
             False, RiskLevel.LOW, True, world.get_risk_profile),
        Tool("check_inventory", "Whether the SKU can still be fulfilled.", {"order_id": "str"},
             False, RiskLevel.LOW, True, world.check_inventory),
        # ---------------- write tools
        Tool("notify_customer", "Send a templated customer notification.",
             {"order_id": "str", "template": "str"}, True, RiskLevel.LOW, False, world.notify_customer),
        Tool("raise_courier_ticket", "Open an escalation ticket with the courier partner.",
             {"order_id": "str", "issue": "str"}, True, RiskLevel.LOW, False, world.raise_courier_ticket),
        Tool("request_address_verification", "Ask customer to confirm/correct address via IVR/WhatsApp.",
             {"order_id": "str"}, True, RiskLevel.LOW, False, world.request_address_verification),
        Tool("expedite_shipment", "Upgrade to express lane (costs money).", {"order_id": "str"},
             True, RiskLevel.MEDIUM, True, world.expedite_shipment,
             compensate=lambda a, o: PlannedAction(tool="revert_expedite", args={"order_id": a["order_id"]})),
        Tool("reassign_courier", f"Move shipment to another courier. One of {COURIERS}.",
             {"order_id": "str", "courier": "str"}, True, RiskLevel.MEDIUM, True, world.reassign_courier,
             compensate=lambda a, o: PlannedAction(
                 tool="reassign_courier", args={"order_id": a["order_id"], "courier": o.get("previous_courier")})),
        Tool("hold_shipment", "Hold dispatch until address/payment is verified.", {"order_id": "str"},
             True, RiskLevel.MEDIUM, True, world.hold_shipment,
             compensate=lambda a, o: PlannedAction(tool="release_hold", args={"order_id": a["order_id"]})),
        Tool("reconcile_payment", "Confirm an order whose payment was captured but order stuck.",
             {"order_id": "str"}, True, RiskLevel.MEDIUM, True, world.reconcile_payment,
             compensate=lambda a, o: PlannedAction(
                 tool="set_order_status", args={"order_id": a["order_id"], "status": o.get("previous_status")})),
        Tool("initiate_refund", "Refund money to the customer. IRREVERSIBLE.",
             {"order_id": "str", "amount": "float", "reason": "str"}, True, RiskLevel.HIGH, False,
             world.initiate_refund),
        Tool("create_replacement", "Create a free replacement order (lost shipment).", {"order_id": "str"},
             True, RiskLevel.HIGH, True, world.create_replacement,
             compensate=lambda a, o: PlannedAction(tool="cancel_replacement", args={"order_id": a["order_id"]})),
        # ---------------- compensation-only tools (not offered to the planner)
        Tool("revert_expedite", "internal", {"order_id": "str"}, True, RiskLevel.LOW, True,
             world.revert_expedite, tags=["internal"]),
        Tool("release_hold", "internal", {"order_id": "str"}, True, RiskLevel.LOW, True,
             world.release_hold, tags=["internal"]),
        Tool("set_order_status", "internal", {"order_id": "str", "status": "str"}, True, RiskLevel.LOW, True,
             world.set_order_status, tags=["internal"]),
        Tool("cancel_replacement", "internal", {"order_id": "str"}, True, RiskLevel.LOW, True,
             world.cancel_replacement, tags=["internal"]),
    ]
    return {t.name: t for t in tools}


def planner_catalog(registry: dict[str, Tool]) -> list[dict[str, Any]]:
    return [t.spec() for t in registry.values() if t.write and "internal" not in t.tags]


def run_tool(registry: dict[str, Tool], action: PlannedAction) -> ActionResult:
    tool = registry.get(action.tool)
    if tool is None:
        return ActionResult(tool=action.tool, args=action.args, ok=False, output={"error": "unknown tool"})
    try:
        out = tool.fn(**action.args)
        comp = tool.compensate(action.args, out) if tool.compensate else None
        return ActionResult(tool=action.tool, args=action.args, ok=True, output=out, compensation=comp)
    except Exception as e:  # noqa: BLE001 - tool errors are data for the agent, not crashes
        return ActionResult(tool=action.tool, args=action.args, ok=False, output={"error": str(e)})
