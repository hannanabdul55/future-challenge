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


def parse_detached_timestamp(ots_bytes: bytes, target_digest: str) -> OtsAttestation:
    """Parse a detached `.ots` proof and report what it actually attests to.

    Declared seam, not a silent no-op: until plan 01-03 installs the `ops`
    extra, calling this raises rather than returning an unverified "looks fine".
    """
    raise AnchorToolUnavailable(
        "OpenTimestamps parsing requires the ops extra: uv sync --extra ops"
    )


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
