"""Synthetic, seeded order-exception generator with ground-truth labels.

Ground truth encodes what the SOPs say *should* happen, so the same generator drives the demo,
the golden eval set, and regression tests.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from exops.models import ExceptionEvent, ExceptionType, Order
from exops.tools.backends import COURIERS, World

AUTO_REFUND_LIMIT = 5000.0


@dataclass
class GroundTruth:
    exception_type: ExceptionType
    expected_actions: set[str]
    needs_approval: bool
    ambiguous: bool = False
    notes: str = ""


@dataclass
class Sample:
    event: ExceptionEvent
    truth: GroundTruth


@dataclass
class Dataset:
    world: World
    samples: list[Sample] = field(default_factory=list)


CITIES = [("Chennai", "600"), ("Bengaluru", "560"), ("Mumbai", "400"), ("Delhi", "110"),
          ("Hyderabad", "500"), ("Coimbatore", "641"), ("Pune", "411"), ("Kolkata", "700")]


def _order(rng: random.Random, i: int, **kw) -> Order:
    city, pin = rng.choice(CITIES)
    base = dict(
        order_id=f"OD{100000 + i}",
        customer_id=f"C{rng.randint(10000, 99999)}",
        customer_phone=f"+91 9{rng.randint(100000000, 999999999)}",
        address=f"{rng.randint(1, 300)}, {rng.choice(['MG Road', 'Anna Salai', 'Main St', '2nd Cross', 'Lake View'])}, {city}",
        pincode=f"{pin}{rng.randint(0, 99):03d}",
        amount=round(rng.choice([rng.uniform(299, 2999), rng.uniform(3000, 12000), rng.uniform(12000, 60000)]), 2),
        payment_mode="PREPAID",
        status="IN_TRANSIT",
        courier=rng.choice(COURIERS),
        awb=f"AWB{rng.randint(10**9, 10**10 - 1)}",
        promised_by_hours=rng.uniform(24, 96),
        created_hours_ago=rng.uniform(24, 200),
    )
    base.update(kw)
    return Order(**base)


def generate(n: int = 200, seed: int = 7, ambiguity_rate: float = 0.2) -> Dataset:
    rng = random.Random(seed)
    world = World()
    ds = Dataset(world=world)
    types = [t for t in ExceptionType if t != ExceptionType.UNKNOWN]

    for i in range(n):
        et = rng.choice(types)
        ambiguous = rng.random() < ambiguity_rate
        o = _order(rng, i)
        oid = o.order_id
        world.ledger[oid] = [{"type": "CAPTURE", "amount": o.amount, "txn_id": f"PG{i}A"}]
        world.inventory[oid] = True
        world.risk[oid] = {"address_score": round(rng.uniform(0.7, 1.0), 2), "prior_rto": 0, "cod": False}
        signals: dict = {}
        expected: set[str] = set()
        approval = False
        notes = ""

        if et == ExceptionType.SHIPMENT_STUCK:
            scan = rng.choice([rng.uniform(60, 110), rng.uniform(125, 240)])
            world.tracking[oid] = {"status": "IN_TRANSIT", "last_scan_hours": round(scan, 1),
                                   "hub": f"{rng.choice(CITIES)[0]} Hub", "eta_hours": round(rng.uniform(24, 72), 1)}
            signals = {"last_scan_hours": round(scan, 1), "order_status": "IN_TRANSIT"}
            desc = f"No tracking scan for {scan:.0f}h on AWB {o.awb} ({o.courier})."
            if scan >= 120:
                expected = {"create_replacement", "notify_customer"}
                approval = True
                notes = "presumed lost -> replacement (HIGH risk)"
            else:
                expected = {"raise_courier_ticket", "notify_customer"}
            if ambiguous:  # also looks like SLA risk
                signals.update({"eta_hours": o.promised_by_hours + rng.uniform(10, 40),
                                "promised_by_hours": round(o.promised_by_hours, 1)})
                desc += " Delivery promise may be missed."

        elif et == ExceptionType.PAYMENT_ORDER_MISMATCH:
            o.status = "PAYMENT_PENDING"
            o.courier, o.awb = None, None
            inv = rng.random() > 0.3
            world.inventory[oid] = inv
            mins = rng.uniform(35, 240)
            signals = {"payment_captured": True, "order_status": "PAYMENT_PENDING",
                       "minutes_since_capture": round(mins), "capture_count": 1}
            desc = f"Payment captured {mins:.0f} min ago but order {oid} still PAYMENT_PENDING."
            if inv:
                expected = {"reconcile_payment", "notify_customer"}
            else:
                expected = {"initiate_refund", "notify_customer"}
                approval = o.amount > AUTO_REFUND_LIMIT
            if ambiguous:
                signals["capture_count"] = 1
                signals["gateway_retry"] = True
                desc += " Gateway shows a retry attempt."

        elif et == ExceptionType.DUPLICATE_CHARGE:
            world.ledger[oid].append({"type": "CAPTURE", "amount": o.amount, "txn_id": f"PG{i}B"})
            signals = {"capture_count": 2, "payment_captured": True, "order_status": o.status}
            desc = f"Two captures of Rs {o.amount:.0f} found for {oid}."
            expected = {"initiate_refund", "notify_customer"}
            approval = o.amount > AUTO_REFUND_LIMIT
            if ambiguous:
                signals["order_status"] = "PAYMENT_PENDING"
                desc = f"Customer reports being charged twice; order {oid} shows PAYMENT_PENDING."

        elif et == ExceptionType.REFUND_MISMATCH:
            o.status = "CANCELLED"
            partial = round(o.amount * rng.uniform(0.3, 0.9), 2)
            world.ledger[oid].append({"type": "REFUND", "amount": partial, "txn_id": f"RF{i}"})
            world.expected_refund[oid] = o.amount
            gap = round(o.amount - partial, 2)
            signals = {"order_status": "CANCELLED", "expected_refund": o.amount, "refunded": partial,
                       "refund_gap": gap}
            desc = f"Cancelled order {oid}: refunded Rs {partial:.0f} of expected Rs {o.amount:.0f}."
            expected = {"initiate_refund", "notify_customer"}
            approval = gap > AUTO_REFUND_LIMIT

        elif et == ExceptionType.RTO_RISK:
            o.status = "CONFIRMED"
            o.payment_mode = "COD"
            world.ledger[oid] = []
            score = rng.choice([rng.uniform(0.1, 0.39), rng.uniform(0.4, 0.6)])
            prior = rng.randint(1, 4)
            world.risk[oid] = {"address_score": round(score, 2), "prior_rto": prior, "cod": True}
            signals = {"address_score": round(score, 2), "prior_rto": prior, "cod": True, "order_status": "CONFIRMED"}
            desc = f"COD order {oid} flagged: address score {score:.2f}, {prior} prior RTOs."
            if score < 0.4:
                expected = {"request_address_verification", "hold_shipment"}
            else:
                expected = {"request_address_verification", "notify_customer"}
            if ambiguous:
                signals["last_scan_hours"] = 50.0
                desc += " Pickup not yet scanned."

        elif et == ExceptionType.SLA_BREACH_RISK:
            eta = o.promised_by_hours + rng.uniform(8, 48)
            otr = round(rng.uniform(0.6, 0.97), 2)
            world.tracking[oid] = {"status": "IN_TRANSIT", "last_scan_hours": round(rng.uniform(1, 20), 1),
                                   "eta_hours": round(eta, 1), "courier_on_time_rate": otr}
            signals = {"eta_hours": round(eta, 1), "promised_by_hours": round(o.promised_by_hours, 1),
                       "courier_on_time_rate": otr, "last_scan_hours": world.tracking[oid]["last_scan_hours"]}
            desc = f"ETA {eta:.0f}h exceeds promise {o.promised_by_hours:.0f}h for {oid} via {o.courier}."
            expected = {"reassign_courier"} if otr < 0.8 else {"expedite_shipment"}
            if eta - o.promised_by_hours > 24:
                expected.add("notify_customer")
            if ambiguous:
                signals["last_scan_hours"] = round(rng.uniform(40, 55), 1)
                desc += " Last scan is getting old."

        world.orders[oid] = o
        ev = ExceptionEvent(event_id=f"EXC-{i:05d}", order_id=oid, source=_source(et), description=desc,
                            signals=signals)
        ds.samples.append(Sample(ev, GroundTruth(et, expected, approval, ambiguous, notes)))
    return ds


def _source(et: ExceptionType) -> str:
    return {
        ExceptionType.SHIPMENT_STUCK: "logistics",
        ExceptionType.SLA_BREACH_RISK: "logistics",
        ExceptionType.PAYMENT_ORDER_MISMATCH: "payments",
        ExceptionType.DUPLICATE_CHARGE: "payments",
        ExceptionType.REFUND_MISMATCH: "payments",
        ExceptionType.RTO_RISK: "risk",
    }.get(et, "oms")
