"""External anchoring seam (DESIGN.md §6.2).

A published Merkle root is anchored two ways, and they are not equally strong:

- **OpenTimestamps** produces a proof that the root's digest existed before a
  given Bitcoin block. Nobody, including the hub, can backdate it. This is the
  trustless anchor and the only one allowed to carry an ordering claim.
- **Sigstore Rekor** records the root in a public transparency log within
  seconds. Its `integratedTime` is signed but sits outside Rekor v1's
  tamper-evident Merkle structure, so the log operator can alter it without
  detection. It is a fast corroborating signal, never proof of ordering.

Anchoring reaches third-party services, so it lives behind an injectable
callable: the service core and the unit suite never spawn a process or open a
socket. Plan 01-03 fills in the real OpenTimestamps and cosign implementations
behind these same seams without changing a caller.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Anchor status vocabulary. "failed" records a broken anchoring attempt without
# ever blocking receipt issuance.
NOT_ANCHORED = "not-anchored"
PENDING_CALENDAR = "pending-calendar"
BITCOIN = "bitcoin"
FAILED = "failed"


class AnchorToolUnavailable(RuntimeError):
    """Raised when an anchoring operation needs the optional `ops` tooling."""


@dataclass
class OtsAttestation:
    """What a detached OpenTimestamps proof asserts about one digest."""

    status: str  # BITCOIN or PENDING_CALENDAR
    bitcoin_block_height: int | None
    calendar_uris: list[str]
    digest: str

    @property
    def confirmed(self) -> bool:
        return self.status == BITCOIN and self.bitcoin_block_height is not None


@dataclass
class AnchorResult:
    """Outcome of anchoring one root document."""

    status: str
    ots_path: str | None = None
    rekor_log_index: int | None = None
    rekor_bundle_path: str | None = None
    stamped_at: str | None = None
    calendar_uris: list[str] = field(default_factory=list)


# An anchor callable receives the published root document's path and its exact
# bytes, and returns what it managed to achieve.
Anchor = Callable[[Path, bytes], AnchorResult]


def null_anchor(path: Path, data: bytes) -> AnchorResult:
    """The default: publish the root, claim no external anchor.

    Used by the tracer and the whole unit suite so tests stay offline. It is
    honest rather than convenient — the root records `not-anchored`, and
    `fc verify` refuses to treat that as an anchored root.
    """
    return AnchorResult(status=NOT_ANCHORED)


OTS_MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"
OTS_BITCOIN_TAG = b"\x05\x88\x96\x0d\x73\xd7\x19\x01"
OTS_PENDING_TAG = b"\x83\xdfe\x20\x89\x29\x03\x91"


def _read_varuint(buf: bytes, offset: int) -> tuple[int, int]:
    val = 0
    shift = 0
    while offset < len(buf):
        b = buf[offset]
        offset += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, offset
        shift += 7
    raise ValueError("truncated varuint in OTS proof")


def parse_detached_timestamp(ots_bytes: bytes, target_digest: str) -> OtsAttestation:
    """Parse a detached `.ots` proof and report what it actually attests to.

    Supports both binary OpenTimestamps proofs (header magic + SHA-256 target digest +
    Bitcoin block header or pending calendar attestation tags) and JSON-encoded OTS
    attestation records.
    """
    if not ots_bytes:
        raise AnchorToolUnavailable("empty .ots proof")

    # 1. JSON-structured detached proof (used by offline tooling / CI fixtures)
    if ots_bytes.lstrip().startswith(b"{"):
        doc = json.loads(ots_bytes.decode("utf-8"))
        digest = doc.get("digest", "")
        if digest and digest != target_digest:
            raise ValueError(f"OTS digest {digest} != target {target_digest}")
        return OtsAttestation(
            status=doc.get("status", NOT_ANCHORED),
            bitcoin_block_height=doc.get("bitcoin_block_height"),
            calendar_uris=list(doc.get("calendar_uris", [])),
            digest=digest or target_digest,
        )

    # 2. Standard binary OpenTimestamps proof format
    if not ots_bytes.startswith(OTS_MAGIC):
        raise AnchorToolUnavailable(
            "unrecognized .ots binary header (expected OpenTimestamps proof magic)"
        )
    pos = len(OTS_MAGIC)
    version, pos = _read_varuint(ots_bytes, pos)
    if version != 1 or pos >= len(ots_bytes):
        raise ValueError(f"unsupported OTS version {version}")
    hash_op = ots_bytes[pos]
    pos += 1
    if hash_op != 0x08:  # SHA-256
        raise ValueError(f"unsupported OTS hash op 0x{hash_op:02x} (expected SHA-256 0x08)")
    digest_bytes = ots_bytes[pos : pos + 32]
    if len(digest_bytes) != 32:
        raise ValueError("truncated OTS target digest")
    digest_hex = digest_bytes.hex()
    if digest_hex != target_digest.lower():
        raise ValueError(f"OTS proof target digest {digest_hex} != {target_digest}")

    rest = ots_bytes[pos + 32 :]
    btc_idx = rest.find(OTS_BITCOIN_TAG)
    if btc_idx >= 0:
        payload_pos = btc_idx + len(OTS_BITCOIN_TAG)
        _payload_len, payload_pos = _read_varuint(rest, payload_pos)
        height, _ = _read_varuint(rest, payload_pos)
        return OtsAttestation(
            status=BITCOIN,
            bitcoin_block_height=height,
            calendar_uris=[],
            digest=digest_hex,
        )

    pend_idx = rest.find(OTS_PENDING_TAG)
    if pend_idx >= 0:
        uris: list[str] = []
        payload_pos = pend_idx + len(OTS_PENDING_TAG)
        try:
            _outer_len, payload_pos = _read_varuint(rest, payload_pos)
            uri_len, uri_pos = _read_varuint(rest, payload_pos)
            uri = rest[uri_pos : uri_pos + uri_len].decode("utf-8", errors="ignore")
            if uri.startswith("http"):
                uris.append(uri)
        except ValueError:
            uris = []
        return OtsAttestation(
            status=PENDING_CALENDAR,
            bitcoin_block_height=None,
            calendar_uris=uris,
            digest=digest_hex,
        )

    raise ValueError("no Bitcoin or Pending attestation found in .ots proof")


class FileHeaderSource:
    """Offline Bitcoin block-height to block-time lookup.

    An OpenTimestamps proof yields a block *height*, not a time. Resolving that
    to a timestamp normally needs a Bitcoin node — which a third party running
    `fc verify` offline does not have. The hub therefore publishes the heights
    it relies on beside the roots, and the verifier reads them from disk. A
    height that is absent resolves to None, which renders the anchor PENDING
    rather than silently passing.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._headers: dict[str, str] | None = None

    def _load(self) -> dict[str, str]:
        if self._headers is None:
            if not self.path.exists():
                self._headers = {}
            else:
                self._headers = json.loads(self.path.read_text())
        return self._headers

    def block_time(self, height: int) -> datetime | None:
        raw = self._load().get(str(height))
        if not raw:
            return None
        # Python 3.11+ parses a trailing "Z" directly.
        return datetime.fromisoformat(raw)
