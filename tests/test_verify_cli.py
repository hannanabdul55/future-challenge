"""COMMIT-04: a third party with no hub access verifies a commitment offline.

These three tests are the red gate that plan 01-01 Task 2 turns green. They call
`fc.service` and `fc.cli` exactly as a real participant and a real auditor would,
and they never touch the network.
"""

from __future__ import annotations

import hashlib
import json
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fc import cli
from fc import commit as C

BEFORE_COMMIT = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
BLOCK_HEIGHT = 912345
BLOCK_TIME_OK = "2026-09-09T13:00:00Z"
BLOCK_TIME_LATE = "2026-09-10T00:30:00Z"


def _anchor_the_root(round_dir: Path, window_index: int, block_time: str) -> None:
    """Write the artifacts a completed anchoring job would leave beside a root.

    The OpenTimestamps proof attests to the digest of the published root document
    file; the Bitcoin header source maps that block height to a time, offline.
    """
    roots = round_dir / "roots"
    root_file = roots / f"{window_index}.json"
    digest = hashlib.sha256(root_file.read_bytes()).hexdigest()

    (roots / f"{window_index}.anchor.json").write_text(
        json.dumps(
            {
                "schema_version": "2026.08",
                "status": "bitcoin",
                "bitcoin_block_height": BLOCK_HEIGHT,
                "digest": digest,
                "calendar_uris": ["https://alice.btc.calendar.opentimestamps.org"],
                "rekor": {"log_index": 184223991, "integratedTime": 1789000000},
            },
            indent=2,
        )
        + "\n"
    )
    (roots / "bitcoin-headers.json").write_text(
        json.dumps({str(BLOCK_HEIGHT): block_time}, indent=2) + "\n"
    )


def _submit_one(fr, *, block_time: str = BLOCK_TIME_OK) -> Path:
    """Submit one commitment, anchor its root, and write the participant sidecar."""
    from fc import service as S

    req, nonce = fr.request(b"submission-bytes", b"rationale-bytes")
    receipt, outcome = S.submit_commitment(
        req, fr.team_pk, fr.hub_sk, fr.round_dir, now=BEFORE_COMMIT
    )
    assert outcome == "accepted"
    assert receipt is not None
    assert receipt.hub_signature
    assert receipt.merkle_position == 0

    root_doc = json.loads((fr.round_dir / "roots" / "0.json").read_text())
    _anchor_the_root(fr.round_dir, 0, block_time)

    sidecar = fr.root / "sidecar" / "acme-v3" / f"{fr.round_id}.json"
    C.write_sidecar(
        sidecar,
        receipt,
        C.merkle_proof([req.commit], 0),
        root_doc["root"],
        team_signature=req.team_signature,
        nonce_hash=C.nonce_hash(nonce),
        window_index=0,
    )
    return sidecar


def test_tracer_end_to_end(fixture_round):
    """The whole spine: commitment in, receipt out, root published, anchored, verified."""
    sidecar = _submit_one(fixture_round)
    roots = fixture_round.round_dir / "roots"

    with pytest.raises(SystemExit) as exc:
        cli.main(
            [
                "verify",
                "--sidecar",
                str(sidecar),
                "--roots",
                str(roots),
                "--bitcoin-headers",
                str(roots / "bitcoin-headers.json"),
            ]
        )
    assert exc.value.code == 0


def test_verify_offline_success(fixture_round, monkeypatch):
    """COMMIT-04 literally: only public artifacts, no hub key, no network."""
    sidecar = _submit_one(fixture_round)
    roots = fixture_round.round_dir / "roots"

    def _no_network(*args, **kwargs):
        raise AssertionError("fc verify must not open a socket")

    monkeypatch.setattr(socket, "socket", _no_network)

    with pytest.raises(SystemExit) as exc:
        cli.main(
            [
                "verify",
                "--sidecar",
                str(sidecar),
                "--roots",
                str(roots),
                "--bitcoin-headers",
                str(roots / "bitcoin-headers.json"),
            ]
        )
    assert exc.value.code == 0


def test_verify_rejects_anchor_after_t_commit(fixture_round):
    """An anchor that lands after T_commit proves nothing — the verdict must fail."""
    sidecar = _submit_one(fixture_round, block_time=BLOCK_TIME_LATE)
    roots = fixture_round.round_dir / "roots"

    with pytest.raises(SystemExit) as exc:
        cli.main(
            [
                "verify",
                "--sidecar",
                str(sidecar),
                "--roots",
                str(roots),
                "--bitcoin-headers",
                str(roots / "bitcoin-headers.json"),
            ]
        )
    assert exc.value.code != 0


def test_rekor_is_reported_but_not_load_bearing(fixture_round):
    """Rekor must be surfaced, labelled operator-asserted, and never gate the verdict."""
    from fc import verify_cli as V

    sidecar = _submit_one(fixture_round)
    roots = fixture_round.round_dir / "roots"
    rep = V.verify(
        sidecar, roots_dir=roots, headers_path=roots / "bitcoin-headers.json"
    )

    rekor = [c for c in rep.checks if c.name == "rekor entry"]
    assert len(rekor) == 1
    assert rekor[0].trust == "operator-asserted"
    # It is read from the anchor record the anchoring job writes, not left unseen.
    assert "184223991" in rekor[0].detail
    # The verdict rests on the trustless checks alone.
    assert rep.ok is True
    assert all(c.trust == "trustless" for c in rep.checks if c.required)
