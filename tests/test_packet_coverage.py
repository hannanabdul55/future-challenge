"""Packet coverage under the Season 1 budget (quick task 261016-fc2, findings 1-4 and 9):
race page titles, heading-path-aware table selection, partial-failure reporting, the weighted
budget split, and bounded GDELT backoff. Everything is offline: network is a fake ``http``
and sleeping goes through an injected recorder."""

from __future__ import annotations

import csv
import json
import urllib.error
from datetime import UTC, date, datetime
from pathlib import Path

from fc import packet as P
from fc import questions as Q

RACES = Path(__file__).resolve().parents[1] / "questions" / "2026-us-midterms" / "races.csv"


def _race_questions():
    return Q.generate_midterms(RACES)


# --------------------------------------------------------------------------- titles

def test_race_page_titles():
    assert P.race_page_title({"family": "senate", "state": "AK", "statement": "x"}) == \
        "2026 United States Senate election in Alaska"
    assert P.race_page_title({"family": "governor", "state": "AZ", "statement": "x"}) == \
        "2026 Arizona gubernatorial election"
    assert P.race_page_title({"family": "senate", "state": "OH",
                              "statement": "Which party ... special election for ..."}) == \
        "2026 United States Senate special election in Ohio"
    assert P.race_page_title({"family": "control", "statement": "x"}) is None
    assert P.race_page_title({"family": "senate", "state": "ZZ", "statement": "x"}) is None


def test_every_race_title_uses_the_full_state_name():
    with RACES.open(newline="") as f:
        names = {row["state"]: row["state_name"] for row in csv.DictReader(f)}
    n = 0
    for q in _race_questions():
        if q["family"] not in ("senate", "governor"):
            continue
        title = P.race_page_title(q)
        assert title is not None and names[q["state"]] in title, q["question_id"]
        n += 1
    assert n == 71
    assert all(P.STATE_NAMES[k] == v for k, v in names.items())


# --------------------------------------------------------------------------- parsing/selection

def _tbl(first_row, n_rows=3):
    rows = "\n".join(f"|-\n| P{i} || 1{i}% || 2{i}%" for i in range(n_rows))
    return "{| class=\"wikitable\"\n! " + " !! ".join(first_row) + "\n" + rows + "\n|}\n"


def test_heading_path_resets_on_a_new_section():
    text = ("== General election ==\n=== Polling ===\n" + _tbl(["Poll source", "D", "R"]) +
            "== Republican primary ==\n" + _tbl(["Poll source", "D", "R"]))
    tables = P.parse_wikitables(text)
    assert tables[0]["path"] == "General election > Polling" and tables[0]["heading"] == "Polling"
    assert tables[1]["path"] == "Republican primary" and tables[1]["heading"] == "Republican primary"


def test_is_poll_table():
    poll = {"heading": "Polling", "rows": [["Source", "D", "R"], ["x", "1", "2"]]}
    src = {"heading": "Results", "rows": [["Poll source", "D", "R"], ["x", "1", "2"]]}
    dates = {"heading": "Election dates", "rows": [["State", "Poll Closing", "x"], ["a", "b", "c"]]}
    assert P._is_poll_table(poll) and P._is_poll_table(src)
    assert not P._is_poll_table(dates)


def _page_http(wikitext_by_page, fail=()):
    def http(url, params=None):
        assert "wikipedia" in url
        page = params["page"]
        if page in fail:
            raise TimeoutError("down")
        return json.dumps({"parse": {"revid": 7, "wikitext": wikitext_by_page(page)}})
    return http


def _polls(questions, **kw):
    polls = P.WikipediaPolls.from_questions(questions, **kw)
    polls.pause_s = 0
    return polls


def test_general_election_table_wins_over_primary_and_is_capped():
    text = ("== Republican primary ==\n=== Polling ===\n" + _tbl(["Poll source", "D", "R"], 3) +
            "== General election ==\n=== Polling ===\n" + _tbl(["Poll source", "D", "R"], 30))
    q = [{"question_id": "senate-ak", "family": "senate", "state": "AK", "statement": "s"}]
    sec = _polls(q).fetch(_page_http(lambda p: text), date(2026, 10, 26))
    tables = sec.data["senate-ak"]
    assert len(tables) == 1
    assert tables[0]["path"] == "General election > Polling"
    assert len(tables[0]["rows"]) == 1 + 6


def test_primary_only_race_page_yields_nothing():
    text = "== Democratic primary ==\n=== Polling ===\n" + _tbl(["Poll source", "D", "R"], 3)
    q = [{"question_id": "senate-ak", "family": "senate", "state": "AK", "statement": "s"}]
    sec = _polls(q).fetch(_page_http(lambda p: text), date(2026, 10, 26))
    assert sec.data["senate-ak"] == []


def test_election_dates_table_is_excluded_from_race_sections():
    text = ("== Election dates ==\n" + _tbl(["State", "Poll Closing", "x"], 3) +
            "== General election ==\n=== Polling ===\n" + _tbl(["Poll source", "D", "R"], 3))
    q = [{"question_id": "senate-ak", "family": "senate", "state": "AK", "statement": "s"}]
    sec = _polls(q).fetch(_page_http(lambda p: text), date(2026, 10, 26))
    assert [t["path"] for t in sec.data["senate-ak"]] == ["General election > Polling"]


# --------------------------------------------------------------------------- partial failure

def _general_text():
    return "== General election ==\n=== Polling ===\n" + _tbl(["Poll source", "D", "R"], 30)


def test_partial_failures_are_reported(tmp_path):
    qs = _race_questions()
    polls = _polls(qs)
    assert len(polls.pages) == 73
    failed = ["senate-ak", "senate-al", "governor-az"]
    titles = [polls.pages[k] for k in failed]
    http = _page_http(lambda p: _general_text(), fail=titles)
    sec = polls.fetch(http, date(2026, 10, 26))
    assert sec.status == "ok"
    assert sec.reason.startswith("3/73 pages failed:")
    assert sec.attribution["failed_pages"] == sorted(failed)

    idx = P.build(date(2026, 10, 26), tmp_path, sources=[polls], http=http,
                  season="s", now=datetime(2026, 10, 26, tzinfo=UTC))
    assert idx["section_reasons"]["wikipedia_polls"].startswith("3/73 pages failed:")
    assert "3 of 73 pages unavailable" in (tmp_path / "packet.md").read_text()


# --------------------------------------------------------------------------- budget/coverage

def test_default_budget_is_the_season_constant():
    assert P.DEFAULT_BUDGET_CHARS == 20_000


def test_all_races_fit_the_season_budget(tmp_path):
    qs = _race_questions()
    sources = P.default_sources({}, qs)
    sources[0].pause_s = 0  # type: ignore[attr-defined]
    sources[-1].pause_s = 0  # type: ignore[attr-defined]

    def http(url, params=None):
        if "wikipedia" in url:
            return json.dumps({"parse": {"revid": 9, "wikitext": _general_text()}})
        return json.dumps({"articles": [{"title": "t", "url": "https://x/1", "domain": "x",
                                         "seendate": "20261025T120000Z"}]})

    P.build(date(2026, 10, 26), tmp_path, sources=sources, http=http, season="s",
            now=datetime(2026, 10, 26, tzinfo=UTC))
    md = (tmp_path / "packet.md").read_text()
    assert len(md) <= P.DEFAULT_BUDGET_CHARS + 500
    race_ids = [q["question_id"] for q in qs if q["family"] in ("senate", "governor")]
    assert len(race_ids) == 71
    for qid in race_ids:
        assert f"### {qid} " in md, qid
    first_race = min(md.index("### senate-"), md.index("### governor-"))
    assert md.index("### house ") < first_race and md.index("### senate ") < first_race


# --------------------------------------------------------------------------- GDELT backoff

def _gdelt(pause_s=2.0, **kw):
    sleeps: list[float] = []
    g = P.GdeltHeadlines(pause_s=pause_s, sleep=sleeps.append, **kw)
    return g, sleeps


ARTICLES = json.dumps({"articles": [{"title": "a", "url": "https://x/%d", "domain": "x",
                                     "seendate": "20261025T120000Z"}]})


def test_gdelt_default_is_one_combined_query():
    g, _ = _gdelt()
    assert len(g.queries) == 1
    q = g.queries[0]
    for term in ('"midterm elections"', '"Senate race"', '"governor race"', "gubernatorial"):
        assert term in q
    assert q.startswith("(") and " OR " in q and q.endswith("sourcelang:english")


def test_gdelt_retries_429_and_empty_body_with_backoff():
    g, sleeps = _gdelt(queries=["q0", "q1", "q2"])
    calls: dict[str, int] = {}

    def http(url, params=None):
        q = params["query"]
        calls[q] = calls.get(q, 0) + 1
        idx = g.queries.index(q)
        if idx == 1 and calls[q] == 1:
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]
        if idx == 2 and calls[q] == 1:
            return ""
        return json.dumps({"articles": [{"title": f"t{idx}", "url": f"https://x/{idx}",
                                         "domain": "x", "seendate": "20261025T120000Z"}]})

    sec = g.fetch(http, date(2026, 10, 26))
    assert sec.status == "ok" and sec.reason is None
    assert {a["url"] for a in sec.data} == {"https://x/0", "https://x/1", "https://x/2"}
    # pause before q2, backoff 2*2**0, pause before q3, backoff 2*2**0
    assert sleeps == [2.0, 2.0, 2.0, 2.0]


def test_gdelt_non_json_body_is_retryable():
    g, sleeps = _gdelt()
    seen = {"n": 0}

    def http(url, params=None):
        seen["n"] += 1
        if seen["n"] == 1:
            return "Please limit requests to one every 5 seconds"
        return ARTICLES % seen["n"]

    g.queries = g.queries[:1]
    sec = g.fetch(http, date(2026, 10, 26))
    assert sec.status == "ok" and seen["n"] == 2 and sleeps == [2.0]


def test_gdelt_total_wait_is_capped():
    g, sleeps = _gdelt(pause_s=10.0)

    def http(url, params=None):
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]

    sec = g.fetch(http, date(2026, 10, 26))
    assert sum(sleeps) <= g.max_wait_s
    assert sec.status == "unavailable"
    for q in g.queries:
        assert repr(q) in sec.reason


def test_gdelt_zero_pause_never_sleeps():
    g, sleeps = _gdelt(pause_s=0)

    def http(url, params=None):
        raise TimeoutError("down")

    assert g.fetch(http, date(2026, 10, 26)).status == "unavailable"
    assert sleeps == []
