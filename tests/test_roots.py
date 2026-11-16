"""Root publication: the chain a third party walks, and the anchor state machine.

Both tests are Wave 0 scaffolding for later plans and are marked strict xfail.
External anchoring is injected here, never shelled out — the unit suite stays
offline and fast.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from itertools import pairwise

BEFORE_COMMIT = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)


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


def test_anchor_status_transition(fixture_round):
    """COMMIT-03 / FREEZE-03: pending-calendar becomes bitcoin only when a proof verifies.

    The scheme is fc-ots/v1: the Merkle root is stamped and the upgrade path never rewrites
    the hub-signed <w>.json, so no later step can invalidate a published proof.
    """
    import struct
    import urllib.error
    from pathlib import Path

    from fc import anchor as A
    from fc import service as S

    fr = fixture_round
    req, _nonce = fr.request(b"sub", b"")
    _r, outcome = S.submit_commitment(req, fr.team_pk, fr.hub_sk, fr.round_dir, now=BEFORE_COMMIT)
    assert outcome == "accepted"
    roots = fr.round_dir / "roots"
    doc_bytes = (roots / "0.json").read_bytes()

    # Stamping is a separate step (`python -m fc.anchor stamp`), here with an injected POST.
    live = (Path(__file__).parent / "fixtures" / "ots" / "calendar-pending.bin").read_bytes()
    stamped = A.stamp_round_dir(
        fr.round_dir,
        calendars=("https://a.pool.opentimestamps.org", "https://b.pool.opentimestamps.org"),
        http_post=lambda url, body, timeout=20.0, max_bytes=10240: live,
    )
    assert stamped == [0]
    anchor_path = roots / "0.anchor.json"
    assert json.loads(anchor_path.read_text())["status"] == "pending-calendar"
    assert (roots / "0.root.ots").is_file()

    # 404 from the calendar: still pending, nothing rewritten.
    def not_yet(url, timeout=20.0, max_bytes=10240):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

    assert S.upgrade_pending_anchors(fr.round_dir, http_get=not_yet, headers=_Headers()) == 0
    assert json.loads(anchor_path.read_text())["status"] == "pending-calendar"

    # The calendar now returns a completed timestamp and the block header is available.
    proof = A.parse_ots((roots / "0.root.ots").read_bytes())
    (msg, att), *_ = [(m, a) for m, a in proof.timestamp.walk() if isinstance(a, A.Pending)]
    done = A.Timestamp(msg)
    node = done.add_op(A.Op(A._OP_SHA256))
    node.add_attestation(A.Bitcoin(950_001))
    head = (struct.pack("<i", 0x20000000) + bytes(32) + node.msg
            + struct.pack("<II", 1_789_000_000, 0x207FFFFF))
    nonce = 0
    while not A.BlockHeader(head + struct.pack("<I", nonce)).pow_ok:
        nonce += 1
    header = A.BlockHeader(head + struct.pack("<I", nonce))

    def completed(url, timeout=20.0, max_bytes=10240):
        if url == f"{att.uri}/timestamp/{msg.hex()}":
            return done.serialize()
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

    assert S.upgrade_pending_anchors(
        fr.round_dir, http_get=completed, headers=_Headers({950_001: header})) == 1
    anchor = json.loads(anchor_path.read_text())
    assert anchor["status"] == "bitcoin" and anchor["bitcoin_block_height"] == 950_001

    # The hub-signed root document is byte-identical, and the window verifies offline.
    assert (roots / "0.json").read_bytes() == doc_bytes
    verdict = A.verify_round_window(fr.round_dir, 0, A.HeaderFile(roots / "bitcoin-headers.json"))
    assert verdict.status == A.BITCOIN


class _Headers:
    def __init__(self, mapping=None):
        self.mapping = dict(mapping or {})

    def header(self, height):
        return self.mapping.get(height)
