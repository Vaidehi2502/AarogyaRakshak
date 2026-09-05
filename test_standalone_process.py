"""Item 5 sanity check: prove the gateway works as a real, separate OS
process - not just a Python object test_scenarios.py and benchmark.py
import in-process.

This launches THREE independent processes:
    this test  --(subprocess, stdio)-->  gateway_server.py  --(subprocess, stdio)-->  ehr_server.py

The test process's ClientSession only ever holds a pipe to
gateway_server.py. It has no handle on the EHR subprocess at all - that
reference lives solely inside gateway_server.py's process memory. That
is what "the agent has no route to the EHR except through the gateway"
means when it's an architectural fact instead of a convenience of how
the test harness happens to be wired.

Passport issuance also crosses a process boundary: issue_passport_cli.py
runs as its own subprocess and signs with a key file on disk
(ISSUER_KEY_PATH) - the same file gateway_server.py reads to verify.
Neither process is handed the other's key material directly; they agree
only through that shared file, the way an issuer and gateway would in a
real deployment.

Run with: python3 test_standalone_process.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client

PROJECT_DIR = Path(__file__).parent
GATEWAY_SCRIPT = PROJECT_DIR / "gateway_server.py"
ISSUE_PASSPORT_SCRIPT = PROJECT_DIR / "issue_passport_cli.py"


async def issue_passport(subject: str, purpose: str, patient: str) -> dict:
    """Runs issue_passport_cli.py as its own subprocess and parses its
    stdout - this is a plain CLI, not an MCP server, so a plain
    subprocess call is enough to prove the cross-process key agreement.
    """
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(ISSUE_PASSPORT_SCRIPT),
        "--subject", subject,
        "--purpose", purpose,
        "--patient", patient,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"issue_passport_cli.py failed: {stderr.decode()}")
    return json.loads(stdout.decode())


async def main() -> int:
    print(f"issuing passports via a separate process ({ISSUE_PASSPORT_SCRIPT.name})...")
    benign_passport = await issue_passport("standalone-test-agent", "medication_reminder", "P100")
    print("  passport for P100/medication_reminder issued.")

    print(f"launching the gateway as a separate process ({GATEWAY_SCRIPT.name}), "
          f"which will itself launch ehr_server.py as ITS subprocess...")
    gateway_params = StdioServerParameters(command=sys.executable, args=[str(GATEWAY_SCRIPT)])

    all_passed = True
    async with stdio_client(gateway_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as agent_client:
            await agent_client.initialize()
            print("  connected to the gateway process over real stdio transport.")

            # 1. Benign call, in scope - must ALLOW.
            result = await agent_client.call_tool(
                "invoke_ehr_tool",
                {
                    "tool_name": "send_reminder",
                    "arguments": {"patient_id": "P100", "message": "Reminder: please take your medication."},
                    "passport": benign_passport,
                },
            )
            outcome = json.loads(result.content[0].text)
            ok = outcome["verdict"] == "ALLOW"
            all_passed &= ok
            print(f"  [{'PASS' if ok else 'FAIL'}] benign send_reminder(P100) -> {outcome['verdict']} (expected ALLOW)")

            # 2. Scope escalation with the SAME passport (bound to P100),
            # targeting a different patient - must BLOCK, and the EHR
            # subprocess this gateway process holds must never be reachable
            # to prove otherwise.
            result = await agent_client.call_tool(
                "invoke_ehr_tool",
                {
                    "tool_name": "send_reminder",
                    "arguments": {"patient_id": "P101", "message": "Reminder: please take your medication."},
                    "passport": benign_passport,
                },
            )
            outcome = json.loads(result.content[0].text)
            ok = outcome["verdict"] == "BLOCK"
            all_passed &= ok
            print(f"  [{'PASS' if ok else 'FAIL'}] scope escalation to P101 -> {outcome['verdict']} (expected BLOCK)")

    if all_passed:
        print("\nAll standalone-process checks passed: the gateway enforces policy")
        print("running as a genuinely separate OS process, over real MCP stdio transport.")
        return 0
    print("\nSome standalone-process checks FAILED.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
