"""A2+A3 scenario audit: for every scenario, which of the eight checks
actually decides the verdict?

The benchmark only asserts verdict == expected. It never asks WHICH check
fired - so an attack can pass its test while being caught by a completely
different mechanism than its class name claims. This script runs all 60
scenarios through the real gateway and buckets each failing scenario by the
check(s) that hard-failed, then prints a per-class table.

Run from the repo root:  python audit_checks.py
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict

from benchmark import load_scenarios, run_scenario

# Map a failing check's reason string (evaluator.py) back to its check name.
# Ordered to match the evaluator's own check order.
_REASON_TO_CHECK = [
    ("signature", ("signature",)),
    ("expiry", ("expired", "issued in the future")),
    ("purpose_known", ("unknown purpose",)),
    ("tool_allowed", ("not permitted under purpose", "no purpose profile")),
    ("patient_scope", ("scoped to patient",)),
    ("payload_class", ("payload class",)),
    ("egress_destination", ("on-file contact", "does not permit egress")),
    ("provenance_taint", ("untrusted free text",)),
]


def classify(reason: str) -> str:
    for check, needles in _REASON_TO_CHECK:
        if any(n in reason for n in needles):
            return check
    return "??? " + reason


async def main() -> None:
    scenarios = load_scenarios()
    gated = [await run_scenario(s, enforce=True) for s in scenarios]

    # deciding check = the FIRST failing check in evaluator order (that is the
    # one the evaluator would name as primary); we also keep every fired check.
    order = [c for c, _ in _REASON_TO_CHECK]
    by_class: dict[str, Counter] = defaultdict(Counter)
    per_scenario: list[tuple] = []

    for r in gated:
        checks_fired = sorted({classify(x) for x in r["final_reasons"]},
                              key=lambda c: order.index(c) if c in order else 99)
        deciding = checks_fired[0] if checks_fired else "(allow)"
        by_class[r["class"]][deciding] += 1
        per_scenario.append((r["id"], r["class"], r["expected"], r["final_verdict"],
                             deciding, ",".join(checks_fired) or "-"))

    print("=" * 82)
    print("SCENARIO AUDIT - which check actually decides each verdict")
    print("=" * 82)
    print(f"{'id':8s} {'class':20s} {'exp':6s} {'got':6s} {'deciding check':20s} all-fired")
    print("-" * 82)
    for row in per_scenario:
        print(f"{row[0]:8s} {row[1]:20s} {row[2]:6s} {row[3]:6s} {row[4]:20s} {row[5]}")

    print()
    print("=" * 82)
    print("PER-CLASS: deciding check distribution")
    print("=" * 82)
    for cls in sorted(by_class):
        dist = ", ".join(f"{k}={v}" for k, v in by_class[cls].most_common())
        print(f"  {cls:22s} {dist}")


if __name__ == "__main__":
    asyncio.run(main())
