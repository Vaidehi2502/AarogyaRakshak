"""Response-path provenance labelling.

Every value that comes back from the EHR is labelled exactly once, at the
moment it re-enters the agent's context:

  TRUSTED     - identity/contact fields the EHR asserts about its own
                record (name, phone, email) - the gateway can compute
                egress destinations from these.
  STRUCTURED  - machine fields with a fixed shape (ids, codes, dates).
  FREE_TEXT   - clinician-authored prose (notes). This is exactly the
                surface an indirect prompt injection lands in, because
                nothing before this point has read or interpreted it.

Labelling happens here, on the response path, never on the request path
- request-side permissions are entirely evaluator.py's + policy.py's job.

trace() answers a narrower question than "does this request quote any
free text anywhere": given the free-text chunks the agent has seen so
far, did an *identifier-like* value in a new request (a phone number,
email, URL, or ID) come from that free text? That is the shape of a real
exfiltration attempt - the agent lifted a destination or code out of
attacker-controlled prose.

It is deliberately blind to ordinary words. An earlier version
substring-matched every string parameter against every free-text chunk,
so any parameter that happened to share a common word with a note (e.g.
both mention "today") was flagged as tainted - a false positive, not a
security signal. Free-form payload content is governed by the purpose
and payload_class checks in the evaluator, not by provenance.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Label(str, Enum):
    TRUSTED = "trusted"
    STRUCTURED = "structured"
    FREE_TEXT = "free_text"


# A value is only ever compared against free-text content if it matches
# one of these *in full* - a sentence that happens to contain a phone
# number is still free text, not a phone number.
_PHONE_RE = re.compile(r"\+?\d[\d\-\s()]{7,}\d")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://\S+")
_ID_RE = re.compile(r"[A-Za-z]{1,5}-?\d{3,}")  # e.g. P100, MRN-4821, PT1234

_IDENTIFIER_PATTERNS = (_PHONE_RE, _EMAIL_RE, _URL_RE, _ID_RE)


def is_identifier_like(value: str) -> bool:
    """True if `value` as a whole looks like a phone/email/URL/ID, not prose."""
    value = value.strip()
    if not value:
        return False
    return any(pattern.fullmatch(value) for pattern in _IDENTIFIER_PATTERNS)


# Which top-level fields of a tool's response payload are free text vs
# trusted identity fields. Anything not listed defaults to STRUCTURED.
_FREE_TEXT_FIELDS: dict[str, frozenset[str]] = {
    "get_clinical_notes": frozenset({"notes"}),
    "get_full_chart": frozenset({"notes"}),
}
_TRUSTED_FIELDS: dict[str, frozenset[str]] = {
    "get_patient_summary": frozenset({"name", "phone", "email"}),
    "get_full_chart": frozenset({"name", "phone", "email"}),
}


def label_response(tool_name: str, result: dict) -> dict[str, Label]:
    """Label each top-level field of a tool's result payload."""
    free_text_fields = _FREE_TEXT_FIELDS.get(tool_name, frozenset())
    trusted_fields = _TRUSTED_FIELDS.get(tool_name, frozenset())
    labels: dict[str, Label] = {}
    for key in result:
        if key in free_text_fields:
            labels[key] = Label.FREE_TEXT
        elif key in trusted_fields:
            labels[key] = Label.TRUSTED
        else:
            labels[key] = Label.STRUCTURED
    return labels


@dataclass
class FreeTextChunk:
    """One piece of clinician-authored prose the agent has read, tagged
    with where it came from so a later taint hit can cite an exact span
    back to the console (see ProvenanceStore.locate).
    """

    text: str
    field: str
    patient_id: str | None = None


@dataclass
class ProvenanceStore:
    """Per-session accumulator of free-text chunks seen on the response path."""

    free_text_chunks: list[FreeTextChunk] = field(default_factory=list)

    def record(self, tool_name: str, labels: dict[str, Label], values: dict, patient_id: str | None = None) -> None:
        for key, label in labels.items():
            if label is not Label.FREE_TEXT:
                continue
            value = values.get(key)
            if isinstance(value, str):
                self.free_text_chunks.append(FreeTextChunk(value, key, patient_id))
            elif isinstance(value, list):
                # One chunk per field, not per list item: a multi-note
                # field (e.g. `notes: [...]`) is a single citation surface
                # for the console, which displays and slices it as one
                # space-joined string - start/end must be offsets into
                # that same joined text, not into one item alone.
                joined = " ".join(v for v in value if isinstance(v, str))
                if joined:
                    self.free_text_chunks.append(FreeTextChunk(joined, key, patient_id))

    def trace(self, arguments: dict) -> list[str]:
        """Names of request parameters whose identifier-like value appears
        verbatim inside free text the agent has already read.

        Restricted to identifier-like values (phone/email/URL/ID) on
        purpose: a free-text parameter (e.g. a reminder message) is never
        compared against free-text chunks, no matter what words it
        shares with a note - that comparison belongs to the purpose and
        payload_class checks, not to provenance.
        """
        tainted: list[str] = []
        if not self.free_text_chunks:
            return tainted
        for name, value in arguments.items():
            if not isinstance(value, str) or not is_identifier_like(value):
                continue
            if any(value in chunk.text for chunk in self.free_text_chunks):
                tainted.append(name)
        return tainted

    def locate(self, value: str) -> dict | None:
        """Where a tainted value came from, as a console citation: the
        byte span of `value` inside the first free-text chunk that
        contains it, plus a `cite` string like "ehr:P105/notes[57-98]".
        Returns None if no chunk contains it (should not happen for a
        name trace() already returned as tainted).
        """
        for chunk in self.free_text_chunks:
            start = chunk.text.find(value)
            if start == -1:
                continue
            end = start + len(value)
            uri = f"ehr:{chunk.patient_id}/{chunk.field}" if chunk.patient_id else f"ehr:{chunk.field}"
            return {"uri": uri, "field": chunk.field, "start": start, "end": end, "cite": f"{uri}[{start}-{end}]"}
        return None
