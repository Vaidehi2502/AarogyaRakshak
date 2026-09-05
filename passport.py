"""Ed25519 purpose-bound passports.

A passport is a short-lived, signed grant: which agent (subject), for
what purpose, scoped to which single patient, valid for how long.
Signing keys belong to the issuing authority (e.g. a hospital identity
system); the gateway only ever holds the issuer's public key and
verifies - it never signs anything itself.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat


@dataclass(frozen=True)
class Passport:
    subject: str
    purpose: str
    patient_id: str
    issued_at: float
    expires_at: float
    nonce: str
    signature: str = ""  # hex-encoded, empty until issue_passport signs it

    def payload(self) -> dict:
        d = asdict(self)
        d.pop("signature")
        return d

    def canonical_bytes(self) -> bytes:
        return json.dumps(self.payload(), sort_keys=True, separators=(",", ":")).encode()

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Passport":
        return Passport(**d)


def generate_issuer_keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    private_key = Ed25519PrivateKey.generate()
    return private_key, private_key.public_key()


def save_private_key(private_key: Ed25519PrivateKey, path: Path) -> None:
    path.write_bytes(private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))


def load_private_key(path: Path) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(path.read_bytes())


def save_public_key(public_key: Ed25519PublicKey, path: Path) -> None:
    path.write_bytes(public_key.public_bytes(Encoding.Raw, PublicFormat.Raw))


def load_public_key(path: Path) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(path.read_bytes())


def load_or_create_issuer_keypair(private_key_path: Path) -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Two separately-launched processes (the gateway and a passport
    issuer) need the same keypair to agree on what a valid signature is.
    Loads it from `private_key_path` if present, otherwise generates and
    persists a fresh one - this is a demo-grade single-file key store,
    not a production KMS.
    """
    if private_key_path.exists():
        private_key = load_private_key(private_key_path)
    else:
        private_key, _ = generate_issuer_keypair()
        private_key_path.parent.mkdir(parents=True, exist_ok=True)
        save_private_key(private_key, private_key_path)
    return private_key, private_key.public_key()


def issue_passport(
    private_key: Ed25519PrivateKey,
    *,
    subject: str,
    purpose: str,
    patient_id: str,
    ttl_seconds: float = 300.0,
    issued_at: float | None = None,
) -> Passport:
    now = issued_at if issued_at is not None else time.time()
    unsigned = Passport(
        subject=subject,
        purpose=purpose,
        patient_id=patient_id,
        issued_at=now,
        expires_at=now + ttl_seconds,
        nonce=uuid.uuid4().hex,
    )
    signature = private_key.sign(unsigned.canonical_bytes())
    return Passport(**{**unsigned.to_dict(), "signature": signature.hex()})


def verify_signature(public_key: Ed25519PublicKey, passport: Passport) -> bool:
    if not passport.signature:
        return False
    try:
        public_key.verify(bytes.fromhex(passport.signature), passport.canonical_bytes())
        return True
    except (InvalidSignature, ValueError):
        return False
