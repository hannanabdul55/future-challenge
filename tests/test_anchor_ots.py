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


def test_names_the_service_relies_on_are_still_importable():
    for name in (
        "null_anchor", "AnchorResult", "Anchor", "AnchorToolUnavailable",
        "OTS_MAGIC", "OTS_BITCOIN_TAG", "NOT_ANCHORED", "PENDING_CALENDAR", "BITCOIN",
        "FAILED",
    ):
        assert hasattr(A, name), name


def test_legacy_heuristic_parser_is_gone():
    """Only the verified OTS path remains (plan 06-13): no parser that trusts a status
    string or a block time typed into a header file."""
    for name in ("parse_detached_timestamp", "FileHeaderSource", "OtsAttestation"):
        assert not hasattr(A, name), name


# ================================================== Task 2: more proofs, forged headers,
# ================================================== explorers, limits, regtest anchors

HELLO = FIX / "hello-world.txt.ots"
HELLO_HEIGHT = 358391
TWO_CAL = FIX / "two-calendars.txt.ots"
HAVE_RIPEMD = True
try:
    __import__("hashlib").new("ripemd160", b"")
except ValueError:  # pragma: no cover - depends on the OpenSSL build
    HAVE_RIPEMD = False


def _header_entry(height: int) -> dict:
    return json.loads(HEADERS.read_text())[str(height)]


@pytest.mark.parametrize("path", [HELLO, TWO_CAL])
def test_other_upstream_proofs_round_trip(path):
    data = path.read_bytes()
    assert A.serialize_ots(A.parse_ots(data)) == data


@pytest.mark.skipif(not HAVE_RIPEMD, reason="hashlib lacks ripemd160 on this OpenSSL build")
def test_hello_world_verifies_at_its_2015_height(monkeypatch):
    # 2015 difficulty is about 2^67 work: the production floor would (correctly) refuse it.
    monkeypatch.setattr(A, "MIN_HEADER_WORK_BITS", 60)
    proof = A.parse_ots(HELLO.read_bytes())
    v = A.verify_digest_anchor(proof.digest, proof, A.HeaderFile(HEADERS))
    assert v.status == A.BITCOIN
    assert v.height == HELLO_HEIGHT
    assert v.block_hash == _header_entry(HELLO_HEIGHT)["hash"]


def test_hello_world_under_the_production_floor_is_not_bitcoin(monkeypatch):
    monkeypatch.setattr(A, "MIN_HEADER_WORK_BITS", 70)
    proof = A.parse_ots(HELLO.read_bytes())
    v = A.verify_digest_anchor(proof.digest, proof, A.HeaderFile(HEADERS))
    assert v.status != A.BITCOIN


def test_unsupported_ripemd160_is_skipped_and_named(monkeypatch):
    def boom(_data):
        raise A.UnsupportedOp("ripemd160 is not available in this Python/OpenSSL build")

    monkeypatch.setattr(A, "_ripemd160", boom)
    monkeypatch.setattr(A, "MIN_HEADER_WORK_BITS", 60)
    data = HELLO.read_bytes()
    proof = A.parse_ots(data)
    assert A.serialize_ots(proof) == data  # round trip needs no hashing
    v = A.verify_digest_anchor(proof.digest, proof, A.HeaderFile(HEADERS))
    assert v.status != A.BITCOIN
    assert "ripemd160" in v.detail


def test_two_calendars_are_pending():
    proof = A.parse_ots(TWO_CAL.read_bytes())
    v = A.verify_digest_anchor(proof.digest, proof, A.HeaderFile(HEADERS))
    assert v.status == A.PENDING_CALENDAR
    assert sorted(v.calendar_uris) == [
        "https://alice.btc.calendar.opentimestamps.org",
        "https://bob.btc.calendar.opentimestamps.org",
    ]
    assert all(u.startswith("https://") for u in v.calendar_uris)


class _Static:
    """A header source backed by a dict, for synthetic anchors."""

    def __init__(self, mapping):
        self.mapping = mapping

    def header(self, height):
        raw = self.mapping.get(height)
        return None if raw is None else A.BlockHeader.from_bytes(raw)


def test_cheap_synthetic_header_is_rejected_under_the_production_floor(monkeypatch):
    root = "ab" * 32
    ots, header_hex = A.regtest_anchor(root, datetime(2026, 11, 20, tzinfo=UTC), 5)
    proof = A.parse_ots(ots)
    src = _Static({5: bytes.fromhex(header_hex)})
    monkeypatch.setattr(A, "MIN_HEADER_WORK_BITS", 70)
    v = A.verify_digest_anchor(root, proof, src)
    assert v.status != A.BITCOIN


def test_header_entry_that_does_not_hash_to_its_stored_hash_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(A, "MIN_HEADER_WORK_BITS", 70)
    entry = dict(_header_entry(GDP_HEIGHT))
    entry["hash"] = "00" * 32
    bad = tmp_path / "headers.json"
    bad.write_text(json.dumps({str(GDP_HEIGHT): entry}))
    assert A.HeaderFile(bad).header(GDP_HEIGHT) is None
    proof = A.parse_ots(_gdp())
    v = A.verify_digest_anchor(proof.digest, proof, A.HeaderFile(bad))
    assert v.status != A.BITCOIN


def test_header_file_rejects_a_header_that_fails_its_own_pow(tmp_path):
    raw = bytearray(bytes.fromhex(_header_entry(GDP_HEIGHT)["header"]))
    raw[76] ^= 0xFF  # change the nonce: the hash no longer meets the target
    hdr = A.BlockHeader.from_bytes(bytes(raw))
    assert not hdr.pow_ok
    f = tmp_path / "h.json"
    f.write_text(json.dumps({str(GDP_HEIGHT): {"hash": hdr.block_hash_hex, "header": raw.hex()}}))
    assert A.HeaderFile(f).header(GDP_HEIGHT) is None


# -------------------------------------------------------------------------- explorers

H1 = _header_entry(GDP_HEIGHT)


def _fake_http(routes):
    calls = []

    def fake(url, timeout, max_bytes):
        calls.append(url)
        if url not in routes:
            raise OSError(f"no route {url}")
        out = routes[url]
        if isinstance(out, Exception):
            raise out
        return out

    fake.calls = calls
    return fake


BS = "https://blockstream.info/api"
MP = "https://mempool.space/api"


def _agreeing_routes(hash_hex=None, header_hex=None):
    h = hash_hex or H1["hash"]
    return {
        f"{BS}/block-height/{GDP_HEIGHT}": h.encode(),
        f"{MP}/block-height/{GDP_HEIGHT}": (h + "\n").encode(),
        f"{BS}/block/{h}/header": (header_hex or H1["header"]).encode(),
    }


def test_explorers_that_agree_yield_a_verified_header():
    src = A.ExplorerHeaders(http_get=_fake_http(_agreeing_routes()))
    hdr = src.header(GDP_HEIGHT)
    assert hdr is not None and hdr.block_hash_hex == H1["hash"]


def test_explorers_that_disagree_yield_nothing():
    routes = _agreeing_routes()
    routes[f"{MP}/block-height/{GDP_HEIGHT}"] = b"11" * 32
    assert A.ExplorerHeaders(http_get=_fake_http(routes)).header(GDP_HEIGHT) is None


def test_header_that_does_not_hash_to_the_agreed_hash_yields_nothing():
    other = _header_entry(HELLO_HEIGHT)["header"]
    routes = _agreeing_routes(header_hex=other)
    assert A.ExplorerHeaders(http_get=_fake_http(routes)).header(GDP_HEIGHT) is None


def test_explorer_failure_yields_nothing():
    routes = _agreeing_routes()
    routes[f"{MP}/block-height/{GDP_HEIGHT}"] = OSError("down")
    assert A.ExplorerHeaders(http_get=_fake_http(routes)).header(GDP_HEIGHT) is None


def test_chained_headers_fall_through_to_the_explorer(tmp_path):
    empty = A.HeaderFile(tmp_path / "none.json")
    chain = A.ChainedHeaders(empty, A.ExplorerHeaders(http_get=_fake_http(_agreeing_routes())))
    assert chain.header(GDP_HEIGHT) is not None
    assert A.ChainedHeaders(empty).header(GDP_HEIGHT) is None


def test_chained_headers_prefer_the_first_hit():
    boom = _fake_http({})
    chain = A.ChainedHeaders(A.HeaderFile(HEADERS), A.ExplorerHeaders(http_get=boom))
    assert chain.header(GDP_HEIGHT) is not None
    assert boom.calls == []


def test_http_get_refuses_non_https():
    with pytest.raises(ValueError):
        A.http_get("http://blockstream.info/api/block-height/1", 5, 1024)


# ----------------------------------------------------------------------------- regtest


def test_regtest_anchor_verifies_only_with_a_lowered_floor(monkeypatch):
    root = "cd" * 32
    when = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    ots, header_hex = A.regtest_anchor(root, when, 100)
    src = _Static({100: bytes.fromhex(header_hex)})
    proof = A.parse_ots(ots)
    assert A.serialize_ots(proof) == ots

    monkeypatch.setattr(A, "MIN_HEADER_WORK_BITS", 0)
    v = A.verify_digest_anchor(root, proof, src)
    assert v.status == A.BITCOIN
    assert v.height == 100
    assert v.block_time == when

    monkeypatch.setattr(A, "MIN_HEADER_WORK_BITS", 70)
    assert A.verify_digest_anchor(root, proof, src).status != A.BITCOIN


# ----------------------------------------------------------------------------- limits


def _wrap(tree: bytes, digest: bytes = b"\x01" * 32) -> bytes:
    return A.OTS_MAGIC + b"\x01\x08" + digest + tree


def _attest(height: int = 1) -> bytes:
    return A.serialize_ots(
        A.DetachedProof(b"\x00" * 32, _chain([], A.Bitcoin(height))))[len(A.OTS_MAGIC) + 34 :]


def _chain(ops, att):
    ts = A.Timestamp(b"\x00" * 32)
    node = ts
    for op in ops:
        node = node.add_op(op)
    node.add_attestation(att)
    return ts


def test_oversized_input_is_rejected():
    with pytest.raises(ValueError):
        A.parse_ots(_wrap(b"\x00" * 70000))


def test_nesting_beyond_the_depth_limit_is_rejected():
    sha = bytes([0x08])
    A.parse_ots(_wrap(sha * 256 + _attest()))  # exactly at the limit parses
    with pytest.raises(ValueError, match="deeper"):
        A.parse_ots(_wrap(sha * 257 + _attest()))


def test_oversized_op_argument_is_rejected():
    arg = b"\xaa" * 4097
    tree = bytes([0xF1]) + A._write_varuint(len(arg)) + arg + _attest()
    with pytest.raises(ValueError):
        A.parse_ots(_wrap(tree))
    ok = b"\xaa" * 4096
    A.parse_ots(_wrap(bytes([0xF1]) + A._write_varuint(len(ok)) + ok + _attest()))


def test_trailing_bytes_are_rejected():
    with pytest.raises(ValueError, match="trailing"):
        A.parse_ots(_gdp() + b"\x00")


def test_unknown_operation_and_truncation_are_rejected():
    with pytest.raises(ValueError):
        A.parse_ots(_wrap(b"\x42"))
    with pytest.raises(ValueError):
        A.parse_ots(_gdp()[:-5])
    with pytest.raises(ValueError):
        A.parse_ots(b"not an ots file")
