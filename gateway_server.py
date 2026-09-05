"""Standalone entrypoint: the gateway as its own OS process.

Everywhere else in this repo (test_scenarios.py, benchmark.py), the
gateway is a Python object the harness constructs in-process via
gateway.connect_stack() over anyio memory streams - convenient for
running 60 scenarios in milliseconds, but it does not demonstrate the
architecture's central claim: the agent has no route to the EHR except
through the gateway. An in-memory object graph a test imports can't
demonstrate that; a real process boundary can.

Run this file directly and it:

  1. Spawns ehr_server.py as its own subprocess over stdio - a genuinely
     separate OS process this script holds the only handle to.
  2. Serves the gateway as an MCP server on ITS OWN stdio - so whatever
     process launches this one (an agent, an MCP client, `mcp dev`, a
     human with `mcp inspector`) gets a session to the gateway and
     nothing else. There is no in-memory shortcut to hand it the EHR's
     streams instead - this process never exposes them; the only thing
     holding a reference to the EHR subprocess's pipes is this process,
     and it only ever forwards individually-evaluated tool calls, never
     the connection itself.

Usage, from an MCP client's perspective:

    stdio command: python3 gateway_server.py

The issuer keypair is persisted at ISSUER_KEY_PATH (generated on first
run if missing) so a separately-run `issue_passport_cli.py` process can
sign passports this gateway process will actually accept - two
independent processes agreeing on trust via a shared key file, the way
an issuer and a gateway would in a real deployment.
"""
from __future__ import annotations

import sys
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.server.stdio import stdio_server

from gateway import GatewaySession, build_gateway_server
from passport import load_or_create_issuer_keypair

ISSUER_KEY_PATH = Path(__file__).parent / ".aarogyarakshak_issuer_key"
EHR_SCRIPT = Path(__file__).parent / "ehr_server.py"


async def main() -> None:
    _private_key, public_key = load_or_create_issuer_keypair(ISSUER_KEY_PATH)

    ehr_params = StdioServerParameters(command=sys.executable, args=[str(EHR_SCRIPT)])
    async with stdio_client(ehr_params) as (ehr_read, ehr_write):
        async with ClientSession(ehr_read, ehr_write) as ehr_client:
            await ehr_client.initialize()

            session = GatewaySession(ehr=ehr_client, issuer_public_key=public_key, enforce=True)
            server = build_gateway_server(session)

            async with stdio_server() as (read_stream, write_stream):
                await server._lowlevel_server.run(
                    read_stream,
                    write_stream,
                    server._lowlevel_server.create_initialization_options(),
                )


if __name__ == "__main__":
    anyio.run(main)
