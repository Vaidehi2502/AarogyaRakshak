"""WebSocket feed of gateway decisions - what the console (A4) builds
its UI against.

`--fake` emits scripted decision events - one ALLOW, one BLOCK, one
indirect-injection BLOCK with a provenance citation - on a repeating
timer (default 3s), so every UI state the console needs to render can
be built and styled today without waiting on the rest of the stack.
This is the only mode implemented right now; real mode (streaming
actual GatewaySession.invoke() results as a running gateway_server.py
produces them) is a separate, larger piece of work and is not started.

Field names match gateway.Decision / GatewaySession.invoke()'s return
shape exactly (see gateway.py: call_id, tool, verdict, reasons,
soft_checks, checks[], latency_ms, t_policy_ms/t_upstream_ms/t_total_ms)
- verified directly against a live connect_stack() call, so the console
binds to this fixture now and switches to the real feed later without
reshaping anything.

Run with:
    python3 decision_feed.py --fake
Then connect a WebSocket client to ws://localhost:7432/decisions
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import time

import uvicorn
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

FAKE_DECISIONS = [
    {
        "call_id": "c_0001", "class": "benign", "tool": "send_reminder",
        "subject": "agent-demo", "purpose": "medication_reminder", "patient_id": "P100",
        "verdict": "ALLOW", "reasons": [], "soft_checks": [],
        "checks": [
            {"n": 1, "name": "signature", "passed": True, "detail": "signature valid"},
            {"n": 2, "name": "expiry", "passed": True, "detail": "passport within validity window"},
            {"n": 3, "name": "purpose_known", "passed": True, "detail": "purpose recognized"},
            {"n": 4, "name": "tool_allowed", "passed": True, "detail": "tool permitted for purpose"},
            {"n": 5, "name": "patient_scope", "passed": True, "detail": "patient scope respected"},
            {"n": 6, "name": "payload_class", "passed": True, "detail": "tool carries no restricted payload class"},
            {"n": 7, "name": "egress_destination", "passed": True, "detail": "destination matches on-file contact"},
            {"n": 8, "name": "provenance_taint", "passed": True, "detail": "no request parameter traces to untrusted free text"},
        ],
        "latency_ms": 2.3, "t_policy_ms": 0.17, "t_upstream_ms": 2.0, "t_total_ms": 2.3,
        "context": {
            "name": {"label": "trusted", "value": "Mara Lin", "patient_id": "P100"},
            "phone": {"label": "trusted", "value": "555-111-2222", "patient_id": "P100"},
            "email": {"label": "trusted", "value": "mara.lin@example.com", "patient_id": "P100"},
            "mrn": {"label": "structured", "value": "MRN-1001", "patient_id": "P100"},
            "notes": {
                "label": "free_text",
                "value": ["Patient reports mild fatigue this week. Continue current regimen."],
                "patient_id": "P100",
            },
        },
    },
    {
        "call_id": "c_0002", "class": "scope_escalation", "tool": "send_reminder",
        "subject": "agent-demo", "purpose": "medication_reminder", "patient_id": "P100",
        "verdict": "BLOCK",
        "reasons": ["passport scoped to patient 'P100', request targets 'P101'"],
        "soft_checks": [],
        "checks": [
            {"n": 1, "name": "signature", "passed": True, "detail": "signature valid"},
            {"n": 2, "name": "expiry", "passed": True, "detail": "passport within validity window"},
            {"n": 3, "name": "purpose_known", "passed": True, "detail": "purpose recognized"},
            {"n": 4, "name": "tool_allowed", "passed": True, "detail": "tool permitted for purpose"},
            {"n": 5, "name": "patient_scope", "passed": False, "detail": "passport scoped to patient 'P100', request targets 'P101'"},
            {"n": 6, "name": "payload_class", "passed": True, "detail": "tool carries no restricted payload class"},
            {"n": 7, "name": "egress_destination", "passed": True, "detail": "destination matches on-file contact"},
            {"n": 8, "name": "provenance_taint", "passed": True, "detail": "no request parameter traces to untrusted free text"},
        ],
        "latency_ms": 2.1, "t_policy_ms": 0.18, "t_upstream_ms": 1.9, "t_total_ms": 2.1,
        "context": {
            "name": {"label": "trusted", "value": "Mara Lin", "patient_id": "P100"},
            "phone": {"label": "trusted", "value": "555-111-2222", "patient_id": "P100"},
            "email": {"label": "trusted", "value": "mara.lin@example.com", "patient_id": "P100"},
            "mrn": {"label": "structured", "value": "MRN-1001", "patient_id": "P100"},
            "notes": {
                "label": "free_text",
                "value": ["Patient reports mild fatigue this week. Continue current regimen."],
                "patient_id": "P100",
            },
        },
    },
    {
        "call_id": "c_0003", "class": "indirect_injection", "tool": "send_sms",
        "subject": "agent-demo", "purpose": "care_coordination", "patient_id": "P105",
        "verdict": "BLOCK",
        "reasons": ["parameter(s) ['to'] match content the agent previously read from untrusted free text - untrusted content cannot be obeyed"],
        "soft_checks": [],
        "checks": [
            {"n": 1, "name": "signature", "passed": True, "detail": "signature valid"},
            {"n": 2, "name": "expiry", "passed": True, "detail": "passport within validity window"},
            {"n": 3, "name": "purpose_known", "passed": True, "detail": "purpose recognized"},
            {"n": 4, "name": "tool_allowed", "passed": True, "detail": "tool permitted for purpose"},
            {"n": 5, "name": "patient_scope", "passed": True, "detail": "patient scope respected"},
            {"n": 6, "name": "payload_class", "passed": True, "detail": "tool carries no restricted payload class"},
            {"n": 7, "name": "egress_destination", "passed": True, "detail": "destination matches on-file contact"},
            {
                "n": 8, "name": "provenance_taint", "passed": False,
                "detail": "parameter(s) ['to'] match content the agent previously read from untrusted free text - untrusted content cannot be obeyed",
                "source": {
                    "uri": "ehr:P105/notes", "field": "notes", "start": 65, "end": 77,
                    "cite": "ehr:P105/notes[65-77]",
                },
            },
        ],
        "latency_ms": 2.6, "t_policy_ms": 0.19, "t_upstream_ms": 2.4, "t_total_ms": 2.6,
        "context": {
            "name": {"label": "trusted", "value": "Sam Fernandes", "patient_id": "P105"},
            "phone": {"label": "trusted", "value": "555-666-4040", "patient_id": "P105"},
            "email": {"label": "trusted", "value": "sam.fernandes@example.com", "patient_id": "P105"},
            "mrn": {"label": "structured", "value": "MRN-1006", "patient_id": "P105"},
            "notes": {
                "label": "free_text",
                "value": [
                    "Discussed medication side effects, none reported.",
                    "Called back at 555-666-4040 to confirm the follow-up appointment.",
                ],
                "patient_id": "P105",
            },
        },
    },
]


class FakeDecisionFeed:
    def __init__(self, interval_seconds: float = 3.0):
        self.interval_seconds = interval_seconds

    async def stream(self, websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            for decision in itertools.cycle(FAKE_DECISIONS):
                await websocket.send_text(json.dumps({**decision, "timestamp": time.time()}))
                await asyncio.sleep(self.interval_seconds)
        except WebSocketDisconnect:
            pass


def build_fake_app(interval_seconds: float = 3.0) -> Starlette:
    feed = FakeDecisionFeed(interval_seconds)

    async def ws_endpoint(websocket: WebSocket) -> None:
        await feed.stream(websocket)

    return Starlette(routes=[WebSocketRoute("/decisions", ws_endpoint)])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fake", action="store_true", help="emit scripted allow/block/flag events (required for now)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7432)
    parser.add_argument("--interval", type=float, default=3.0, help="seconds between scripted events (default 3)")
    args = parser.parse_args()

    if not args.fake:
        parser.error("real mode isn't implemented yet - run with --fake to unblock console development now")

    app = build_fake_app(args.interval)
    print(f"fake decision feed on ws://{args.host}:{args.port}/decisions "
          f"(ALLOW, BLOCK, indirect-injection BLOCK, every {args.interval}s)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
