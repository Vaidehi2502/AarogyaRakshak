"""Eight ordered, deterministic checks. No LLM anywhere in this file.

Every check is a pure function returning a CheckResult. The evaluator
runs all eight and folds them into exactly one of three verdicts:

    any HARD_FAIL  -> BLOCK
    else any SOFT  -> FLAG
    else           -> ALLOW

All eight always run - a hard failure on check 6 is never hidden by an
earlier pass, and the audit log gets every reason, not just the first.

All eight checks are HARD_FAIL checks - there is no live SOFT_SIGNAL
path, so no scenario should ever produce FLAG. The three-verdict
machinery (ALLOW/FLAG/BLOCK) stays in place for a future soft check; do
not read "0 flags in the benchmark" as a bug.

SETTLED DECISION (V, confirmed 2026-09-08): provenance_taint is HARD and
stays HARD - see _check_provenance below for the rationale. This is NOT
an open question; do not flip it to SOFT_SIGNAL without a deliberate
re-decision. It is load-bearing downstream: scenarios IND-03..06 are
provenance-ONLY blocks whose expected verdict is BLOCK. Flipping to soft
turns those four into FLAG (attack block rate 100% -> 86.7%, caught
stays 100%) and breaks test_scenarios.py until their `expected` fields
are updated to FLAG. audit_checks.py is what surfaces this coupling.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from passport import Passport, verify_signature
from policy import TOOL_EGRESS_PARAM, TOOL_PAYLOAD_CLASS, PurposeProfile, get_purpose_profile
from provenance import ProvenanceStore


class Status(str, Enum):
    PASS = "pass"
    HARD_FAIL = "hard_fail"
    SOFT_SIGNAL = "soft_signal"


class Verdict(str, Enum):
    ALLOW = "ALLOW"
    FLAG = "FLAG"
    BLOCK = "BLOCK"


@dataclass
class CheckResult:
    name: str
    status: Status
    reason: str


# --- the eight checks ---------------------------------------------------


def _check_signature(passport: Passport, issuer_public_key: Ed25519PublicKey) -> CheckResult:
    if verify_signature(issuer_public_key, passport):
        return CheckResult("signature", Status.PASS, "signature valid")
    return CheckResult("signature", Status.HARD_FAIL, "passport signature invalid")


def _check_expiry(passport: Passport, now: float) -> CheckResult:
    if passport.issued_at - 5 > now:
        return CheckResult("expiry", Status.HARD_FAIL, "passport issued in the future")
    if passport.expires_at < now:
        return CheckResult("expiry", Status.HARD_FAIL, "passport expired")
    return CheckResult("expiry", Status.PASS, "passport within validity window")


def _check_purpose_known(passport: Passport) -> tuple[CheckResult, PurposeProfile | None]:
    profile = get_purpose_profile(passport.purpose)
    if profile is None:
        return CheckResult("purpose_known", Status.HARD_FAIL, f"unknown purpose {passport.purpose!r}"), None
    return CheckResult("purpose_known", Status.PASS, "purpose recognized"), profile


def _check_tool_allowed(profile: PurposeProfile | None, tool_name: str) -> CheckResult:
    if profile is None:
        return CheckResult("tool_allowed", Status.HARD_FAIL, "no purpose profile to check against")
    if tool_name not in profile.allowed_tools:
        return CheckResult(
            "tool_allowed", Status.HARD_FAIL, f"tool {tool_name!r} not permitted under purpose {profile.name!r}"
        )
    return CheckResult("tool_allowed", Status.PASS, "tool permitted for purpose")


def _check_patient_scope(passport: Passport, arguments: dict) -> CheckResult:
    requested_patient = arguments.get("patient_id")
    if requested_patient is not None and requested_patient != passport.patient_id:
        return CheckResult(
            "patient_scope",
            Status.HARD_FAIL,
            f"passport scoped to patient {passport.patient_id!r}, request targets {requested_patient!r}",
        )
    return CheckResult("patient_scope", Status.PASS, "patient scope respected")


def _check_payload_class(profile: PurposeProfile | None, tool_name: str) -> CheckResult:
    required = TOOL_PAYLOAD_CLASS.get(tool_name)
    if required is None:
        return CheckResult("payload_class", Status.PASS, "tool carries no restricted payload class")
    if profile is None or required not in profile.payload_classes:
        return CheckResult(
            "payload_class", Status.HARD_FAIL, f"purpose not permitted to read payload class {required!r}"
        )
    return CheckResult("payload_class", Status.PASS, "payload class permitted for purpose")


def _check_egress_destination(
    profile: PurposeProfile | None, tool_name: str, arguments: dict, on_file_contacts: dict
) -> CheckResult:
    param = TOOL_EGRESS_PARAM.get(tool_name)
    if param is None:
        return CheckResult("egress_destination", Status.PASS, "tool has no egress destination")
    if profile is None or profile.egress == "none":
        return CheckResult("egress_destination", Status.HARD_FAIL, "purpose does not permit egress")
    destination = arguments.get(param)
    allowed = {v for v in on_file_contacts.values() if v}
    if destination not in allowed:
        return CheckResult(
            "egress_destination",
            Status.HARD_FAIL,
            f"destination {destination!r} does not match patient's on-file contact info",
        )
    return CheckResult("egress_destination", Status.PASS, "destination matches on-file contact")


def _check_provenance(store: ProvenanceStore, arguments: dict) -> CheckResult:
    """HARD_FAIL, not SOFT_SIGNAL, by SETTLED decision (see module
    docstring): the central claim is that untrusted content cannot be
    obeyed, not that it is merely suspicious. A destination that happens
    to match the patient's on-file contact does not rescue a parameter
    that traces to free text the agent read - the taint is disqualifying
    on its own. (Soft until 2026-09-05; flipped because a provenance-only
    failure that still executes contradicts the "cannot be obeyed" claim;
    confirmed to stay hard 2026-09-08.) IND-03..06 depend on this.
    """
    tainted = store.trace(arguments)
    if tainted:
        return CheckResult(
            "provenance_taint",
            Status.HARD_FAIL,
            f"parameter(s) {tainted} match content the agent previously read from untrusted free text - untrusted content cannot be obeyed",
        )
    return CheckResult("provenance_taint", Status.PASS, "no request parameter traces to untrusted free text")


@dataclass
class EvaluationResult:
    verdict: Verdict
    checks: list[CheckResult]

    @property
    def reasons(self) -> list[str]:
        return [c.reason for c in self.checks if c.status != Status.PASS]

    @property
    def soft_signal_checks(self) -> list[str]:
        """Names of checks that fired SOFT_SIGNAL - i.e. what to point at
        when explaining why a call came back FLAG rather than BLOCK.
        """
        return [c.name for c in self.checks if c.status == Status.SOFT_SIGNAL]


def evaluate(
    *,
    passport: Passport,
    issuer_public_key: Ed25519PublicKey,
    tool_name: str,
    arguments: dict,
    provenance_store: ProvenanceStore,
    on_file_contacts: dict,
    now: float | None = None,
) -> EvaluationResult:
    now = now if now is not None else time.time()

    purpose_check, profile = _check_purpose_known(passport)
    checks = [
        _check_signature(passport, issuer_public_key),
        _check_expiry(passport, now),
        purpose_check,
        _check_tool_allowed(profile, tool_name),
        _check_patient_scope(passport, arguments),
        _check_payload_class(profile, tool_name),
        _check_egress_destination(profile, tool_name, arguments, on_file_contacts),
        _check_provenance(provenance_store, arguments),
    ]

    if any(c.status == Status.HARD_FAIL for c in checks):
        verdict = Verdict.BLOCK
    elif any(c.status == Status.SOFT_SIGNAL for c in checks):
        verdict = Verdict.FLAG
    else:
        verdict = Verdict.ALLOW
    return EvaluationResult(verdict=verdict, checks=checks)
