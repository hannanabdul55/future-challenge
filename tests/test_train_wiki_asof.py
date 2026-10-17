"""Point-in-time revision shim, offline: a fake MediaWiki API, no network, no wall clock."""

from __future__ import annotations

import json
import urllib.error
from datetime import date

import pytest

from fc import packet as P
from fc.train import wiki_asof

WIKITEXT = """
== General election ==
=== Polling ===
{| class="wikitable"
! Poll source !! Date !! Sample !! Ossoff (D) !! Walker (R)
|-
| Emerson || Oct 1 || 800 || 49% || 45%
|}
"""


class FakeWiki:
    """titles -> [(revid, timestamp, wikitext)], oldest first; counts calls."""

    def __init__(self, pages, redirects=None, ignore_rvstart=False):
        self.pages = pages
        self.redirects = redirects or {}
        self.ignore_rvstart = ignore_rvstart
        self.calls = []

    def __call__(self, params):
        self.calls.append(dict(params))
        if params["action"] == "query":
            title = params["titles"]
            red = []
            if title in self.redirects:
                red = [{"from": title, "to": self.redirects[title]}]
                title = self.redirects[title]
            if title not in self.pages:
                return json.dumps({"query": {"pages": [{"title": title, "missing": True}]}})
            older = [r for r in self.pages[title]
                     if self.ignore_rvstart or r[1] <= params["rvstart"]]
            page = {"title": title, "pageid": 1}
            if older:
                rid, ts, _ = older[-1] if not self.ignore_rvstart else self.pages[title][-1]
                page["revisions"] = [{"revid": rid, "timestamp": ts}]
            q = {"pages": [page]}
            if red:
                q["redirects"] = red
            return json.dumps({"query": q})
        if params["action"] == "parse":
            for title, revs in self.pages.items():
                for rid, _, text in revs:
                    if rid == params["oldid"]:
                        return json.dumps({"parse": {"title": title, "revid": rid,
                                                     "wikitext": text}})
        raise AssertionError(f"unexpected call {params}")


PAGES = {
    "2022 United States Senate election in Georgia": [
        (100, "2022-10-01T10:00:00Z", WIKITEXT.replace("49%", "40%")),
        (200, "2022-10-30T23:59:59Z", WIKITEXT),
        (300, "2022-10-31T00:00:01Z", WIKITEXT.replace("49%", "10%")),
    ],
    "2022 United States Senate elections": [(400, "2022-09-01T00:00:00Z", WIKITEXT)],
}
ASOF = "2022-10-31T00:00:00Z"


def cache(tmp_path, fake, **kw):
    return wiki_asof.WikiCache(tmp_path / "wiki", fetch=fake, pause_s=0, sleep=lambda s: None,
                               **kw)


def test_rev_at_returns_newest_revision_at_or_before_asof(tmp_path):
    c = cache(tmp_path, FakeWiki(PAGES))
    rec = c.rev_at("2022 United States Senate election in Georgia", ASOF)
    assert rec["revid"] == 200 and rec["timestamp"] == "2022-10-30T23:59:59Z"
    assert rec["redirected_from"] is None


def test_rev_at_reports_redirect(tmp_path):
    fake = FakeWiki(PAGES, redirects={"Georgia Senate 2022":
                                      "2022 United States Senate election in Georgia"})
    rec = cache(tmp_path, fake).rev_at("Georgia Senate 2022", ASOF)
    assert rec["redirected_from"] == "Georgia Senate 2022" and rec["revid"] == 200


def test_missing_page_and_page_created_later_raise_lookup_error(tmp_path):
    c = cache(tmp_path, FakeWiki(PAGES))
    with pytest.raises(LookupError):
        c.rev_at("2022 United States Senate election in Texas", ASOF)
    with pytest.raises(LookupError):
        c.rev_at("2022 United States Senate election in Georgia", "2022-09-01T00:00:00Z")


def test_timestamp_guard_trips_when_api_returns_a_later_revision(tmp_path):
    c = cache(tmp_path, FakeWiki(PAGES, ignore_rvstart=True))
    with pytest.raises(AssertionError, match="after"):
        c.rev_at("2022 United States Senate election in Georgia", ASOF)


def test_cache_hit_makes_zero_network_calls(tmp_path):
    fake = FakeWiki(PAGES)
    title = "2022 United States Senate election in Georgia"
    c = cache(tmp_path, fake)
    rec = c.rev_at(title, ASOF)
    c.wikitext(rec["revid"])
    n = len(fake.calls)
    assert n == 2
    again = cache(tmp_path, fake)  # new instance, same disk cache
    assert again.rev_at(title, ASOF) == rec
    assert again.wikitext(rec["revid"]) == WIKITEXT
    with pytest.raises(LookupError):
        again.rev_at("2022 United States Senate election in Texas", ASOF)  # not cached yet
    n_after_new = len(fake.calls)
    with pytest.raises(LookupError):
        again.rev_at("2022 United States Senate election in Texas", ASOF)  # missing is cached
    assert len(fake.calls) == n_after_new == n + 1


def test_backoff_retries_transient_errors_then_gives_up(tmp_path):
    slept = []
    state = {"n": 0}

    def flaky(params):
        state["n"] += 1
        if state["n"] < 3:
            raise urllib.error.HTTPError("u", 429, "slow down", {}, None)
        return FakeWiki(PAGES)(params)

    c = wiki_asof.WikiCache(tmp_path / "w", fetch=flaky, pause_s=0.5, tries=4,
                            sleep=slept.append)
    assert c.rev_at("2022 United States Senate election in Georgia", ASOF)["revid"] == 200
    assert state["n"] == 3 and len(slept) == 2

    def hard(params):
        raise urllib.error.HTTPError("u", 404, "nope", {}, None)

    c2 = wiki_asof.WikiCache(tmp_path / "w2", fetch=hard, pause_s=0, sleep=slept.append)
    with pytest.raises(urllib.error.HTTPError):
        c2.rev_at("x", ASOF)


def test_historical_http_feeds_wikipedia_polls_unchanged(tmp_path):
    c = cache(tmp_path, FakeWiki(PAGES))
    http = wiki_asof.historical_http(c, ASOF)
    pages = {"senate-ga": "2022 United States Senate election in Georgia",
             "senate": "2022 United States Senate elections"}
    sec = P.WikipediaPolls(pages=pages, pause_s=0).fetch(http, date(2022, 10, 31))
    assert sec.status == "ok" and sec.reason is None
    assert sec.attribution["pages"]["senate-ga"]["revid"] == 200
    assert "oldid=200" in sec.attribution["pages"]["senate-ga"]["url"]
    assert sec.data["senate-ga"][0]["rows"][1][3] == "49%"


def test_historical_http_refuses_everything_but_wikipedia_parse(tmp_path):
    http = wiki_asof.historical_http(cache(tmp_path, FakeWiki(PAGES)), ASOF)
    with pytest.raises(RuntimeError):
        http(P.GDELT_API, {"query": "x"})
    with pytest.raises(RuntimeError):
        http(P.POLYMARKET_API, {"slug": "x"})
    with pytest.raises(RuntimeError):
        http(P.WIKI_API, {"action": "query", "titles": "x"})
    with pytest.raises(RuntimeError):
        http(P.WIKI_API, None)


def test_derive_races_uses_title_existence_and_the_combined_state_page(tmp_path):
    from fc.train import history

    combined = "2022 United States Senate elections in California"
    pages = dict(PAGES)
    pages[combined] = [(500, "2022-10-29T00:00:00Z", WIKITEXT)]
    pages["2022 Arizona gubernatorial election"] = [(600, "2022-10-02T00:00:00Z", WIKITEXT)]
    redirects = {
        "2022 United States Senate election in California": combined,
        "2022 United States Senate special election in California": combined,
        # a redirect to a different, non-combined article is not a race
        "2022 United States Senate election in Texas": "2022 United States Senate elections",
    }
    rows = history.derive_races(history.CYCLES[2022],
                                cache(tmp_path, FakeWiki(pages, redirects=redirects)))
    got = [(r["office"], r["state"], r["seat"]) for r in rows]
    assert got == [("senate", "CA", "class3"), ("senate", "CA", "special"),
                   ("senate", "GA", "class3"), ("governor", "AZ", "")]
    assert set(rows[0]) == {"office", "state", "state_name", "seat", "incumbent_party",
                            "open_seat", "pres_2020"}
