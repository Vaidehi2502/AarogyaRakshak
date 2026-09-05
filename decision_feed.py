"""WebSocket feed of gateway decisions - what the console (A4) builds
its UI against.

`--fake` emits three scripted decision events - one ALLOW, one BLOCK,
one FLAG - on a repeating timer (default 3s), so every UI state the
console needs to render can be built and styled today without waiting
on the rest of the stack. This is the only mode implemented right now;
real mode (streaming actual Decision objects as a running gateway_server.py
produces them) is a separate, larger piece of work and is not started.

Note on the FLAG example: as of 2026-09-05 (see evaluator.py),
provenance_taint was changed from a soft to a hard check, so the real
evaluator currently has no live path that ever produces FLAG - the
scripted FLAG event below exists purely so the console has a fixture to
render that UI state against. It is illustrative of the wire schema, not
a claim that the current backend produces it.

Run with:
    python3 decision_feed.py --fake
Then connect a WebSocket client to ws://127.0.0.1:8765/ws
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

# Field names match gateway.Decision / GatewaySession.invoke()'s return
# shape exactly, so the console can bind to this fixture now and switch
# to the real feed later without reshaping anything.
FAKE_DECISIONS = [
    {
        "id": "FAKE-ALLOW-01", "class": "benign", "tool": "send_reminder",
        "subject": "agent-demo", "purpose": "medication_reminder", "patient_id": "P100",
        "verdict": "ALLOW", "reasons": [], "soft_checks": [],
        "t_policy_ms": 0.17, "t_total_ms": 2.3,
    },
    {
        "id": "FAKE-BLOCK-01", "class": "scope_escalation", "tool": "send_reminder",
        "subject": "agent-demo", "purpose": "medication_reminder", "patient_id": "P100",
        "verdict": "BLOCK",
        "reasons": ["passport scoped to patient 'P100', request targets 'P101'"],
        "soft_checks": [],
        "t_policy_ms": 0.18, "t_total_ms": 2.1,
    },
    {
        "id": "FAKE-FLAG-01", "class": "indirect_injection", "tool": "send_sms",
        "subject": "agent-demo", "purpose": "care_coordination", "patient_id": "P105",
        "verdict": "FLAG",
        "reasons": ["parameter(s) ['to'] match content the agent previously read from untrusted free text"],
        "soft_checks": ["provenance_taint"],
        "t_policy_ms": 0.19, "t_total_ms": 2.6,
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

    return Starlette(routes=[WebSocketRoute("/ws", ws_endpoint)])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fake", action="store_true", help="emit scripted allow/block/flag events (required for now)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--interval", type=float, default=3.0, help="seconds between scripted events (default 3)")
    args = parser.parse_args()

    if not args.fake:
        parser.error("real mode isn't implemented yet - run with --fake to unblock console development now")

    app = build_fake_app(args.interval)
    print(f"fake decision feed on ws://{args.host}:{args.port}/ws "
          f"(one ALLOW, one BLOCK, one FLAG, every {args.interval}s)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
