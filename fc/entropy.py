"""Entropy service (DESIGN.md §3.2).

E      = SHA-256( beta_R || S )          beta_R = drand round R randomness, S = hub secret
seed_i = SHA-256( E || family_id || i )   i as 8-byte big-endian

H(S) is published and anchored before T_commit. Round R is announced at T_open and
chosen so that it fires after T_commit. Drand quicknet: 3-second period.
"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime

QUICKNET_CHAIN = "52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971"
QUICKNET_GENESIS = 1692803367  # unix seconds
QUICKNET_PERIOD = 3
DRAND_API = "https://api.drand.sh"


def round_after(t: datetime, margin_s: int = 30) -> int:
    """First quicknet round that fires at or after t + margin."""
    ts = t.astimezone(UTC).timestamp() + margin_s
    return math.floor((ts - QUICKNET_GENESIS) / QUICKNET_PERIOD) + 2


def round_time(r: int) -> datetime:
    return datetime.fromtimestamp(QUICKNET_GENESIS + (r - 1) * QUICKNET_PERIOD, tz=UTC)


@dataclass
class DrandRound:
    chain: str
    round: int
    randomness: str
    signature: str


def fetch_drand(r: int, chain: str = QUICKNET_CHAIN, timeout: float = 10.0) -> DrandRound:
    url = f"{DRAND_API}/{chain}/public/{r}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        d = json.load(resp)
    return DrandRound(chain, int(d["round"]), d["randomness"], d["signature"])


# --------------------------------------------------------------------------- hub secret

def new_hub_secret() -> bytes:
    return secrets.token_bytes(32)


def secret_commitment(S: bytes) -> str:
    return hashlib.sha256(b"future-challenge/hub-secret/v1" + S).hexdigest()


def verify_secret(S: bytes, H_S: str) -> bool:
    return secrets.compare_digest(secret_commitment(S), H_S)


# --------------------------------------------------------------------------- derivation

def combined_entropy(beta_hex: str, S: bytes) -> bytes:
    return hashlib.sha256(bytes.fromhex(beta_hex) + S).digest()


def derive_seed(E: bytes, family_id: str, index: int) -> int:
    d = hashlib.sha256(E + family_id.encode() + index.to_bytes(8, "big")).digest()
    return int.from_bytes(d, "big")


def entropy_record(round_id: str, H_S: str, S: bytes | None, drand: DrandRound | None,
                   families: dict[str, int]) -> dict:
    rec = {
        "round_id": round_id,
        "hub_secret_commitment": H_S,
        "hub_secret": S.hex() if S else None,
        "drand": None if drand is None else drand.__dict__,
        "E": None,
        "seed_derivation": "sha256(E || family_id || index_be64)",
        "families": {k: {"n": n} for k, n in families.items()},
    }
    if S is not None and drand is not None:
        if not verify_secret(S, H_S):
            raise ValueError("hub secret does not match commitment")
        rec["E"] = combined_entropy(drand.randomness, S).hex()
    return rec
