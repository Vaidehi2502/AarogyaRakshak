"""Declarative purpose profiles.

Each purpose describes exactly what an agent operating under it may do:
which EHR tools it may call, whether it may read free-text clinical
payloads, whether it is scoped to a single patient, and how strict the
egress (SMS/email) destination check is. This is the only place
permissions are defined - evaluator.py reads it, nothing computes
permissions ad hoc.

PROCESS NOTE for whoever adds scenarios (A2/A3): every purpose named in
a scenario's passport must have a PurposeProfile entry here, or every
call under it hard-fails at check 3 (purpose_known) regardless of what
the scenario is trying to test. Add the profile in the same change that
adds the first scenario using that purpose.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PurposeProfile:
    name: str
    allowed_tools: frozenset[str]
    payload_classes: frozenset[str]  # free-text payload classes this purpose may read
    patient_scope: str  # "single" - every request must target the one patient bound in the passport
    egress: str  # "none" | "patient_on_file"


# Which free-text payload class a tool's response carries, if any.
TOOL_PAYLOAD_CLASS: dict[str, str] = {
    "get_clinical_notes": "clinical_notes",
    "get_full_chart": "clinical_notes",
}

# Which parameter of a tool call names an egress destination, if any.
TOOL_EGRESS_PARAM: dict[str, str] = {
    "send_sms": "to",
    "send_email": "to",
}

_PROFILES: dict[str, PurposeProfile] = {
    "medication_reminder": PurposeProfile(
        name="medication_reminder",
        allowed_tools=frozenset({"get_patient_summary", "send_reminder"}),
        payload_classes=frozenset(),
        patient_scope="single",
        egress="none",
    ),
    "clinical_review": PurposeProfile(
        name="clinical_review",
        allowed_tools=frozenset({"get_patient_summary", "get_clinical_notes", "get_full_chart"}),
        payload_classes=frozenset({"clinical_notes"}),
        patient_scope="single",
        egress="none",
    ),
    "billing_inquiry": PurposeProfile(
        name="billing_inquiry",
        allowed_tools=frozenset({"get_patient_summary", "update_billing_code"}),
        payload_classes=frozenset(),
        patient_scope="single",
        egress="none",
    ),
    "care_coordination": PurposeProfile(
        name="care_coordination",
        allowed_tools=frozenset({"get_patient_summary", "get_clinical_notes", "send_sms", "send_email"}),
        payload_classes=frozenset({"clinical_notes"}),
        patient_scope="single",
        egress="patient_on_file",
    ),
    "appointment_scheduling": PurposeProfile(
    name="appointment_scheduling",
    allowed_tools=frozenset({"get_patient_summary", "get_clinical_notes"}),
    payload_classes=frozenset(),
    patient_scope="single",
    egress="none",
),
}


def get_purpose_profile(name: str) -> PurposeProfile | None:
    return _PROFILES.get(name)


def all_purposes() -> list[str]:
    return list(_PROFILES)
