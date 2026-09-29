"""Terminal demo.

    python -m exops.cli --n 25            # process 25 synthetic exceptions
    python -m exops.cli --n 25 --approve  # also approve the human queue
"""
from __future__ import annotations

import argparse
import json

from exops.data.synth import generate
from exops.pipeline import Platform


def main() -> None:
    ap = argparse.ArgumentParser(description="Order Exception Intelligence Platform demo")
    ap.add_argument("--n", type=int, default=25)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--approve", action="store_true", help="approve everything in the human queue")
    a = ap.parse_args()

    ds = generate(n=a.n, seed=a.seed)
    p = Platform(ds.world)
    print(f"{'EVENT':<11} {'TYPE':<23} {'CONF':>5}  {'STATUS':<17} ACTIONS")
    for s in ds.samples:
        c = p.process(s.event)
        acts = ", ".join(x.tool for x in c.plan.actions) if c.plan else "-"
        conf = f"{c.classification.confidence:.2f}" if c.classification else "-"
        t = c.classification.exception_type.value if c.classification else "-"
        esc = " ^" if c.escalations else ""
        print(f"{s.event.event_id:<11} {t:<23} {conf:>5}  {c.status.value:<17} {acts}{esc}")
    print("\n(^ = routed from small to large model)\n")

    q = p.pending()
    print(f"Human approval queue: {len(q)}")
    for c in q:
        print(f"  {c.case_id} {c.event.order_id}: {'; '.join(c.verdict.approval_reasons)}")
        if a.approve:
            p.approve(c.case_id, "ops-lead")
    print("\nSummary:", json.dumps(p.summary(), indent=2))


if __name__ == "__main__":
    main()
