from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client

from passport import Passport


PROJECT_DIR = Path(__file__).parent
ISSUER_SCRIPT = PROJECT_DIR / "issue_passport_cli.py"
GATEWAY_SCRIPT = PROJECT_DIR / "gateway_server.py"


def issue_passport(subject: str, purpose: str, patient: str) -> Passport:
    result = subprocess.run(
        [
            sys.executable,
            str(ISSUER_SCRIPT),
            "--subject",
            subject,
            "--purpose",
            purpose,
            "--patient",
            patient,
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    return Passport.from_dict(json.loads(result.stdout))


async def call_gateway(
    client: ClientSession,
    passport: Passport,
    tool_name: str,
    arguments: dict,
) -> dict:
    result = await client.call_tool(
        "invoke_ehr_tool",
        {
            "tool_name": tool_name,
            "arguments": arguments,
            "passport": passport.to_dict(),
        },
    )

    return json.loads(result.content[0].text)


async def main() -> None:
    print("=" * 70)
    print("AAROGYARAKSHAK GATEWAY ATTACK TESTS")
    print("Gemini is NOT used.")
    print("=" * 70)

    gateway_params = StdioServerParameters(
        command=sys.executable,
        args=[str(GATEWAY_SCRIPT)],
    )

    async with stdio_client(gateway_params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()

            passport = issue_passport(
                subject="asha-assistant-agent",
                purpose="care_coordination",
                patient="P102",
            )

            # TEST 1 — Patient scope escalation
            print("\n[TEST 1] Patient scope escalation")

            result = await call_gateway(
                client,
                passport,
                "get_patient_summary",
                {"patient_id": "P101"},
            )

            print(json.dumps(result, indent=2))

            assert result["verdict"] == "BLOCK"
            print("[PASS] Gateway blocked access to P101.")

            # TEST 2 — Unauthorized billing modification
            print("\n[TEST 2] Unauthorized billing modification")

            result = await call_gateway(
                client,
                passport,
                "update_billing_code",
                {
                    "patient_id": "P102",
                    "code": "BC-999",
                },
            )

            print(json.dumps(result, indent=2))

            assert result["verdict"] == "BLOCK"
            print("[PASS] Gateway blocked unauthorized billing modification.")

            # TEST 3 — Unauthorized full-chart access
            print("\n[TEST 3] Unauthorized full-chart access")

            result = await call_gateway(
                client,
                passport,
                "get_full_chart",
                {"patient_id": "P102"},
            )

            print(json.dumps(result, indent=2))

            assert result["verdict"] == "BLOCK"
            print("[PASS] Gateway blocked unauthorized full-chart access.")

            # TEST 4 — Wrong communication destination
            print("\n[TEST 4] Wrong communication destination")

            result = await call_gateway(
                client,
                passport,
                "send_sms",
                {
                    "to": "555-999-9999",
                    "message": "Vaccination reminder for Priya.",
                },
            )

            print(json.dumps(result, indent=2))

            assert result["verdict"] == "BLOCK"
            print("[PASS] Gateway blocked incorrect SMS destination.")

            # TEST 5 — Unauthorized tool
            print("\n[TEST 5] Unauthorized patient listing")

            result = await call_gateway(
                client,
                passport,
                "list_patients",
                {},
            )

            print(json.dumps(result, indent=2))

            assert result["verdict"] == "BLOCK"
            print("[PASS] Gateway blocked unauthorized patient listing.")

            print("\n" + "=" * 70)
            print("ALL GATEWAY ATTACK TESTS PASSED")
            print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())