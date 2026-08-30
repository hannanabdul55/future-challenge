"""The hub's commit-intake service core (DESIGN.md §6.2).

This module is the single call surface for accepting a commitment. The PR-path
workflow and any future HTTP handler both call `submit_commitment`, so
COMMIT-06's "both paths produce the same receipt" holds by construction rather
than by keeping two implementations in step.

Two rules shape everything here:

- Deadlines come only from `rounds/<id>/manifest.json`. They are never
  re-derived from ISO-week arithmetic at a call site.
- Anchoring never blocks receipt issuance. The anchor callable is injected, and
  a failure is recorded on the root as `anchor_status` rather than raised.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from . import anchor as A
from . import commit as C

# A distinct signed structure earns its own domain separator — a root signature
# must never be replayable as a receipt signature.
ROOT_DOMAIN_SEP = b"future-challenge/root/v1"

ACCEPTED = "accepted"
REJECTED = "rejected"
BAD_SIGNATURE = "bad_signature"
AFTER_T_COMMIT = "after_T_commit"

# Transport-level input limits, applied before any cryptographic work so a
# hostile artifact cannot make the hub do expensive things.
MAX_SIDECAR_BYTES = 64 * 1024
COMMIT_RE = re.compile(r"^[0-9a-f]{64}$")
IDENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
REQUIRED_REQUEST_KEYS = ("round_id", "model_id", "commit", "team_signature")


@dataclass
class RootDocument:
    """One published Merkle root, chained to its predecessor (CT-STH shaped)."""

    schema_version: str
    round_id: str
    window_index: int
    prev_root: str | None
    commit_count: int
    root: str
    server_time: str
    hub_signature: str
    anchor_status: str
    ots_proof: str | None
    rekor: dict

    def payload(self) -> bytes:
        d = asdict(self)
        d.pop("hub_signature")
        return C.canonical_json(d)


@dataclass
class LedgerEntry:
    """One line of the public record — accepted and rejected alike."""

    schema_version: str
    round_id: str
    model_id: str
    commit: str
    team_signature: str
    received: str
    outcome: str
    reason: str | None
    merkle_position: int | None
    window_index: int | None


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n")


def load_manifest(round_dir: str | Path) -> dict:
    """Read the round manifest — the only source of T_commit and T_reveal."""
    return json.loads((Path(round_dir) / "manifest.json").read_text())


def deadline(manifest: dict, key: str) -> datetime:
    """Parse a manifest deadline. Never compute one; only ever read one."""
    return datetime.fromisoformat(manifest[key])


def read_ledger(round_dir: str | Path) -> list[dict]:
    path = Path(round_dir) / "commits.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())["entries"]


def append_ledger(round_dir: str | Path, entry: LedgerEntry) -> None:
    entries = read_ledger(round_dir)
    entries.append(asdict(entry))
    _write_json(
        Path(round_dir) / "commits.json",
        {"schema_version": C.SCHEMA_VERSION, "entries": entries},
    )


def read_roots(round_dir: str | Path) -> list[dict]:
    """Every published root for a round, ordered by window index."""
    roots_dir = Path(round_dir) / "roots"
    if not roots_dir.exists():
        return []
    docs = []
    for p in roots_dir.glob("*.json"):
        if p.stem.isdigit():
            docs.append(json.loads(p.read_text()))
    return sorted(docs, key=lambda d: d["window_index"])


def publish_root(round_dir: str | Path, commits: list[str], hub_sk, *,
                 now: datetime | None = None, anchor: A.Anchor | None = None) -> RootDocument:
    """Publish a Merkle root over `commits`, then try to anchor it.

    The root is written and signed before anchoring is attempted, so an
    anchoring outage can never cost a participant their receipt.
    """
    now = now or datetime.now(UTC)
    anchor = anchor or A.null_anchor
    round_dir = Path(round_dir)

    existing = read_roots(round_dir)
    window_index = len(existing)
    prev_root = existing[-1]["root"] if existing else None

    doc = RootDocument(
        schema_version=C.SCHEMA_VERSION,
        round_id=load_manifest(round_dir)["round_id"],
        window_index=window_index,
        prev_root=prev_root,
        commit_count=len(commits),
        root=C.merkle_root(commits),
        server_time=C.iso_z(now),
        hub_signature="",
        anchor_status=A.NOT_ANCHORED,
        ots_proof=None,
        rekor={},
    )
    doc.hub_signature = C.sign(hub_sk, ROOT_DOMAIN_SEP + doc.payload())

    path = round_dir / "roots" / f"{window_index}.json"
    _write_json(path, asdict(doc))

    # Anchoring reaches third parties and is allowed to fail. It is never
    # allowed to propagate — the receipt is already earned.
    try:
        result = anchor(path, path.read_bytes())
        doc.anchor_status = result.status
        doc.ots_proof = result.ots_path
        doc.rekor = {
            "log_index": result.rekor_log_index,
            "bundle_path": result.rekor_bundle_path,
            "stamped_at": result.stamped_at,
        }
    except Exception as exc:  # noqa: BLE001 - any anchor failure is recorded, never raised
        doc.anchor_status = A.FAILED
        doc.rekor = {"error": f"{type(exc).__name__}: {exc}"}

    # Re-sign: the anchor outcome is part of the published document.
    doc.hub_signature = ""
    doc.hub_signature = C.sign(hub_sk, ROOT_DOMAIN_SEP + doc.payload())
    _write_json(path, asdict(doc))
    return doc


def submit_commitment(req: C.CommitRequest, team_pk, hub_sk, round_dir: str | Path, *,
                      now: datetime | None = None,
                      anchor: A.Anchor | None = None) -> tuple[C.Receipt | None, str]:
    """Accept or refuse one commitment, and publish the outcome either way.

    Returns `(Receipt, "accepted")`, `(None, "bad_signature")` or
    `(None, "after_T_commit")`. Every one of those lands in the round's public
    ledger — a refused commitment is published, never dropped, because success
    criterion 2 is only observable if the refusal is visible.

    The commit window closes *at* `T_commit`: a request whose recorded server
    time equals the deadline is already late.
    """
    now = now or datetime.now(UTC)
    round_dir = Path(round_dir)
    t_commit = deadline(load_manifest(round_dir), "T_commit")

    receipt: C.Receipt | None = None
    position: int | None = None
    window_index: int | None = None

    if not C.verify_sig(team_pk, req.payload(), req.team_signature):
        # A forged request is a forged request whether or not it is also late;
        # the more specific defect wins, so a forgery never enters the record
        # as a merely late legitimate attempt.
        outcome, reason, result = REJECTED, BAD_SIGNATURE, (None, BAD_SIGNATURE)
    elif now >= t_commit:
        outcome, reason, result = REJECTED, AFTER_T_COMMIT, (None, AFTER_T_COMMIT)
    else:
        accepted = [e["commit"] for e in read_ledger(round_dir) if e["outcome"] == ACCEPTED]
        position = len(accepted)
        receipt = C.issue_receipt(hub_sk, req, team_pk, position, now=now)
        doc = publish_root(round_dir, [*accepted, req.commit], hub_sk, now=now, anchor=anchor)
        window_index = doc.window_index
        outcome, reason, result = ACCEPTED, None, (receipt, ACCEPTED)

    # One construction site for both outcomes, so accepted and rejected entries
    # cannot drift apart in shape.
    append_ledger(
        round_dir,
        LedgerEntry(
            schema_version=C.SCHEMA_VERSION,
            round_id=req.round_id,
            model_id=req.model_id,
            commit=req.commit,
            team_signature=req.team_signature,
            received=C.iso_z(now),
            outcome=outcome,
            reason=reason,
            merkle_position=position,
            window_index=window_index,
        ),
    )
    return result


def verify_root_signature(hub_pk, doc: dict) -> bool:
    """Check the hub's signature over a published root document."""
    sig = doc.get("hub_signature", "")
    unsigned = {k: v for k, v in doc.items() if k != "hub_signature"}
    return C.verify_sig(hub_pk, ROOT_DOMAIN_SEP + C.canonical_json(unsigned), sig)


def root_for(round_dir: str | Path, root_hex: str) -> dict | None:
    """Find the published root document whose root equals `root_hex`."""
    for doc in read_roots(round_dir):
        if secrets.compare_digest(doc["root"], root_hex):
            return doc
    return None


# --------------------------------------------------------------- front ends
#
# COMMIT-06 says the PR path and the API path must produce the same receipt.
# That holds here by construction rather than by discipline: both adapters are
# transport parsers that delegate to `submit_commitment`. Neither contains a
# deadline check, a signature check, a ledger write or a root publication, so
# there is nothing that can drift between them.


def load_team_key(model_id: str, metadata_dir: str | Path):
    """Resolve a team's Ed25519 public key from its registered entry metadata.

    The key comes from `model-metadata/<model_id>.yml` and nowhere else — never
    from the submitted request — so a request cannot nominate the key that will
    be used to validate it.
    """
    path = Path(metadata_dir) / f"{model_id}.yml"
    if not path.exists():
        raise ValueError(f"no registered entry metadata for model_id {model_id!r}")
    meta = yaml.safe_load(path.read_text()) or {}
    pem = meta.get("public_key_pem")
    if not pem:
        raise ValueError(f"entry {model_id!r} has no public_key_pem registered")
    return C.load_public(pem.encode() if isinstance(pem, str) else pem)


def _validated_request(payload: dict) -> C.CommitRequest:
    """Shared input gate for both front ends. Raises ValueError naming the field."""
    missing = [k for k in REQUIRED_REQUEST_KEYS if k not in payload]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")
    for field_name in ("round_id", "model_id"):
        value = payload[field_name]
        if not isinstance(value, str) or not IDENT_RE.match(value):
            raise ValueError(f"invalid {field_name}: {value!r}")
    if not isinstance(payload["commit"], str) or not COMMIT_RE.match(payload["commit"]):
        raise ValueError("invalid commit: expected 64 lowercase hex characters")
    try:
        bytes.fromhex(payload["team_signature"])
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid team_signature: not hex") from exc
    return C.CommitRequest(
        payload["round_id"], payload["model_id"], payload["commit"], payload["team_signature"]
    )


def submit_from_mapping(payload: dict, hub_sk, round_dir: str | Path, *,
                        metadata_dir: str | Path, now: datetime | None = None,
                        anchor: A.Anchor | None = None) -> tuple[C.Receipt | None, str]:
    """API-shaped front end: parse a mapping, then delegate."""
    req = _validated_request(payload)
    team_pk = load_team_key(req.model_id, metadata_dir)
    return submit_commitment(req, team_pk, hub_sk, round_dir, now=now, anchor=anchor)


def submit_from_sidecar_file(path: str | Path, hub_sk, round_dir: str | Path, *,
                             metadata_dir: str | Path, now: datetime | None = None,
                             anchor: A.Anchor | None = None) -> tuple[C.Receipt | None, str]:
    """PR-path front end: read the participant's sidecar file, then delegate."""
    path = Path(path)
    if not path.exists():
        raise ValueError(f"sidecar not found: {path}")
    size = path.stat().st_size
    if size > MAX_SIDECAR_BYTES:
        raise ValueError(f"sidecar exceeds {MAX_SIDECAR_BYTES} bytes: {size}")
    payload = json.loads(path.read_text())
    receipt, outcome = submit_from_mapping(
        payload, hub_sk, round_dir, metadata_dir=metadata_dir, now=now, anchor=anchor
    )
    if receipt is not None:
        # Write the hub receipt back into the participant's sidecar, preserving
        # the fields they supplied. The newest root is the one that covers them.
        accepted = [e["commit"] for e in read_ledger(round_dir) if e["outcome"] == ACCEPTED]
        doc = read_roots(round_dir)[-1]
        C.write_sidecar(
            path, receipt, C.merkle_proof(accepted, receipt.merkle_position), doc["root"],
            team_signature=payload["team_signature"],
            nonce_hash=payload.get("nonce_hash", ""),
            window_index=doc["window_index"],
        )
    return receipt, outcome
