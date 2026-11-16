"""COMMIT-04: a third party with no hub access verifies a commitment offline.

These three tests are the red gate that plan 01-01 Task 2 turns green. They call
`fc.service` and `fc.cli` exactly as a real participant and a real auditor would,
and they never touch the network.
"""

from __future__ import annotations

import json
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fc import anchor as A
from fc import cli
from fc import commit as C

BEFORE_COMMIT = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
BLOCK_HEIGHT = 912345
BLOCK_TIME_OK = "2026-09-09T13:00:00Z"
BLOCK_TIME_LATE = "2026-09-10T00:30:00Z"


def _anchor_the_root(round_dir: Path, window_index: int, block_time: str) -> None:
    """Write the artifacts a completed anchoring job would leave beside a root.

    A synthetic (regtest) proof that the window's Merkle root is in a block mined at
    `block_time`, the block header in the {height: {hash, header}} file a third party
    verifies against offline, and the fc-ots/v1 anchor record. The header is cheap, so
    verification needs the autouse floor fixture in conftest.py.
    """
    roots = round_dir / "roots"
    root_hex = json.loads((roots / f"{window_index}.json").read_text())["root"]
    when = datetime.fromisoformat(block_time)
    ots, header_hex = A.regtest_anchor(root_hex, when, BLOCK_HEIGHT)
    (roots / f"{window_index}.root.ots").write_bytes(ots)

    headers_file = roots / "bitcoin-headers.json"
    headers = json.loads(headers_file.read_text()) if headers_file.exists() else {}
    headers[str(BLOCK_HEIGHT)] = {
        "hash": A.BlockHeader.from_bytes(bytes.fromhex(header_hex)).block_hash_hex,
        "header": header_hex,
    }
    headers_file.write_text(json.dumps(headers, indent=2) + "\n")

    (roots / f"{window_index}.anchor.json").write_text(
        json.dumps(
            {
                "schema_version": "2026.08",
                "scheme": A.ANCHOR_SCHEME,
                "status": "bitcoin",
                "bitcoin_block_height": BLOCK_HEIGHT,
                "digest": root_hex,
                "calendar_uris": ["https://alice.btc.calendar.opentimestamps.org"],
                "rekor": {"log_index": 184223991, "integratedTime": 1789000000},
            },
            indent=2,
        )
        + "\n"
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


def _verify(sidecar, roots, **kw):
    from fc import verify_cli as V

    return V.verify(sidecar, roots_dir=roots, headers_path=roots / "bitcoin-headers.json", **kw)


def _check(rep, name):
    return next(c for c in rep.checks if c.name == name)


def test_anchor_passes_only_through_a_proof_verified_against_a_header(fixture_round):
    sidecar = _submit_one(fixture_round)
    rep = _verify(sidecar, fixture_round.round_dir / "roots")
    att = _check(rep, "anchor attests to root")
    assert att.ok and att.state == "PASS"
    assert "verified against the block header" in att.detail
    assert f"Bitcoin block {BLOCK_HEIGHT}" in att.detail
    assert "header file bitcoin-headers.json" in att.detail
    assert _check(rep, "anchor precedes T_commit").ok
    assert rep.ok is True


def test_anchor_record_without_a_proof_is_not_evidence(fixture_round):
    sidecar = _submit_one(fixture_round)
    roots = fixture_round.round_dir / "roots"
    (roots / "0.root.ots").unlink()
    assert (roots / "0.anchor.json").exists()  # still claims status: bitcoin
    rep = _verify(sidecar, roots)
    att = _check(rep, "anchor attests to root")
    assert not att.ok and att.state == "FAIL"
    assert "not evidence" in att.detail
    assert rep.ok is False


def test_legacy_height_to_time_headers_are_a_claim_not_a_header(fixture_round):
    sidecar = _submit_one(fixture_round)
    roots = fixture_round.round_dir / "roots"
    (roots / "bitcoin-headers.json").write_text(json.dumps({str(BLOCK_HEIGHT): BLOCK_TIME_OK}))
    rep = _verify(sidecar, roots)
    assert not _check(rep, "anchor attests to root").ok
    assert not _check(rep, "anchor precedes T_commit").ok
    assert rep.ok is False


def test_a_flipped_proof_byte_fails(fixture_round):
    sidecar = _submit_one(fixture_round)
    roots = fixture_round.round_dir / "roots"
    ots = bytearray((roots / "0.root.ots").read_bytes())
    ots[len(A.OTS_MAGIC) + 2] ^= 0x01  # first byte of the digest the proof is for
    (roots / "0.root.ots").write_bytes(bytes(ots))
    rep = _verify(sidecar, roots)
    att = _check(rep, "anchor attests to root")
    assert not att.ok and att.state == "FAIL"
    assert rep.ok is False


def test_two_hour_margin_counts_against_the_deadline(fixture_round):
    """Block time 22:30Z is before T_commit 23:59Z, but 22:30 + 2 h is not."""
    sidecar = _submit_one(fixture_round, block_time="2026-09-09T22:30:00Z")
    rep = _verify(sidecar, fixture_round.round_dir / "roots")
    assert _check(rep, "anchor attests to root").ok
    late = _check(rep, "anchor precedes T_commit")
    assert not late.ok
    assert "2 h margin" in late.detail
    assert rep.ok is False


@pytest.mark.real_work_floor
def test_cheap_regtest_header_is_rejected_under_the_production_floor(fixture_round):
    assert A.MIN_HEADER_WORK_BITS == 70
    sidecar = _submit_one(fixture_round)
    rep = _verify(sidecar, fixture_round.round_dir / "roots")
    att = _check(rep, "anchor attests to root")
    assert not att.ok and att.state != "PASS"
    assert rep.ok is False


def test_fetch_takes_a_header_from_two_agreeing_explorers(fixture_round, monkeypatch):
    sidecar = _submit_one(fixture_round)
    roots = fixture_round.round_dir / "roots"
    entry = json.loads((roots / "bitcoin-headers.json").read_text())[str(BLOCK_HEIGHT)]
    (roots / "bitcoin-headers.json").write_text("{}")  # nothing offline
    assert not _check(_verify(sidecar, roots), "anchor attests to root").ok

    def fake_http_get(url, timeout, max_bytes):
        if "/block-height/" in url:
            return entry["hash"].encode()
        if url.endswith(f"/block/{entry['hash']}/header"):
            return entry["header"].encode()
        raise OSError(url)

    real = A.ExplorerHeaders
    monkeypatch.setattr(A, "ExplorerHeaders", lambda: real(http_get=fake_http_get))
    rep = _verify(sidecar, roots, fetch=True)
    att = _check(rep, "anchor attests to root")
    assert att.ok
    assert "blockstream.info" in att.detail and "mempool.space" in att.detail
    assert rep.ok is True


def test_entry_verify_passes_fetch_through(fixture_round, monkeypatch):
    from fc import freeze as F
    from fc import verify_cli as V

    seen = {}

    def fake_verify_entry(path, **kw):
        seen.update(kw)
        rep = V.VerifyReport()
        rep.add("merkle inclusion", True, "stub", V.TRUSTLESS)
        return rep

    monkeypatch.setattr(F, "verify_entry", fake_verify_entry)
    with pytest.raises(SystemExit) as exc:
        cli.main(["verify", "--entry", "m.yml", "--season-dir", "s",
                  "--before", "2026-09-10T00:00:00+00:00", "--fetch"])
    assert exc.value.code == 0
    assert seen["fetch"] is True
    with pytest.raises(SystemExit):
        cli.main(["verify", "--entry", "m.yml", "--season-dir", "s",
                  "--before", "2026-09-10T00:00:00+00:00"])
    assert seen["fetch"] is False
