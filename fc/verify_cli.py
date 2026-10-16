"""Third-party verification of one commitment, offline (COMMIT-04).

Runs against nothing but a directory of published artifacts — no hub key, no
hub cooperation, no network. See `verify` for the check list and trust model:
`report.ok` is the conjunction of the `trustless` checks alone, so neither a
hub signature nor a Rekor timestamp can carry the verdict by itself.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from secrets import compare_digest

from . import anchor as A
from . import commit as C
from . import service as S

TRUSTLESS = "trustless"
HUB_ASSERTED = "hub-asserted"
OPERATOR_ASSERTED = "operator-asserted"

PASS, FAIL, PENDING = "PASS", "FAIL", "PENDING"

# A hostile artifact must not be able to exhaust memory before it is rejected.
MAX_ARTIFACT_BYTES = 1024 * 1024

REQUIRED_SIDECAR_KEYS = ("round_id", "model_id", "commit", "hub_receipt")
REQUIRED_RECEIPT_KEYS = ("merkle_root", "merkle_path", "merkle_position", "server_time")


class ArtifactError(ValueError):
    """A published artifact is missing, oversized, or malformed."""


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    trust: str
    state: str = PASS

    @property
    def required(self) -> bool:
        return self.trust == TRUSTLESS


@dataclass
class VerifyReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str, trust: str, state: str | None = None) -> Check:
        c = Check(name, ok, detail, trust, state or (PASS if ok else FAIL))
        self.checks.append(c)
        return c

    @property
    def ok(self) -> bool:
        """True only when every trustless check passed."""
        required = [c for c in self.checks if c.required]
        return bool(required) and all(c.ok for c in required)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "checks": [
                {"name": c.name, "state": c.state, "trust": c.trust, "detail": c.detail}
                for c in self.checks
            ],
        }

    def __str__(self) -> str:
        width = max((len(c.name) for c in self.checks), default=0)
        lines = [
            f"{c.state:<7} {c.name:<{width}}  [{c.trust}]  {c.detail}" for c in self.checks
        ]
        lines.append("")
        lines.append(f"VERDICT: {'ok' if self.ok else 'FAILED'}  "
                     f"(trustless checks decide; see labels above)")
        return "\n".join(lines)


def _read_json(path: Path, what: str) -> dict:
    if not path.exists():
        raise ArtifactError(f"{what} not found: {path}")
    size = path.stat().st_size
    if size > MAX_ARTIFACT_BYTES:
        raise ArtifactError(f"{what} exceeds {MAX_ARTIFACT_BYTES} bytes: {size}")
    return json.loads(path.read_text())


def verify(sidecar_path: str | Path, *, roots_dir=None, manifest_path=None,
           hub_key_path=None, headers_path=None, allow_pending: bool = False,
           ots_parser=None, fetch: bool = False,
           before: datetime | None = None, before_label: str = "T_commit") -> VerifyReport:
    """Build the verification report for one commitment record.

    `before` overrides the instant the anchor must precede (default: the round
    manifest's `T_commit`). A frozen-model entry passes a daily round's `T_open`
    here (rules 2026.10 §3) and `before_label` names it in the report.
    """
    rep = VerifyReport()
    sidecar_path = Path(sidecar_path)
    ots_parser = ots_parser or A.parse_detached_timestamp
    anchor_check = f"anchor precedes {before_label}"
    receipt_check = f"receipt server_time precedes {before_label}"

    # ---------------------------------------------------------- sidecar shape
    try:
        sidecar = _read_json(sidecar_path, "sidecar")
        missing = [k for k in REQUIRED_SIDECAR_KEYS if k not in sidecar]
        receipt = sidecar.get("hub_receipt") or {}
        missing += [f"hub_receipt.{k}" for k in REQUIRED_RECEIPT_KEYS if k not in receipt]
        if missing:
            raise ArtifactError(f"missing keys: {', '.join(missing)}")
        bytes.fromhex(sidecar["commit"])
        rep.add("sidecar well-formed", True,
                f"{sidecar['model_id']} in round {sidecar['round_id']}", HUB_ASSERTED)
    except (ArtifactError, ValueError, json.JSONDecodeError) as exc:
        rep.add("sidecar well-formed", False, str(exc), HUB_ASSERTED)
        rep.add("merkle inclusion", False, "not attempted — sidecar unreadable", TRUSTLESS)
        return rep

    if roots_dir is None:
        roots_dir = sidecar_path.parent
    roots_dir = Path(roots_dir)
    round_dir = roots_dir.parent
    manifest_path = Path(manifest_path) if manifest_path else round_dir / "manifest.json"

    # ------------------------------------------------------- merkle inclusion
    proof = [tuple(p) for p in receipt["merkle_path"]]
    root_hex = receipt["merkle_root"]
    included = C.merkle_verify(sidecar["commit"], proof, root_hex)
    rep.add("merkle inclusion", included,
            f"commit sits at position {receipt['merkle_position']} under root {root_hex[:16]}…",
            TRUSTLESS)

    # ------------------------------------------------- published root document
    root_doc = None
    try:
        docs = S.read_roots(round_dir)
        for d in docs:
            if compare_digest(d["root"], root_hex):
                root_doc = d
                break
        rep.add("root published", root_doc is not None,
                (f"window {root_doc['window_index']} covering {root_doc['commit_count']} "
                 f"commitment(s)") if root_doc
                else f"no published root matches {root_hex[:16]}…",
                TRUSTLESS)
    except (OSError, ValueError, KeyError) as exc:
        rep.add("root published", False, f"roots unreadable: {exc}", TRUSTLESS)

    if root_doc is None:
        rep.add("anchor attests to root", False, "not attempted — no root document", TRUSTLESS)
        rep.add(anchor_check, False, "not attempted — no root document", TRUSTLESS)
        return rep

    window = root_doc["window_index"]
    root_file = roots_dir / f"{window}.json"

    # ------------------------------------------------ OpenTimestamps attestation
    attestation = None
    anchor_rec: dict = {}
    try:
        anchor_rec = _read_json(roots_dir / f"{window}.anchor.json", "anchor record")
        file_digest = hashlib.sha256(root_file.read_bytes()).hexdigest()
        digest_ok = compare_digest(anchor_rec.get("digest", ""), file_digest)

        ots_file = roots_dir / f"{window}.json.ots"
        if ots_file.exists():
            # Byte-level proof present: it, not the record's own claim, decides.
            attestation = ots_parser(ots_file.read_bytes(), file_digest)
            status = attestation.status
            height = attestation.bitcoin_block_height
            provenance = "parsed from the detached .ots proof"
        else:
            status = anchor_rec.get("status", A.NOT_ANCHORED)
            height = anchor_rec.get("bitcoin_block_height")
            provenance = ("as recorded beside the root; the .ots proof itself was not "
                          "parsed, so only the digest binding is cryptographic here")

        if not digest_ok:
            rep.add("anchor attests to root", False,
                    "anchor record digest does not match the published root document",
                    TRUSTLESS)
        elif status == A.BITCOIN:
            rep.add("anchor attests to root", True,
                    f"Bitcoin block {height} — {provenance}", TRUSTLESS)
        elif status == A.PENDING_CALENDAR:
            rep.add("anchor attests to root", bool(allow_pending),
                    f"calendar accepted the digest, Bitcoin confirmation pending "
                    f"({provenance})", TRUSTLESS, state=PENDING)
        else:
            rep.add("anchor attests to root", False,
                    f"root is not anchored (status: {status})", TRUSTLESS)
    except (ArtifactError, A.AnchorToolUnavailable, ValueError, OSError,
            json.JSONDecodeError) as exc:
        rep.add("anchor attests to root", False, str(exc), TRUSTLESS)
        height, status = None, A.NOT_ANCHORED

    # --------------------------------------------- anchor time precedes the deadline
    try:
        t_commit = before or datetime.fromisoformat(
            _read_json(manifest_path, "manifest")["T_commit"])
        headers = A.FileHeaderSource(headers_path or roots_dir / "bitcoin-headers.json")
        block_time = headers.block_time(height) if height is not None else None
        if block_time is None:
            rep.add(anchor_check, False,
                    "no block time available for the anchoring block — cannot place the "
                    "commitment in time", TRUSTLESS, state=PENDING)
        else:
            precedes = block_time < t_commit
            rep.add(anchor_check, precedes,
                    f"anchored {block_time.isoformat()} vs {before_label} "
                    f"{t_commit.isoformat()}", TRUSTLESS)
    except (ArtifactError, ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        rep.add(anchor_check, False, str(exc), TRUSTLESS)

    # ------------------------------------------------------------------ Rekor
    # The anchoring job records Rekor beside the root, and may do so after the
    # root was first published (an upgrade sweep rewrites it), so the anchor
    # record wins over whatever the root document captured at publication time.
    rekor = anchor_rec.get("rekor") or root_doc.get("rekor") or {}
    log_index = rekor.get("log_index")
    rep.add("rekor entry", log_index is not None,
            (f"log index {log_index}; Rekor v1 integratedTime is signed but sits OUTSIDE the "
             f"tamper-evident structure and is alterable by the log operator without "
             f"detection, so it cannot carry the ordering claim on its own")
            if log_index is not None else "no Rekor entry recorded",
            OPERATOR_ASSERTED)

    # ------------------------------------------------------- hub-signed claims
    if hub_key_path:
        try:
            hub_pk = C.load_public(Path(hub_key_path).read_bytes())
            rep.add("hub signature on root", S.verify_root_signature(hub_pk, root_doc),
                    "root document signed by the published hub key", HUB_ASSERTED)
        except (OSError, ValueError, TypeError) as exc:
            rep.add("hub signature on root", False, str(exc), HUB_ASSERTED)

    try:
        t_commit = before or datetime.fromisoformat(
            _read_json(manifest_path, "manifest")["T_commit"])
        server_time = datetime.fromisoformat(receipt["server_time"])
        rep.add(receipt_check, server_time < t_commit,
                f"hub recorded {receipt['server_time']}", HUB_ASSERTED)
    except (ArtifactError, ValueError, KeyError, json.JSONDecodeError) as exc:
        rep.add(receipt_check, False, str(exc), HUB_ASSERTED)

    return rep
