"""Shared test/benchmark fixtures: one issuer keypair (so a scenario file
signed for one harness verifies in the other), and a helper that turns a
scenario's declarative passport spec into a signed Passport.
"""
from __future__ import annotations

from passport import Passport, generate_issuer_keypair, issue_passport

ISSUER_PRIVATE_KEY, ISSUER_PUBLIC_KEY = generate_issuer_keypair()


def passport_from_spec(spec: dict) -> Passport:
    return issue_passport(
        ISSUER_PRIVATE_KEY,
        subject=spec["subject"],
        purpose=spec["purpose"],
        patient_id=spec["patient_id"],
        ttl_seconds=spec.get("ttl_seconds", 300.0),
    )
