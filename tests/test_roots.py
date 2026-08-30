"""Root publication: the chain a third party walks, and the anchor state machine.

Both tests are Wave 0 scaffolding for later plans and are marked strict xfail.
External anchoring is injected here, never shelled out — the unit suite stays
offline and fast.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

BEFORE_COMMIT = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)


@pytest.mark.xfail(strict=True, reason="Wave 0 scaffold — implemented in plan 01-04")
def test_root_chain_integrity(fixture_round):
    """COMMIT-02: roots chain contiguously, so a dropped root is detectable."""
    from fc import service as S

    fr = fixture_round
    for i in range(3):
        req, _nonce = fr.request(f"sub-{i}".encode(), b"", model_id=f"team-{i}")
        _r, outcome = S.submit_commitment(
            req,
            fr.team_pk,
            fr.hub_sk,
            fr.round_dir,
            now=BEFORE_COMMIT + timedelta(minutes=i),
        )
        assert outcome == "accepted"

    roots = S.read_roots(fr.round_dir)
    assert [r["window_index"] for r in roots] == [0, 1, 2]
    assert roots[0]["prev_root"] is None
    for prev, cur in pairwise(roots):
        assert cur["prev_root"] == prev["root"]
    # Each root covers every commitment accepted so far, not just the newest.
    assert [r["commit_count"] for r in roots] == [1, 2, 3]

    cadence = json.loads((fr.round_dir / "roots" / "cadence.json").read_text())
    assert "gaps" in cadence


@pytest.mark.xfail(strict=True, reason="Wave 0 scaffold — implemented in plan 01-03")
def test_anchor_status_transition(fixture_round):
    """COMMIT-03: pending-calendar becomes bitcoin only when the attestation says so."""
    from fc import anchor as A
    from fc import service as S

    fr = fixture_round
    calls: list[str] = []

    def pending_anchor(path, data):
        calls.append(str(path))
        return A.AnchorResult(
            status="pending-calendar",
            ots_path=str(path) + ".ots",
            rekor_log_index=184223991,
            rekor_bundle_path=str(path) + ".sigstore.json",
            stamped_at="2026-09-09T12:00:00.000Z",
        )

    req, _nonce = fr.request(b"sub", b"")
    _r, outcome = S.submit_commitment(
        req, fr.team_pk, fr.hub_sk, fr.round_dir, now=BEFORE_COMMIT, anchor=pending_anchor
    )
    assert outcome == "accepted"
    assert len(calls) == 1

    doc = json.loads((fr.round_dir / "roots" / "0.json").read_text())
    assert doc["anchor_status"] == "pending-calendar"

    # The upgrade sweep promotes only when a Bitcoin attestation is actually present.
    def still_pending(ots_bytes, target_digest):
        return A.OtsAttestation(
            status="pending-calendar", bitcoin_block_height=None, calendar_uris=[], digest=target_digest
        )

    S.upgrade_pending_anchors(fr.round_dir, ots_parser=still_pending)
    doc = json.loads((fr.round_dir / "roots" / "0.json").read_text())
    assert doc["anchor_status"] == "pending-calendar"

    def confirmed(ots_bytes, target_digest):
        return A.OtsAttestation(
            status="bitcoin", bitcoin_block_height=912345, calendar_uris=[], digest=target_digest
        )

    S.upgrade_pending_anchors(fr.round_dir, ots_parser=confirmed)
    doc = json.loads((fr.round_dir / "roots" / "0.json").read_text())
    assert doc["anchor_status"] == "bitcoin"
