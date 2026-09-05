"""The enforcement gateway: an MCP server facing the agent, an MCP client
facing the EHR.

Every call the agent makes is evaluated by evaluator.py before it is
(maybe) forwarded to the real EHR tool; every EHR response is labelled
by provenance.py before it flows back into the agent's context. The
agent's ClientSession only ever holds a handle to the gateway's streams
- it has no reference to the EHR's session, so it has no route to the
EHR except through here.

`enforce=False` skips the evaluator entirely (every call is forwarded
as ALLOW) - it exists only to produce the ungated baseline the benchmark
compares against.
"""
from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import anyio
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from mcp import ClientSession
from mcp.server.mcpserver.server import MCPServer
from mcp.shared.memory import create_client_server_memory_streams

from audit import AuditLog
from ehr_server import create_ehr_server
from evaluator import EvaluationResult, Verdict, evaluate
from passport import Passport
from policy import TOOL_EGRESS_PARAM
from provenance import ProvenanceStore, label_response


@dataclass
class Decision:
    """The gateway's verdict on one tool call, plus the three timers that
    together explain where a call's latency actually goes. All three are
    measured on every gated call, never estimated or backfilled:

      t_policy_ms    the evaluate() call only - first check to final
                     verdict, no I/O in the timed region.
      t_upstream_ms  time spent waiting on the EHR over MCP - the
                     forwarded tool call, plus a contacts lookup for
                     egress-capable tools if one was needed.
      t_total_ms     everything this invoke() call did: contacts lookup +
                     policy decision + upstream call. This is what the
                     agent waits on beyond the bare agent<->gateway
                     transport hop (which lives outside the gateway
                     process and isn't visible here).

    t_policy_ms + t_upstream_ms <= t_total_ms; the remainder is
    provenance labelling/recording and audit-log bookkeeping, which are
    pure CPU and normally negligible next to a real network hop.
    """

    verdict: Verdict
    reasons: list[str]
    soft_checks: list[str]
    t_policy_ms: float
    t_upstream_ms: float
    t_total_ms: float


@dataclass
class GatewaySession:
    """One agent conversation's worth of gateway state.

    The provenance store accumulates free-text chunks across calls the
    same way an agent's context window would - that's what lets an
    indirect-injection scenario "read the poisoned note" in one call and
    have it matter on the next.
    """

    ehr: ClientSession
    issuer_public_key: Ed25519PublicKey
    enforce: bool = True
    audit_log: AuditLog = field(default_factory=AuditLog)
    provenance: ProvenanceStore = field(default_factory=ProvenanceStore)

    async def invoke(self, tool_name: str, arguments: dict, passport: Passport) -> dict:
        """Run one agent-issued tool call through the gateway.

        Returns {"verdict", "reasons", "result", "t_policy_ms",
        "t_upstream_ms", "t_total_ms"}; `result` is populated for ALLOW
        and FLAG, and is None for BLOCK.
        """
        total_start = time.perf_counter()
        t_policy_ms = 0.0
        t_upstream_ms = 0.0

        if self.enforce:
            on_file_contacts, contacts_upstream_ms = await self._lookup_contacts(tool_name, passport.patient_id)
            t_upstream_ms += contacts_upstream_ms

            eval_start = time.perf_counter()
            evaluation = evaluate(
                passport=passport,
                issuer_public_key=self.issuer_public_key,
                tool_name=tool_name,
                arguments=arguments,
                provenance_store=self.provenance,
                on_file_contacts=on_file_contacts,
            )
            t_policy_ms = (time.perf_counter() - eval_start) * 1000
        else:
            evaluation = EvaluationResult(verdict=Verdict.ALLOW, checks=[])

        result = None
        if evaluation.verdict in (Verdict.ALLOW, Verdict.FLAG):
            result, call_upstream_ms = await self._call_ehr(tool_name, arguments)
            t_upstream_ms += call_upstream_ms

        t_total_ms = (time.perf_counter() - total_start) * 1000

        decision = Decision(
            verdict=evaluation.verdict,
            reasons=evaluation.reasons,
            soft_checks=evaluation.soft_signal_checks,
            t_policy_ms=t_policy_ms,
            t_upstream_ms=t_upstream_ms,
            t_total_ms=t_total_ms,
        )

        self.audit_log.append(
            {
                "subject": passport.subject,
                "purpose": passport.purpose,
                "patient_id": passport.patient_id,
                "tool": tool_name,
                "arguments": arguments,
                "verdict": decision.verdict.value,
                "reasons": decision.reasons,
                "soft_checks": decision.soft_checks,
                "t_policy_ms": decision.t_policy_ms,
                "t_upstream_ms": decision.t_upstream_ms,
                "t_total_ms": decision.t_total_ms,
                "enforced": self.enforce,
            }
        )
        return {
            "verdict": decision.verdict.value,
            "reasons": decision.reasons,
            "soft_checks": decision.soft_checks,
            "result": result,
            "t_policy_ms": decision.t_policy_ms,
            "t_upstream_ms": decision.t_upstream_ms,
            "t_total_ms": decision.t_total_ms,
        }

    async def _lookup_contacts(self, tool_name: str, patient_id: str) -> tuple[dict, float]:
        # Only egress-capable tools need a destination truth to check
        # against - skip the extra EHR round trip otherwise.
        if tool_name not in TOOL_EGRESS_PARAM:
            return {}, 0.0
        try:
            summary, upstream_ms = await self._call_ehr("get_patient_summary", {"patient_id": patient_id})
        except Exception:
            return {}, 0.0
        return {"phone": summary.get("phone"), "email": summary.get("email")}, upstream_ms

    async def _call_ehr(self, tool_name: str, arguments: dict) -> tuple[dict, float]:
        """Returns (payload, t_upstream_ms). Only the MCP call itself is
        timed - JSON decoding and provenance labelling happen after the
        clock stops, since they are not part of "waiting on the EHR".
        """
        start = time.perf_counter()
        call_result = await self.ehr.call_tool(tool_name, arguments)
        upstream_ms = (time.perf_counter() - start) * 1000

        payload = json.loads(call_result.content[0].text)
        labels = label_response(tool_name, payload)
        self.provenance.record(tool_name, labels, payload)
        return payload, upstream_ms


def build_gateway_server(session: GatewaySession) -> MCPServer:
    server = MCPServer("aarogyarakshak-gateway")

    @server.tool()
    async def invoke_ehr_tool(tool_name: str, arguments: dict, passport: dict) -> str:
        result = await session.invoke(tool_name, arguments, Passport.from_dict(passport))
        return json.dumps(result)

    return server


async def _serve(server: MCPServer, streams: tuple) -> None:
    await server._lowlevel_server.run(
        streams[0],
        streams[1],
        server._lowlevel_server.create_initialization_options(),
        raise_exceptions=True,
    )


@asynccontextmanager
async def connect_stack(patients: dict, issuer_public_key: Ed25519PublicKey, enforce: bool = True):
    """Wire up, entirely in-process over anyio memory streams:

        agent ClientSession <-MCP-> gateway MCPServer
                                       |
                                  (evaluator + provenance + audit)
                                       |
                                gateway's EHR ClientSession <-MCP-> EHR MCPServer

    Yields (agent_client, gateway_session). Only the agent_client and the
    session's audit/provenance state are handed back - there is no
    reference to the EHR's streams reachable from the caller.
    """
    ehr_server = create_ehr_server(patients)

    async with create_client_server_memory_streams() as (ehr_client_streams, ehr_server_streams):
        async with create_client_server_memory_streams() as (agent_client_streams, gw_server_streams):
            async with anyio.create_task_group() as tg:
                tg.start_soon(_serve, ehr_server, ehr_server_streams)

                async with ClientSession(*ehr_client_streams) as ehr_client:
                    await ehr_client.initialize()

                    session = GatewaySession(ehr=ehr_client, issuer_public_key=issuer_public_key, enforce=enforce)
                    gw_server = build_gateway_server(session)
                    tg.start_soon(_serve, gw_server, gw_server_streams)

                    async with ClientSession(*agent_client_streams) as agent_client:
                        await agent_client.initialize()
                        yield agent_client, session


async def invoke_via_mcp(agent_client: ClientSession, tool_name: str, arguments: dict, passport: Passport) -> dict:
    """Helper for callers holding only the agent's ClientSession: makes the
    real `tools/call` round trip to the gateway and unwraps the JSON result.
    """
    call_result = await agent_client.call_tool(
        "invoke_ehr_tool", {"tool_name": tool_name, "arguments": arguments, "passport": passport.to_dict()}
    )
    return json.loads(call_result.content[0].text)
