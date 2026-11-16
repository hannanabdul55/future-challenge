"""OpenTimestamps codec and Bitcoin header verification (FREEZE-03, plan 06-03).

Every test is offline: the proofs and block headers are committed under
tests/fixtures/ots/ (upstream example proofs, headers cross-checked on two public
explorers). Tests that depend on the production work floor set it explicitly with
monkeypatch so a later autouse fixture cannot change their meaning.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fc import anchor as A

FIX = Path(__file__).parent / "fixtures" / "ots"
HEADERS = FIX / "headers.json"

GDP = FIX / "gdp2q25-2nd.pdf.ots"
GDP_HEIGHT = 912095
GDP_HASH = "000000000000000000007f0753c6209263c7072e51b725e89e75a0c708b90345"


def _gdp() -> bytes:
    return GDP.read_bytes()


# ---------------------------------------------------------------------- tags / codec


def test_tag_constants_match_upstream():
    assert A.PENDING_TAG == bytes.fromhex("83dfe30d2ef90c8e")
    assert A.OTS_PENDING_TAG == A.PENDING_TAG
    assert A.BITCOIN_TAG == bytes.fromhex("0588960d73d71901")
    assert A.ANCHOR_SCHEME == "fc-ots/v1"
    assert A.INVALID == "invalid"
    assert A.BLOCK_TIME_MARGIN_S == 7200


@pytest.mark.parametrize("n", [0, 1, 127, 128, 300, 912095, 2**32, 2**63])
def test_varuint_round_trip(n):
    enc = A._write_varuint(n)
    assert A._Reader(enc).varuint() == n


def test_non_canonical_varuint_rejected():
    with pytest.raises(ValueError):
        A._Reader(b"\x80\x00").varuint()


def test_gdp_round_trips_byte_identically():
    data = _gdp()
    proof = A.parse_ots(data)
    assert A.serialize_ots(proof) == data
    assert len(proof.digest) == 32


def test_gdp_contains_a_bitcoin_attestation_at_the_expected_height():
    proof = A.parse_ots(_gdp())
    atts = [a for _msg, a in proof.timestamp.walk() if isinstance(a, A.Bitcoin)]
    assert [a.height for a in atts] == [GDP_HEIGHT]


def test_block_header_fields():
    raw = bytes.fromhex(json.loads(HEADERS.read_text())[str(GDP_HEIGHT)]["header"])
    hdr = A.BlockHeader.from_bytes(raw)
    assert hdr.block_hash_hex == GDP_HASH
    assert hdr.pow_ok
    assert hdr.work_bits > 70
    assert len(hdr.merkle_root) == 32


def test_block_header_rejects_wrong_length():
    with pytest.raises(ValueError):
        A.BlockHeader.from_bytes(b"\x00" * 79)


# -------------------------------------------------------------------- verification


def test_gdp_verifies_offline_against_its_real_header(monkeypatch):
    monkeypatch.setattr(A, "MIN_HEADER_WORK_BITS", 70)
    hdr = json.loads(HEADERS.read_text())[str(GDP_HEIGHT)]
    ntime = A.BlockHeader.from_bytes(bytes.fromhex(hdr["header"])).time
    proof = A.parse_ots(_gdp())
    v = A.verify_digest_anchor(proof.digest, proof, A.HeaderFile(HEADERS))
    assert v.status == A.BITCOIN
    assert v.height == GDP_HEIGHT
    assert v.block_hash == GDP_HASH
    assert v.block_time == datetime.fromtimestamp(ntime, UTC)
    assert v.anchored_before == v.block_time + timedelta(hours=2)
    assert v.header_work_bits is not None and v.header_work_bits >= 70


def test_verify_accepts_hex_digest(monkeypatch):
    monkeypatch.setattr(A, "MIN_HEADER_WORK_BITS", 70)
    proof = A.parse_ots(_gdp())
    v = A.verify_digest_anchor(proof.digest.hex(), proof, A.HeaderFile(HEADERS))
    assert v.status == A.BITCOIN


def test_flipped_op_argument_byte_is_invalid(monkeypatch):
    monkeypatch.setattr(A, "MIN_HEADER_WORK_BITS", 70)
    data = bytearray(_gdp())
    first_op = len(A.OTS_MAGIC) + 1 + 1 + 32  # version, file hash op, digest
    assert data[first_op] in (0xF0, 0xF1) and data[first_op + 1] < 0x80
    data[first_op + 2 + 10] ^= 0x01
    proof = A.parse_ots(bytes(data))
    v = A.verify_digest_anchor(proof.digest, proof, A.HeaderFile(HEADERS))
    assert v.status == A.INVALID
    assert f"does not match block {GDP_HEIGHT} merkle root" in v.detail


def test_wrong_digest_is_invalid(monkeypatch):
    monkeypatch.setattr(A, "MIN_HEADER_WORK_BITS", 70)
    proof = A.parse_ots(_gdp())
    v = A.verify_digest_anchor(b"\x11" * 32, proof, A.HeaderFile(HEADERS))
    assert v.status == A.INVALID


def test_header_below_the_work_floor_is_never_bitcoin(monkeypatch):
    monkeypatch.setattr(A, "MIN_HEADER_WORK_BITS", 200)  # real header carries ~2^78
    proof = A.parse_ots(_gdp())
    v = A.verify_digest_anchor(proof.digest, proof, A.HeaderFile(HEADERS))
    assert v.status != A.BITCOIN


def test_missing_header_is_pending_with_a_hint(monkeypatch, tmp_path):
    monkeypatch.setattr(A, "MIN_HEADER_WORK_BITS", 70)
    proof = A.parse_ots(_gdp())
    v = A.verify_digest_anchor(proof.digest, proof, A.HeaderFile(tmp_path / "none.json"))
    assert v.status == A.PENDING_CALENDAR
    assert f"header for block {GDP_HEIGHT} not available" in v.detail


def test_a_claimed_block_time_alone_is_not_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(A, "MIN_HEADER_WORK_BITS", 0)
    legacy = tmp_path / "bitcoin-headers.json"
    legacy.write_text(json.dumps({str(GDP_HEIGHT): "2025-08-01T00:00:00Z"}))
    assert A.HeaderFile(legacy).header(GDP_HEIGHT) is None
    proof = A.parse_ots(_gdp())
    v = A.verify_digest_anchor(proof.digest, proof, A.HeaderFile(legacy))
    assert v.status != A.BITCOIN


def test_tree_without_attestation_is_not_anchored():
    digest = b"\x22" * 32
    proof = A.DetachedProof(digest=digest, timestamp=A.Timestamp(digest))
    v = A.verify_digest_anchor(digest, proof, A.HeaderFile(HEADERS))
    assert v.status == A.NOT_ANCHORED


def test_legacy_names_still_importable():
    for name in (
        "null_anchor", "AnchorResult", "Anchor", "AnchorToolUnavailable",
        "parse_detached_timestamp", "FileHeaderSource", "OtsAttestation",
        "OTS_MAGIC", "OTS_BITCOIN_TAG", "NOT_ANCHORED", "PENDING_CALENDAR", "BITCOIN",
        "FAILED",
    ):
        assert hasattr(A, name), name
