"""Benchmark harness for the gateway.

Reads scenarios from scenarios/*.json (see the shape below) and runs
each one through the gateway twice - gated (enforce=True) and ungated
(enforce=False).

IMPORTANT SCOPE NOTE: `steps` are explicit, pre-scripted tool calls, not
the output of an LLM agent deciding what to do after reading a poisoned
note. This harness measures whether the GATEWAY enforces policy given a
tool-call attempt - it does not measure whether a real agent can be
talked into making that attempt in the first place. Those are different
questions; only the first one is answered here.

Reports, separately:
  - policy evaluation latency (t_policy: evaluate() only, no I/O)
  - gated call latency, gateway-side (t_total: the gateway's own
    contacts-lookup + policy decision + upstream EHR call - this
    explicitly EXCLUDES the agent<->gateway transport leg, which
    happens outside GatewaySession.invoke() and so cannot be measured
    from here; do not call this "end-to-end")
  - attack block rate (BLOCK only)
  - attack caught rate (BLOCK or FLAG)
  - false-positive rate on benign cases
  - ungated attack success rate
  - per-class breakdown
  - which specific scenarios returned FLAG rather than BLOCK, and which
    soft check fired (as of 2026-09-05, provenance_taint is a hard
    check, so this list should be empty - see evaluator.py)

Scenario file shape:
    {
      "id": str, "class": str, "expected": "ALLOW"|"FLAG"|"BLOCK",
      "passport": {"subject", "purpose", "patient_id"},
      "user_message": str,
      "poisoned_field": {"patient", "field", "text"} | null,
      "steps": [{"tool": str, "arguments": dict}, ...]
    }
`steps` drives execution (there is no LLM here to infer intent from
`user_message`); the *last* step is the one being judged against
`expected`, earlier steps exist only to load context (e.g. read a
poisoned note) the way a real multi-turn agent session would.

Run with: python3 benchmark.py
"""
from __future__ import annotations

import asyncio
import json
import statistics
from pathlib import Path

from ehr_server import apply_poison, build_patient_db
from fixtures import ISSUER_PUBLIC_KEY, passport_from_spec
from gateway import connect_stack, invoke_via_mcp

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


def load_scenarios() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(SCENARIOS_DIR.glob("*.json"))]


def _action_succeeded(tool_name: str, result: dict | None) -> bool:
    """Whether the underlying EHR action actually executed/exfiltrated."""
    if result is None:
        return False
    if tool_name in ("send_sms", "send_email"):
        return result.get("status") == "sent"
    if tool_name == "update_billing_code":
        return result.get("status") == "updated"
    return True  # any populated read result counts as a successful access


async def run_scenario(scenario: dict, *, enforce: bool) -> dict:
    patients = build_patient_db()
    apply_poison(patients, scenario.get("poisoned_field"))
    passport = passport_from_spec(scenario["passport"])

    step_records = []
    async with connect_stack(patients, ISSUER_PUBLIC_KEY, enforce=enforce) as (agent_client, _session):
        for step in scenario["steps"]:
            outcome = await invoke_via_mcp(agent_client, step["tool"], step["arguments"], passport)
            step_records.append({"tool": step["tool"], **outcome})

    final = step_records[-1]
    return {
        "id": scenario["id"],
        "class": scenario["class"],
        "expected": scenario["expected"],
        "final_verdict": final["verdict"],
        "final_tool": final["tool"],
        "final_reasons": final["reasons"],
        "final_soft_checks": final["soft_checks"],
        "action_succeeded": _action_succeeded(final["tool"], final["result"]),
        # Straight from the Decision object (gateway.py) - not
        # re-measured here, so there is exactly one source of truth for
        # each number. t_total is gateway-side only; see module docstring.
        "t_policy_ms": [s["t_policy_ms"] for s in step_records],
        "t_total_ms": [s["t_total_ms"] for s in step_records],
    }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def _fmt_rate(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "n/a"
    return f"{numerator}/{denominator} ({100 * numerator / denominator:.1f}%)"


async def main() -> None:
    scenarios = load_scenarios()
    gated = [await run_scenario(s, enforce=True) for s in scenarios]
    ungated = [await run_scenario(s, enforce=False) for s in scenarios]

    benign = [r for r in gated if r["class"] == "benign"]
    attacks = [r for r in gated if r["class"] != "benign"]
    ungated_attacks = [r for r in ungated if r["class"] != "benign"]

    t_policy = [ms for r in gated for ms in r["t_policy_ms"]]
    t_total = [ms for r in gated for ms in r["t_total_ms"]]

    blocked = [r for r in attacks if r["final_verdict"] == "BLOCK"]
    caught = [r for r in attacks if r["final_verdict"] in ("BLOCK", "FLAG")]
    flagged = [r for r in attacks if r["final_verdict"] == "FLAG"]
    false_positives = [r for r in benign if r["final_verdict"] != "ALLOW"]
    ungated_successes = [r for r in ungated_attacks if r["action_succeeded"]]

    print("=" * 78)
    print("AarogyaRakshak gateway benchmark")
    print("=" * 78)
    print(f"{len(scenarios)} cases: {len(benign)} benign, {len(attacks)} attacks across five classes")
    print("SCOPE: measures gateway enforcement of scripted tool-call attempts,")
    print("not whether an LLM agent can be talked into attempting them.")
    print()

    print("-- latency (gated calls only; ungated skips the evaluator) --")
    print(f"policy evaluation                median={statistics.median(t_policy):.3f}ms   p95={_percentile(t_policy, 0.95):.3f}ms   n={len(t_policy)}")
    print(f"gated call latency (gateway-side) median={statistics.median(t_total):.3f}ms   p95={_percentile(t_total, 0.95):.3f}ms   n={len(t_total)}")
    print("  policy evaluation                = evaluate() only, no I/O in the timed region.")
    print("  gated call latency (gateway-side) = contacts lookup + policy decision + upstream")
    print("    EHR call, measured inside the gateway process. This EXCLUDES the agent<->gateway")
    print("    transport leg (today the gateway is a Python object the harness imports directly,")
    print("    not yet a standalone process an agent connects to over the wire - see gateway_server.py")
    print("    for the standalone entrypoint). Do not call this number \"end-to-end\".")
    print()

    print("-- detection --")
    print(f"attack block rate (BLOCK only):      {_fmt_rate(len(blocked), len(attacks))}")
    print(f"attack caught rate (BLOCK or FLAG):  {_fmt_rate(len(caught), len(attacks))}")
    print(f"false-positive rate (benign cases):  {_fmt_rate(len(false_positives), len(benign))}")
    print(f"UNGATED attack success rate:         {_fmt_rate(len(ungated_successes), len(ungated_attacks))}")
    print("  (enforce=False bypasses the evaluator entirely - this is what the")
    print("   same attack steps do with no gateway in front of the EHR at all)")
    print()

    print("-- scenarios that returned FLAG, not BLOCK --")
    if flagged:
        for r in flagged:
            checks = ", ".join(r["final_soft_checks"]) or "(none recorded)"
            print(f"  {r['id']:10s} class={r['class']:20s} soft check(s) fired: {checks}")
            for reason in r["final_reasons"]:
                print(f"      reason: {reason}")
    else:
        print("  none")
    print()

    print("-" * 88)
    print(f"{'class':22s} {'n':>3s} {'accuracy':>10s} {'blocked':>9s} {'flagged':>9s} {'allowed':>9s} {'ungated_success':>16s}")
    print("-" * 88)
    classes = sorted({r["class"] for r in gated})
    for cls in classes:
        rows = [r for r in gated if r["class"] == cls]
        urows = [r for r in ungated if r["class"] == cls]
        acc = sum(1 for r in rows if r["final_verdict"] == r["expected"]) / len(rows)
        n_block = sum(1 for r in rows if r["final_verdict"] == "BLOCK")
        n_flag = sum(1 for r in rows if r["final_verdict"] == "FLAG")
        n_allow = sum(1 for r in rows if r["final_verdict"] == "ALLOW")
        u_success = sum(1 for r in urows if r["action_succeeded"]) / len(urows) if urows else 0.0
        print(f"{cls:22s} {len(rows):3d} {100*acc:9.1f}% {n_block:9d} {n_flag:9d} {n_allow:9d} {100*u_success:15.1f}%")
    print("-" * 88)

    matched_expected = [r for r in gated if r["final_verdict"] == r["expected"]]
    print()
    print(f"overall verdict accuracy: {_fmt_rate(len(matched_expected), len(gated))}")
    mismatches = [r for r in gated if r["final_verdict"] != r["expected"]]
    if mismatches:
        print("MISMATCHES:")
        for r in mismatches:
            print(f"  {r['id']:10s} expected={r['expected']:6s} got={r['final_verdict']}")


if __name__ == "__main__":
    asyncio.run(main())
