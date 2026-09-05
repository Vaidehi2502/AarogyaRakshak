"""Hash-chained, append-only audit log.

Every gateway decision is appended here. Each entry embeds the hash of
the entry before it, so the log can be verified end-to-end: tamper with
or delete any entry and every hash after it stops matching.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field

GENESIS_HASH = "0" * 64


def _canonical(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()


@dataclass
class AuditLog:
    entries: list[dict] = field(default_factory=list)

    def append(self, event: dict) -> dict:
        prev_hash = self.entries[-1]["hash"] if self.entries else GENESIS_HASH
        body = {**event, "seq": len(self.entries), "timestamp": time.time(), "prev_hash": prev_hash}
        record = {**body, "hash": hashlib.sha256(prev_hash.encode() + _canonical(body)).hexdigest()}
        self.entries.append(record)
        return record

    def verify_chain(self) -> bool:
        prev_hash = GENESIS_HASH
        for record in self.entries:
            body = {k: v for k, v in record.items() if k != "hash"}
            if body["prev_hash"] != prev_hash:
                return False
            if hashlib.sha256(prev_hash.encode() + _canonical(body)).hexdigest() != record["hash"]:
                return False
            prev_hash = record["hash"]
        return True
