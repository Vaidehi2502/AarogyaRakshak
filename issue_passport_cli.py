"""Mints a signed passport against the same key file gateway_server.py
uses, and prints it as JSON on stdout.

Usage:
    python3 issue_passport_cli.py --subject agent-1 --purpose medication_reminder --patient P100

The private key is loaded from ISSUER_KEY_PATH (created by whichever of
gateway_server.py or this script runs first) - this is what lets a
passport minted in one process be accepted by a gateway running in a
completely different one.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from passport import issue_passport, load_or_create_issuer_keypair

ISSUER_KEY_PATH = Path(__file__).parent / ".aarogyarakshak_issuer_key"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", required=True, help="agent identity, e.g. agent-1")
    parser.add_argument("--purpose", required=True, help="a purpose name from policy.py")
    parser.add_argument("--patient", required=True, help="the single patient this passport is scoped to")
    parser.add_argument("--ttl", type=float, default=300.0, help="validity window in seconds (default 300)")
    args = parser.parse_args()

    private_key, _public_key = load_or_create_issuer_keypair(ISSUER_KEY_PATH)
    passport = issue_passport(
        private_key, subject=args.subject, purpose=args.purpose, patient_id=args.patient, ttl_seconds=args.ttl
    )
    print(json.dumps(passport.to_dict(), indent=2))


if __name__ == "__main__":
    main()
