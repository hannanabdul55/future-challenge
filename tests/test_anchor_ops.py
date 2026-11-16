"""Calendar stamping, upgrade and the `python -m fc.anchor` operator CLI (plan 06-16).

Everything is offline: HTTP is injected, and no test opens a socket. The one real calendar
response is the committed fixture `tests/fixtures/ots/calendar-pending.bin`.
"""

from __future__ import annotations

import hashlib
import json
import struct
import urllib.error
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fc import anchor as A

FIXTURE = Path(__file__).parent / "fixtures" / "ots" / "calendar-pending.bin"
# The digest the fixture was captured for (see tests/fixtures/ots/README.md).
FIXTURE_DIGEST = bytes.fromhex("a58ef9f839009e86d1256d2528d97f9a98ed111b0de36abb99728418f9806927")

CAL_A = "https://a.pool.opentimestamps.org"
CAL_B = "https://b.pool.opentimestamps.org"


def root_hex(window: int = 0, salt: str = "") -> str:
    return hashlib.sha256(f"merkle-root-{window}{salt}".encode()).hexdigest()


def make_round(tmp_path: Path, windows: int = 1, salt: str = "") -> Path:
    """A round directory holding `windows` published root documents."""
    rd = tmp_path / "round"
    (rd / "roots").mkdir(parents=True)
    for w in range(windows):
        doc = {"window_index": w, "root": root_hex(w, salt), "anchor_status": "not-anchored",
               "hub_signature": "sig"}
        (rd / "roots" / f"{w}.json").write_text(json.dumps(doc, indent=2) + "\n")
    return rd


class FakePost:
    """Stands in for http_post: every calendar answers with the live fixture response."""

    def __init__(self, fail: tuple[str, ...] = (), garbage: tuple[str, ...] = ()) -> None:
        self.calls: list[tuple[str, bytes]] = []
        self.fail = fail
        self.garbage = garbage

    def __call__(self, url: str, body: bytes, timeout: float = 20.0, max_bytes: int = 10240):
        self.calls.append((url, body))
        if any(url.startswith(f) for f in self.fail):
            raise urllib.error.URLError("calendar down")
        if any(url.startswith(g) for g in self.garbage):
            return b"\x00not a timestamp"
        return FIXTURE.read_bytes()


# ------------------------------------------------------------------ the live fixture


def test_fixture_ends_in_a_pending_attestation_on_an_allow_listed_https_host():
    r = A._Reader(FIXTURE.read_bytes())
    ts = A.Timestamp.parse(r, FIXTURE_DIGEST)
    assert r.done
    pending = [att for _msg, att in ts.walk() if isinstance(att, A.Pending)]
    assert pending, "the calendar response must carry a pending attestation"
    for att in pending:
        assert att.uri.startswith("https://")
        assert A.calendar_allowed(att.uri), att.uri
    assert not any(isinstance(att, A.Bitcoin) for _msg, att in ts.walk())


def test_fixture_round_trips_byte_for_byte():
    data = FIXTURE.read_bytes()
    assert A.Timestamp.parse(A._Reader(data), FIXTURE_DIGEST).serialize() == data


# ------------------------------------------------------------------------- allowlist


@pytest.mark.parametrize("uri", [
    "https://a.pool.opentimestamps.org",
    "https://b.pool.opentimestamps.org/",
    "https://alice.btc.calendar.opentimestamps.org",
    "https://bob.btc.calendar.opentimestamps.org",
    "https://finney.calendar.eternitywall.com",
    "https://a.pool.eternitywall.com",
    "https://ots.btc.catallaxy.com",
    "https://alice.btc.calendar.opentimestamps.org:443",
])
def test_calendar_allowed_accepts_the_known_calendars(uri):
    assert A.calendar_allowed(uri)


@pytest.mark.parametrize("uri", [
    "http://a.pool.opentimestamps.org",
    "https://evil.example.com",
    "https://a.pool.opentimestamps.org.evil.com",
    "https://evilpool.opentimestamps.org",
    "https://ab.pool.opentimestamps.org",
    "https://a.pool.opentimestamps.org@evil.com",
    "https://a.pool.opentimestamps.org\\@evil.com",
    "https://evil.com/@a.pool.opentimestamps.org",
    "https://a.pool.opentimestamps.org:8443",
    "https://a.pool.opentimestamps.org?x=1",
    "https://a.pool.opentimestamps.org/#frag",
    "https://a.pool.opentimestamps.org\n",
    "https://127.0.0.1",
    "ftp://a.pool.opentimestamps.org",
    "a.pool.opentimestamps.org",
    "",
])
def test_calendar_allowed_refuses_everything_else(uri):
    assert not A.calendar_allowed(uri)


def test_calendar_allowed_refuses_non_strings():
    assert not A.calendar_allowed(None)  # type: ignore[arg-type]
    assert not A.calendar_allowed(b"https://a.pool.opentimestamps.org")  # type: ignore[arg-type]


def test_default_calendars_are_all_allowed():
    assert len(A.DEFAULT_CALENDARS) >= 3
    assert all(A.calendar_allowed(c) for c in A.DEFAULT_CALENDARS)


# ----------------------------------------------------------------------- stamp_digest


def test_stamp_digest_merges_calendar_responses_under_one_root():
    post = FakePost()
    stamped = A.stamp_digest(FIXTURE_DIGEST, calendars=(CAL_A, CAL_B), http_post=post)
    assert stamped.calendars == [CAL_A, CAL_B]
    assert [c[0] for c in post.calls] == [f"{CAL_A}/digest", f"{CAL_B}/digest"]
    assert all(body == FIXTURE_DIGEST for _u, body in post.calls)
    assert stamped.timestamp.msg == FIXTURE_DIGEST
    assert any(isinstance(att, A.Pending) for _m, att in stamped.timestamp.walk())


def test_stamp_digest_needs_min_calendars():
    post = FakePost(fail=(CAL_B,))
    with pytest.raises(A.StampError):
        A.stamp_digest(FIXTURE_DIGEST, calendars=(CAL_A, CAL_B), http_post=post)


def test_stamp_digest_treats_a_garbage_response_as_a_failed_calendar():
    post = FakePost(garbage=(CAL_B,))
    with pytest.raises(A.StampError):
        A.stamp_digest(FIXTURE_DIGEST, calendars=(CAL_A, CAL_B), http_post=post)
    stamped = A.stamp_digest(FIXTURE_DIGEST, calendars=(CAL_A, CAL_B), min_calendars=1,
                             http_post=post)
    assert stamped.calendars == [CAL_A]


def test_stamp_digest_refuses_a_disallowed_calendar_before_any_request():
    post = FakePost()
    for bad in ("https://evil.example.com", "http://a.pool.opentimestamps.org"):
        with pytest.raises(A.CalendarNotAllowed):
            A.stamp_digest(FIXTURE_DIGEST, calendars=(CAL_A, bad), http_post=post)
    assert post.calls == []


def test_stamp_digest_cannot_reach_more_calendars_than_it_was_given():
    post = FakePost()
    with pytest.raises(A.StampError):
        A.stamp_digest(FIXTURE_DIGEST, calendars=(CAL_A,), min_calendars=2, http_post=post)
    assert post.calls == []


# ------------------------------------------------------------------- stamp_round_dir


def test_stamp_round_dir_writes_ots_and_anchor_json_without_touching_the_root_doc(tmp_path):
    rd = make_round(tmp_path)
    before = (rd / "roots" / "0.json").read_bytes()
    post = FakePost()

    stamped = A.stamp_round_dir(rd, calendars=(CAL_A, CAL_B), http_post=post)

    assert stamped == [0]
    assert (rd / "roots" / "0.json").read_bytes() == before
    anchor = json.loads((rd / "roots" / "0.anchor.json").read_text())
    assert anchor["scheme"] == A.ANCHOR_SCHEME == "fc-ots/v1"
    assert anchor["digest"] == root_hex(0)
    assert anchor["status"] == A.PENDING_CALENDAR
    assert anchor["calendar_uris"] == [CAL_A, CAL_B]
    assert anchor["stamped_at"].endswith("Z")
    assert "schema_version" in anchor
    proof = A.parse_ots((rd / "roots" / "0.root.ots").read_bytes())
    assert proof.digest.hex() == root_hex(0)
    assert any(isinstance(att, A.Pending) for _m, att in proof.timestamp.walk())
    # The stamped value is the Merkle root: rewriting the document cannot invalidate it.
    assert [url for url, _b in post.calls] == [f"{CAL_A}/digest", f"{CAL_B}/digest"]
    assert all(body == bytes.fromhex(root_hex(0)) for _u, body in post.calls)


def test_stamp_round_dir_second_call_is_a_no_op(tmp_path):
    rd = make_round(tmp_path)
    post = FakePost()
    A.stamp_round_dir(rd, calendars=(CAL_A, CAL_B), http_post=post)
    ots = (rd / "roots" / "0.root.ots").read_bytes()
    anchor = (rd / "roots" / "0.anchor.json").read_bytes()
    n_calls = len(post.calls)

    assert A.stamp_round_dir(rd, calendars=(CAL_A, CAL_B), http_post=post) == []
    assert len(post.calls) == n_calls
    assert (rd / "roots" / "0.root.ots").read_bytes() == ots
    assert (rd / "roots" / "0.anchor.json").read_bytes() == anchor


def test_stamp_round_dir_stamps_every_unstamped_window(tmp_path):
    rd = make_round(tmp_path, windows=3)
    (rd / "roots" / "cadence.json").write_text("{}")
    A.stamp_round_dir(rd, calendars=(CAL_A, CAL_B), http_post=FakePost())
    for w in range(3):
        assert (rd / "roots" / f"{w}.root.ots").exists()
        assert json.loads((rd / "roots" / f"{w}.anchor.json").read_text())["digest"] == root_hex(w)
    assert not (rd / "roots" / "cadence.root.ots").exists()


def test_stamp_round_dir_failure_leaves_no_half_written_window(tmp_path):
    rd = make_round(tmp_path)
    post = FakePost(fail=(CAL_B,))
    with pytest.raises(A.StampError):
        A.stamp_round_dir(rd, calendars=(CAL_A, CAL_B), http_post=post)
    assert not (rd / "roots" / "0.root.ots").exists()
    assert not (rd / "roots" / "0.anchor.json").exists()


def test_stamp_round_dir_refuses_a_disallowed_calendar_before_any_request(tmp_path):
    rd = make_round(tmp_path)
    post = FakePost()
    with pytest.raises(A.CalendarNotAllowed):
        A.stamp_round_dir(rd, calendars=(CAL_A, "https://evil.example.com"), http_post=post)
    with pytest.raises(A.CalendarNotAllowed):
        A.stamp_round_dir(rd, calendars=(CAL_A, "http://b.pool.opentimestamps.org"),
                          http_post=post)
    assert post.calls == []


def test_stamp_round_dir_defaults_use_every_default_calendar(tmp_path):
    rd = make_round(tmp_path)
    post = FakePost()
    A.stamp_round_dir(rd, http_post=post)
    assert [u for u, _b in post.calls] == [f"{c}/digest" for c in A.DEFAULT_CALENDARS]


# ------------------------------------------------------------------------------ CLI


def test_cli_stamp_exits_zero_with_two_calendars_and_one_with_a_single_success(tmp_path, capsys):
    rd = make_round(tmp_path)
    one_down = FakePost(fail=(CAL_B,))
    code = A.main(["stamp", "--round-dir", str(rd), "--calendar", CAL_A, "--calendar", CAL_B],
                  http_post=one_down)
    assert code == 1
    assert not (rd / "roots" / "0.root.ots").exists()

    both_up = FakePost()
    code = A.main(["stamp", "--round-dir", str(rd), "--calendar", CAL_A, "--calendar", CAL_B],
                  http_post=both_up)
    assert code == 0
    assert (rd / "roots" / "0.root.ots").exists()
    out = capsys.readouterr().out
    assert "0.root.ots" in out
    assert "pending-calendar" in out


def test_cli_stamp_refuses_a_disallowed_calendar_before_any_request(tmp_path):
    rd = make_round(tmp_path)
    post = FakePost()
    for bad in ("https://evil.example.com", "http://a.pool.opentimestamps.org"):
        code = A.main(["stamp", "--round-dir", str(rd), "--calendar", bad, "--calendar", CAL_B],
                      http_post=post)
        assert code != 0
    assert post.calls == []


def test_cli_stamp_accepts_several_round_dirs_and_fails_if_any_window_stays_unstamped(tmp_path):
    first = make_round(tmp_path / "x")
    second = make_round(tmp_path / "y")
    post = FakePost()
    code = A.main(["stamp", "--round-dir", str(first), str(second), "--calendar", CAL_A,
                   "--calendar", CAL_B], http_post=post)
    assert code == 0
    assert (first / "roots" / "0.root.ots").exists() and (second / "roots" / "0.root.ots").exists()
    missing = tmp_path / "nowhere"
    code = A.main(["stamp", "--round-dir", str(missing), "--calendar", CAL_A, "--calendar", CAL_B],
                  http_post=post)
    assert code != 0


def test_cli_stamp_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        A.main(["stamp", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--round-dir" in out and "--min-calendars" in out


# =============================================================== upgrade and verify

BLOCK_TIME = datetime(2026, 11, 10, 0, 0, tzinfo=UTC)
HEIGHT = 950_001


def mine_header(merkle_root: bytes, when: datetime = BLOCK_TIME) -> A.BlockHeader:
    """A ~1-bit-work synthetic header; it only verifies with the work floor lowered."""
    head = (struct.pack("<i", 0x20000000) + bytes(32) + merkle_root
            + struct.pack("<II", int(when.timestamp()), 0x207FFFFF))
    nonce = 0
    while True:
        hdr = A.BlockHeader(head + struct.pack("<I", nonce))
        if hdr.pow_ok:
            return hdr
        nonce += 1


class FakeHeaders:
    """Stands in for an explorer / header file: height -> BlockHeader."""

    def __init__(self, mapping=None) -> None:
        self.mapping = dict(mapping or {})
        self.calls: list[int] = []

    def header(self, height: int):
        self.calls.append(height)
        return self.mapping.get(height)


def stamped_round(tmp_path: Path, windows: int = 1, salt: str = "") -> Path:
    rd = make_round(tmp_path, windows, salt)
    A.stamp_round_dir(rd, calendars=(CAL_A, CAL_B), http_post=FakePost())
    return rd


def pending_nodes(rd: Path, window: int = 0) -> list[tuple[bytes, A.Pending]]:
    proof = A.parse_ots((rd / "roots" / f"{window}.root.ots").read_bytes())
    return [(m, a) for m, a in proof.timestamp.walk() if isinstance(a, A.Pending)]


class FakeCalendar:
    """Stands in for calendar_get. Unknown URLs answer 404 (still pending)."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.responses: dict[str, bytes] = {}
        self.errors: dict[str, Exception] = {}

    def complete(self, rd: Path, height: int = HEIGHT, window: int = 0) -> A.BlockHeader:
        """Make the calendar return a Bitcoin attestation for the window's pending node."""
        (msg, att), = pending_nodes(rd, window)
        done = A.Timestamp(msg)
        node = done.add_op(A.Op(A._OP_SHA256))
        node.add_attestation(A.Bitcoin(height))
        self.responses[f"{att.uri}/timestamp/{msg.hex()}"] = done.serialize()
        assert node.msg is not None
        return mine_header(node.msg)

    def __call__(self, url: str, timeout: float = 20.0, max_bytes: int = 10240) -> bytes:
        self.calls.append(url)
        assert "evil" not in url and url.startswith("https://"), f"unexpected request {url}"
        if url in self.errors:
            raise self.errors[url]
        if url in self.responses:
            return self.responses[url]
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]


@pytest.fixture
def floor0(monkeypatch):
    monkeypatch.setattr(A, "MIN_HEADER_WORK_BITS", 0)


def read_anchor(rd: Path, w: int = 0) -> dict:
    return json.loads((rd / "roots" / f"{w}.anchor.json").read_text())


# ------------------------------------------------------------------- upgrade_timestamp


def test_upgrade_timestamp_404_means_still_pending(tmp_path):
    rd = stamped_round(tmp_path)
    proof = A.parse_ots((rd / "roots" / "0.root.ots").read_bytes())
    before = proof.timestamp.serialize()
    cal = FakeCalendar()

    assert A.upgrade_timestamp(proof.timestamp, cal) == 0
    assert proof.timestamp.serialize() == before
    (msg, att), = pending_nodes(rd)
    assert cal.calls == [f"{att.uri}/timestamp/{msg.hex()}"]


def test_upgrade_timestamp_merges_a_completed_attestation_and_keeps_the_pending_one(tmp_path):
    rd = stamped_round(tmp_path)
    cal = FakeCalendar()
    cal.complete(rd)
    proof = A.parse_ots((rd / "roots" / "0.root.ots").read_bytes())

    assert A.upgrade_timestamp(proof.timestamp, cal) == 1
    atts = [a for _m, a in proof.timestamp.walk()]
    assert any(isinstance(a, A.Bitcoin) and a.height == HEIGHT for a in atts)
    assert any(isinstance(a, A.Pending) for a in atts)
    A.parse_ots(A.serialize_ots(proof))  # still a well-formed proof


def test_upgrade_timestamp_never_requests_evil_or_http_calendars(tmp_path):
    digest = bytes.fromhex(root_hex(0))
    ts = A.Timestamp(digest)
    ts.add_attestation(A.Pending("https://evil.example.com"))
    ts.add_attestation(A.Pending("http://a.pool.opentimestamps.org"))
    ts.add_attestation(A.Pending("https://a.pool.opentimestamps.org.evil.com"))
    ts.add_attestation(A.Pending("https://127.0.0.1"))
    cal = FakeCalendar()
    notes: list = []

    assert A.upgrade_timestamp(ts, cal, notes=notes) == 0
    assert cal.calls == []
    assert len(notes) == 4 and all(level == "warning" for level, _m in notes)


@pytest.mark.parametrize("failure", [
    urllib.error.URLError("down"),
    urllib.error.HTTPError("u", 500, "boom", {}, None),
    TimeoutError("slow"),
    ValueError("too big"),
])
def test_upgrade_timestamp_records_other_errors_and_moves_on(tmp_path, failure):
    rd = stamped_round(tmp_path)
    proof = A.parse_ots((rd / "roots" / "0.root.ots").read_bytes())
    (msg, att), = pending_nodes(rd)
    cal = FakeCalendar()
    cal.errors[f"{att.uri}/timestamp/{msg.hex()}"] = failure
    notes: list = []

    assert A.upgrade_timestamp(proof.timestamp, cal, notes=notes) == 0
    assert [level for level, _m in notes] == ["warning"]


def test_upgrade_timestamp_ignores_a_garbage_200(tmp_path):
    rd = stamped_round(tmp_path)
    proof = A.parse_ots((rd / "roots" / "0.root.ots").read_bytes())
    (msg, att), = pending_nodes(rd)
    cal = FakeCalendar()
    cal.responses[f"{att.uri}/timestamp/{msg.hex()}"] = b"\x00junk"
    notes: list = []
    before = proof.timestamp.serialize()

    assert A.upgrade_timestamp(proof.timestamp, cal, notes=notes) == 0
    assert proof.timestamp.serialize() == before
    assert [level for level, _m in notes] == ["warning"]


# ------------------------------------------------------------------ upgrade_round_dir


def test_upgrade_round_dir_confirms_a_window_and_records_its_header(tmp_path, floor0):
    rd = stamped_round(tmp_path)
    doc_before = (rd / "roots" / "0.json").read_bytes()
    cal = FakeCalendar()
    hdr = cal.complete(rd)

    counts = A.upgrade_round_dir(rd, http_get=cal, headers=FakeHeaders({HEIGHT: hdr}))

    assert tuple(counts) == (1, 0, 1)
    assert (counts.confirmed, counts.pending, counts.upgraded) == (1, 0, 1)
    anchor = read_anchor(rd)
    assert anchor["status"] == A.BITCOIN
    assert anchor["bitcoin_block_height"] == HEIGHT
    assert anchor["block_hash"] == hdr.block_hash_hex
    assert anchor["upgraded_at"].endswith("Z")
    assert anchor["digest"] == root_hex(0) and anchor["scheme"] == A.ANCHOR_SCHEME
    assert anchor["calendar_uris"] == [CAL_A, CAL_B]  # stamping facts are kept
    proof = A.parse_ots((rd / "roots" / "0.root.ots").read_bytes())
    assert any(isinstance(a, A.Bitcoin) for _m, a in proof.timestamp.walk())
    headers = json.loads((rd / "roots" / "bitcoin-headers.json").read_text())
    assert headers == {str(HEIGHT): {"hash": hdr.block_hash_hex, "header": hdr.raw.hex()}}
    assert (rd / "roots" / "0.json").read_bytes() == doc_before
    # ...and a third party can now verify offline from the committed files alone.
    verdict = A.verify_round_window(rd, 0, A.HeaderFile(rd / "roots" / "bitcoin-headers.json"))
    assert verdict.status == A.BITCOIN and verdict.height == HEIGHT


def test_upgrade_round_dir_404_leaves_the_window_pending(tmp_path, floor0):
    rd = stamped_round(tmp_path)
    ots = (rd / "roots" / "0.root.ots").read_bytes()
    anchor = (rd / "roots" / "0.anchor.json").read_bytes()
    explorer = FakeHeaders()

    counts = A.upgrade_round_dir(rd, http_get=FakeCalendar(), headers=explorer)

    assert tuple(counts) == (0, 1, 0)
    assert (rd / "roots" / "0.root.ots").read_bytes() == ots
    assert (rd / "roots" / "0.anchor.json").read_bytes() == anchor
    assert not (rd / "roots" / "bitcoin-headers.json").exists()
    assert explorer.calls == []  # no Bitcoin attestation yet, so no explorer traffic


def test_upgrade_round_dir_skips_confirmed_windows(tmp_path, floor0):
    rd = stamped_round(tmp_path)
    cal = FakeCalendar()
    hdr = cal.complete(rd)
    A.upgrade_round_dir(rd, http_get=cal, headers=FakeHeaders({HEIGHT: hdr}))
    calls = len(cal.calls)

    counts = A.upgrade_round_dir(rd, http_get=cal, headers=FakeHeaders())

    assert tuple(counts) == (1, 0, 0)
    assert len(cal.calls) == calls


def test_upgrade_round_dir_never_requests_evil_or_http_pending_uris(tmp_path):
    rd = make_round(tmp_path, windows=2)
    for w, uri in enumerate(("https://evil.example.com", "http://a.pool.opentimestamps.org")):
        digest = bytes.fromhex(root_hex(w))
        ts = A.Timestamp(digest)
        ts.add_attestation(A.Pending(uri))
        (rd / "roots" / f"{w}.root.ots").write_bytes(A.serialize_ots(A.DetachedProof(digest, ts)))
    cal = FakeCalendar()

    counts = A.upgrade_round_dir(rd, http_get=cal, headers=FakeHeaders())

    assert cal.calls == []
    assert tuple(counts) == (0, 2, 0)


def test_upgrade_round_dir_keeps_progress_when_the_header_is_not_yet_available(tmp_path, floor0):
    rd = stamped_round(tmp_path)
    cal = FakeCalendar()
    hdr = cal.complete(rd)

    first = A.upgrade_round_dir(rd, http_get=cal, headers=FakeHeaders())  # explorer has nothing
    assert tuple(first) == (0, 1, 0)
    assert read_anchor(rd)["status"] == A.PENDING_CALENDAR
    proof = A.parse_ots((rd / "roots" / "0.root.ots").read_bytes())
    assert any(isinstance(a, A.Bitcoin) for _m, a in proof.timestamp.walk())

    cal.calls.clear()
    second = A.upgrade_round_dir(rd, http_get=cal, headers=FakeHeaders({HEIGHT: hdr}))
    assert tuple(second) == (1, 0, 1)
    assert read_anchor(rd)["status"] == A.BITCOIN


def test_upgrade_round_dir_rejects_a_cheap_header_under_the_production_floor(tmp_path,
                                                                             monkeypatch):
    monkeypatch.setattr(A, "MIN_HEADER_WORK_BITS", 70)
    rd = stamped_round(tmp_path)
    ots = (rd / "roots" / "0.root.ots").read_bytes()
    cal = FakeCalendar()
    hdr = cal.complete(rd)
    notes: list = []

    counts = A.upgrade_round_dir(rd, http_get=cal, headers=FakeHeaders({HEIGHT: hdr}), notes=notes)

    assert tuple(counts) == (0, 1, 0)
    assert read_anchor(rd)["status"] == A.PENDING_CALENDAR
    assert (rd / "roots" / "0.root.ots").read_bytes() == ots  # the bad attestation is not kept
    assert not (rd / "roots" / "bitcoin-headers.json").exists()
    assert any(level == "warning" for level, _m in notes)


def test_upgrade_round_dir_merges_headers_in_sorted_order_without_dropping_others(tmp_path,
                                                                                 floor0):
    rd = stamped_round(tmp_path)
    other = {"1": {"hash": "aa", "header": "bb"}}
    (rd / "roots" / "bitcoin-headers.json").write_text(json.dumps(other))
    cal = FakeCalendar()
    hdr = cal.complete(rd)

    A.upgrade_round_dir(rd, http_get=cal, headers=FakeHeaders({HEIGHT: hdr}))

    text = (rd / "roots" / "bitcoin-headers.json").read_text()
    data = json.loads(text)
    assert data["1"] == other["1"] and str(HEIGHT) in data
    assert list(data) == sorted(data)


def test_upgrade_round_dir_reports_a_corrupt_proof_and_continues(tmp_path, floor0):
    rd = stamped_round(tmp_path, windows=2)
    (rd / "roots" / "0.root.ots").write_bytes(b"not an ots file")
    cal = FakeCalendar()
    hdr = cal.complete(rd, window=1)
    notes: list = []

    counts = A.upgrade_round_dir(rd, http_get=cal, headers=FakeHeaders({HEIGHT: hdr}), notes=notes)

    assert tuple(counts) == (1, 1, 1)
    assert [level for level, _m in notes if level == "error"] == ["error"]
    assert read_anchor(rd, 1)["status"] == A.BITCOIN


def test_upgrade_round_dir_ignores_unstamped_windows(tmp_path):
    rd = make_round(tmp_path)
    assert tuple(A.upgrade_round_dir(rd, http_get=FakeCalendar(), headers=FakeHeaders())) == (0, 0,
                                                                                              0)


# ---------------------------------------------------------------- verify_round_window


def confirmed_round(tmp_path: Path) -> tuple[Path, A.BlockHeader]:
    rd = stamped_round(tmp_path)
    cal = FakeCalendar()
    hdr = cal.complete(rd)
    A.upgrade_round_dir(rd, http_get=cal, headers=FakeHeaders({HEIGHT: hdr}))
    return rd, hdr


def test_verify_round_window_bitcoin_pending_and_missing(tmp_path, floor0):
    rd, hdr = confirmed_round(tmp_path)
    headers = A.HeaderFile(rd / "roots" / "bitcoin-headers.json")
    ok = A.verify_round_window(rd, 0, headers)
    assert ok.status == A.BITCOIN
    assert ok.block_hash == hdr.block_hash_hex
    assert ok.anchored_before == BLOCK_TIME.replace(hour=2)

    pending = stamped_round(tmp_path / "p")
    assert A.verify_round_window(pending, 0, headers).status == A.PENDING_CALENDAR

    bare = make_round(tmp_path / "q")
    assert A.verify_round_window(bare, 0, headers).status == A.NOT_ANCHORED


def test_verify_round_window_rejects_a_proof_for_another_root(tmp_path, floor0):
    rd, _hdr = confirmed_round(tmp_path)
    doc = json.loads((rd / "roots" / "0.json").read_text())
    doc["root"] = root_hex(99)
    (rd / "roots" / "0.json").write_text(json.dumps(doc))
    verdict = A.verify_round_window(rd, 0, A.HeaderFile(rd / "roots" / "bitcoin-headers.json"))
    assert verdict.status == A.INVALID


# ------------------------------------------------------------------------------ CLI


def test_cli_upgrade_pending_prints_counts_and_exits_zero(tmp_path, capsys):
    rd = stamped_round(tmp_path)
    code = A.main(["upgrade", "--round-dir", str(rd)], http_get=FakeCalendar(),
                  headers=FakeHeaders())
    assert code == 0
    assert "confirmed 0, pending 1, upgraded 0" in capsys.readouterr().out


def test_cli_upgrade_confirms_and_sums_over_round_dirs(tmp_path, capsys, floor0):
    first = stamped_round(tmp_path / "x")
    second = stamped_round(tmp_path / "y", salt="-other-round")
    cal = FakeCalendar()
    hdr = cal.complete(first)
    code = A.main(["upgrade", "--round-dir", str(first), str(second)], http_get=cal,
                  headers=FakeHeaders({HEIGHT: hdr}))
    assert code == 0
    out = capsys.readouterr().out
    assert "confirmed 1, pending 1, upgraded 1" in out
    assert read_anchor(first)["status"] == A.BITCOIN


def test_cli_upgrade_exits_nonzero_on_a_format_error_or_missing_dir(tmp_path):
    rd = stamped_round(tmp_path)
    (rd / "roots" / "0.root.ots").write_bytes(b"garbage")
    assert A.main(["upgrade", "--round-dir", str(rd)], http_get=FakeCalendar(),
                  headers=FakeHeaders()) != 0
    assert A.main(["upgrade", "--round-dir", str(tmp_path / "nowhere")],
                  http_get=FakeCalendar(), headers=FakeHeaders()) != 0


def test_cli_verify_exits_zero_only_for_a_bitcoin_verified_window(tmp_path, capsys, monkeypatch):
    rd, hdr = confirmed_round_at_floor0(tmp_path, monkeypatch)
    capsys.readouterr()

    monkeypatch.setattr(A, "MIN_HEADER_WORK_BITS", 0)
    hf = str(rd / "roots" / "bitcoin-headers.json")
    assert A.main(["verify", "--round-dir", str(rd), "--window", "0", "--headers", hf]) == 0
    out = capsys.readouterr().out
    assert "bitcoin" in out and str(HEIGHT) in out and hdr.block_hash_hex in out
    assert "anchored_before 2026-11-10T02:00:00.000Z" in out
    # The default --headers is roots/bitcoin-headers.json beside the proofs.
    assert A.main(["verify", "--round-dir", str(rd), "--window", "0"]) == 0

    # The same regtest window under the production floor is not evidence.
    monkeypatch.setattr(A, "MIN_HEADER_WORK_BITS", 70)
    assert A.main(["verify", "--round-dir", str(rd), "--window", "0", "--headers", hf]) != 0


def confirmed_round_at_floor0(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "MIN_HEADER_WORK_BITS", 0)
    return confirmed_round(tmp_path)


def test_cli_verify_pending_window_is_nonzero(tmp_path, capsys):
    rd = stamped_round(tmp_path)
    assert A.main(["verify", "--round-dir", str(rd), "--window", "0"]) != 0
    assert "pending-calendar" in capsys.readouterr().out


def test_cli_verify_fetch_chains_the_explorer_after_the_file(tmp_path, floor0):
    rd, hdr = confirmed_round(tmp_path)
    (rd / "roots" / "bitcoin-headers.json").unlink()
    assert A.main(["verify", "--round-dir", str(rd), "--window", "0"]) != 0
    assert A.main(["verify", "--round-dir", str(rd), "--window", "0", "--fetch"],
                  headers=FakeHeaders({HEIGHT: hdr})) == 0


def test_cli_verify_missing_window_is_an_error(tmp_path):
    rd = make_round(tmp_path)
    assert A.main(["verify", "--round-dir", str(rd), "--window", "7"]) != 0


def test_cli_help_lists_all_three_subcommands(capsys):
    with pytest.raises(SystemExit) as exc:
        A.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "stamp" in out and "upgrade" in out and "verify" in out
    for sub in ("upgrade", "verify"):
        with pytest.raises(SystemExit) as exc:
            A.main([sub, "--help"])
        assert exc.value.code == 0
