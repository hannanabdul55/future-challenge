"""Commitment protocol (DESIGN.md §6).

commit  = SHA-256( canonical_bytes(submission) || canonical_bytes(rationale) || nonce )
Receipts are signed by the hub; every 10 minutes commits are batched into a Merkle
tree whose root is anchored externally (OpenTimestamps, Rekor). Anchoring itself is
performed by the ops tooling; this module produces roots, proofs and receipts.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

DOMAIN_SEP = b"future-challenge/commit/v1"

# Stamped on every published document so a later reader detects, rather than
# silently misparses, a format change. Bump only with a migration note.
SCHEMA_VERSION = "2026.08"


# --------------------------------------------------------------------------- hashing

def sha256(*parts: bytes) -> bytes:
    h = hashlib.sha256()
    for p in parts:
        h.update(p)
    return h.digest()


def canonical_json(obj) -> bytes:
    """Deterministic JSON encoding used wherever bytes are signed or hashed."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def file_bytes(path: str | Path) -> bytes:
    return Path(path).read_bytes()


def new_nonce() -> bytes:
    return secrets.token_bytes(32)


def nonce_hash(nonce: bytes) -> str:
    """Hex digest binding a nonce without revealing it (published at commit time)."""
    return sha256(DOMAIN_SEP, b"nonce", nonce).hex()


def iso_z(now: datetime) -> str:
    """The single timestamp formatter: RFC 3339 UTC with millisecond precision."""
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def commitment(submission: bytes, rationale: bytes, nonce: bytes) -> str:
    """Return hex commitment for a submission. Nonce must be 32 bytes."""
    if len(nonce) != 32:
        raise ValueError("nonce must be 32 bytes")
    return sha256(DOMAIN_SEP, submission, rationale, nonce).hex()


def verify_reveal(commit_hex: str, submission: bytes, rationale: bytes, nonce: bytes) -> bool:
    return secrets.compare_digest(commitment(submission, rationale, nonce), commit_hex)


# --------------------------------------------------------------------------- merkle

def _leaf(commit_hex: str) -> bytes:
    return sha256(b"\x00", bytes.fromhex(commit_hex))


def _node(left: bytes, right: bytes) -> bytes:
    return sha256(b"\x01", left, right)


def merkle_root(commits: list[str]) -> str:
    """Root over commitment hex strings (order preserved). Odd levels duplicate last."""
    if not commits:
        return sha256(b"\x00").hex()
    level = [_leaf(c) for c in commits]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [_node(level[i], level[i + 1]) for i in range(0, len(level), 2)]
    return level[0].hex()


def merkle_proof(commits: list[str], index: int) -> list[tuple[str, str]]:
    """Inclusion proof for commits[index]: list of (sibling_hex, 'L'|'R')."""
    if not 0 <= index < len(commits):
        raise IndexError(index)
    level = [_leaf(c) for c in commits]
    proof: list[tuple[str, str]] = []
    i = index
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        sib = i ^ 1
        proof.append((level[sib].hex(), "L" if sib < i else "R"))
        level = [_node(level[j], level[j + 1]) for j in range(0, len(level), 2)]
        i //= 2
    return proof


def merkle_verify(commit_hex: str, proof: list[tuple[str, str]], root_hex: str) -> bool:
    h = _leaf(commit_hex)
    for sib_hex, side in proof:
        sib = bytes.fromhex(sib_hex)
        h = _node(sib, h) if side == "L" else _node(h, sib)
    return secrets.compare_digest(h.hex(), root_hex)


# --------------------------------------------------------------------------- keys

def generate_keypair() -> tuple[bytes, bytes]:
    """Return (private_pem, public_pem)."""
    sk = Ed25519PrivateKey.generate()
    priv = sk.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub = sk.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return priv, pub


def load_private(pem: bytes) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("expected Ed25519 private key")
    return key


def load_public(pem: bytes) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(pem)
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError("expected Ed25519 public key")
    return key


def sign(sk: Ed25519PrivateKey, payload: bytes) -> str:
    return sk.sign(DOMAIN_SEP + payload).hex()


def verify_sig(pk: Ed25519PublicKey, payload: bytes, sig_hex: str) -> bool:
    try:
        pk.verify(bytes.fromhex(sig_hex), DOMAIN_SEP + payload)
        return True
    except (InvalidSignature, ValueError):
        return False


# --------------------------------------------------------------------------- receipts

@dataclass
class CommitRequest:
    round_id: str
    model_id: str
    commit: str
    team_signature: str  # over canonical_json({round_id, model_id, commit})

    def payload(self) -> bytes:
        return canonical_json(
            {"round_id": self.round_id, "model_id": self.model_id, "commit": self.commit}
        )


def team_sign_request(sk: Ed25519PrivateKey, round_id: str, model_id: str, commit: str) -> CommitRequest:
    req = CommitRequest(round_id, model_id, commit, "")
    req.team_signature = sign(sk, req.payload())
    return req


@dataclass
class Receipt:
    round_id: str
    model_id: str
    commit: str
    server_time: str
    merkle_position: int
    hub_signature: str

    def payload(self) -> bytes:
        d = asdict(self)
        d.pop("hub_signature")
        return canonical_json(d)


def issue_receipt(hub_sk: Ed25519PrivateKey, req: CommitRequest, team_pk: Ed25519PublicKey,
                  position: int, now: datetime | None = None) -> Receipt:
    if not verify_sig(team_pk, req.payload(), req.team_signature):
        raise ValueError("bad team signature")
    now = now or datetime.now(UTC)
    r = Receipt(req.round_id, req.model_id, req.commit, iso_z(now), position, "")
    r.hub_signature = sign(hub_sk, r.payload())
    return r


def verify_receipt(hub_pk: Ed25519PublicKey, r: Receipt) -> bool:
    return verify_sig(hub_pk, r.payload(), r.hub_signature)


def write_sidecar(path: str | Path, receipt: Receipt, proof: list[tuple[str, str]] | None,
                  root: str | None, extra: dict | None = None, *,
                  team_signature: str = "", nonce_hash: str = "",
                  window_index: int | None = None) -> None:
    """Write the participant's commitment record (DESIGN.md Appendix A).

    Hash and signature values are bare lowercase hex throughout — the same bytes
    `commitment()` and `sign()` already produce, so no prefix is parsed or stripped
    anywhere. `reveal` stays null until the reveal window closes.
    """
    rec = {
        "schema_version": SCHEMA_VERSION,
        "round_id": receipt.round_id,
        "model_id": receipt.model_id,
        "commit": receipt.commit,
        "nonce_hash": nonce_hash,
        "team_signature": team_signature,
        "hub_receipt": {
            "server_time": receipt.server_time,
            "merkle_position": receipt.merkle_position,
            "merkle_root": root,
            "merkle_path": proof,
            "window_index": window_index,
            "hub_signature": receipt.hub_signature,
            "ots_proof": None,
            "rekor_log_index": None,
        },
        "reveal": None,
    }
    if extra:
        rec.update(extra)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(rec, indent=2) + "\n")
