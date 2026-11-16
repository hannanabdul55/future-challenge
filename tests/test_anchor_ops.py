"""Calendar stamping, upgrade and the `python -m fc.anchor` operator CLI (plan 06-16).

Everything is offline: HTTP is injected, and no test opens a socket. The one real calendar
response is the committed fixture `tests/fixtures/ots/calendar-pending.bin`.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
from pathlib import Path

import pytest

from fc import anchor as A

FIXTURE = Path(__file__).parent / "fixtures" / "ots" / "calendar-pending.bin"
# The digest the fixture was captured for (see tests/fixtures/ots/README.md).
FIXTURE_DIGEST = bytes.fromhex("a58ef9f839009e86d1256d2528d97f9a98ed111b0de36abb99728418f9806927")

CAL_A = "https://a.pool.opentimestamps.org"
CAL_B = "https://b.pool.opentimestamps.org"


def root_hex(window: int = 0) -> str:
    return hashlib.sha256(f"merkle-root-{window}".encode()).hexdigest()


def make_round(tmp_path: Path, windows: int = 1) -> Path:
    """A round directory holding `windows` published root documents."""
    rd = tmp_path / "round"
    (rd / "roots").mkdir(parents=True)
    for w in range(windows):
        doc = {"window_index": w, "root": root_hex(w), "anchor_status": "not-anchored",
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
