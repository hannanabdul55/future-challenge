"""Historical packets, offline: the polls allowance matches a live packet (D-04), the GDELT
section is marked unavailable, builds are byte-stable and touch only the Wikipedia API."""

from __future__ import annotations

import json
from datetime import date

import pytest

from fc import packet as P
from fc.train import history, packets, wiki_asof

CYCLE = history.CYCLES[2022]
DAY = date(2022, 11, 1)
ASOF = "2022-11-01T00:00:00Z"
STATS = json.loads(packets.LIVE_STATS.read_text())


def _wikitext(pages_seed: int) -> str:
    rows = "".join(
        f"|-\n| Pollster {pages_seed}-{i} || Oct {i + 1}-{i + 3}, 2022 || {800 + i} || "
        f"{40 + (i + pages_seed) % 9}% || {38 + (i * 3 + pages_seed) % 9}%\n"
        for i in range(12))
    return ("== General election ==\n=== Polling ===\n{| class=\"wikitable\"\n"
            "! Poll source !! Date !! Sample !! Smith (D) !! Jones (R)\n" + rows + "|}\n")


class RichWiki:
    """Fake MediaWiki API: every title exists with a poll-rich table, last edited 2022-10-20."""

    def __init__(self):
        self.calls = []

    def __call__(self, params):
        self.calls.append(dict(params))
        if params["action"] == "query":
            title = params["titles"]
            rid = 1000 + sum(map(ord, title))
            return json.dumps({"query": {"pages": [{
                "title": title, "revisions": [{"revid": rid,
                                               "timestamp": "2022-10-20T00:00:00Z"}]}]}})
        if params["action"] == "parse":
            rid = int(params["oldid"])
            return json.dumps({"parse": {"title": f"t{rid}", "revid": rid,
                                         "wikitext": _wikitext(rid % 7)}})
        raise AssertionError(params)


def _questions() -> list[dict]:
    qs = []
    for st, name in sorted(P.STATE_NAMES.items()):
        for fam in ("senate", "governor"):
            qs.append({"question_id": f"{fam}-{st.lower()}", "family": fam, "state": st,
                       "state_name": name, "statement": f"Who wins the {fam} race in {name}?"})
    return qs


def _cache(tmp_path, fake=None):
    return wiki_asof.WikiCache(tmp_path / "wiki", fetch=fake or RichWiki(), pause_s=0,
                               sleep=lambda s: None)


def _articles(target: int) -> list[dict]:
    """GDELT articles whose rendered section is exactly ``target`` characters."""
    arts = [{"title": f"Senate race headline number {i:03d} " + "x" * 50,
             "url": f"https://example.com/{i}", "domain": "example.com",
             "seendate": f"20221101T{999999 - i:06d}Z"} for i in range(80)]
    g = P.GdeltHeadlines(pause_s=0)
    sec = P.Section(g.name, "ok", g.licence, {}, data=[
        {"title": a["title"], "url": a["url"], "domain": a["domain"], "seen": a["seendate"]}
        for a in arts])
    text = g.render(sec, 4000)
    k = sum(1 for line in text.splitlines() if line.startswith("- "))
    delta = target - len(text)
    last = arts[k - 1]
    last["title"] = last["title"] + "y" * delta if delta >= 0 else last["title"][:delta]
    return arts


def _live_shaped(tmp_path, cache, headlines_len):
    arts = _articles(headlines_len)
    wiki_http = wiki_asof.historical_http(cache, ASOF)

    def http(url, params=None):
        if url == P.GDELT_API:
            return json.dumps({"articles": arts})
        return wiki_http(url, params)

    pages = dict(history.national_pages(CYCLE.year))
    for q in _questions():
        pages[q["question_id"]] = history.race_title(q, CYCLE.year)
    sources = [P.WikipediaPolls(pages=pages, pause_s=0), P.PredictionMarkets({}),
               P.GdeltHeadlines(pause_s=0)]
    return P.build(DAY, tmp_path / "live", sources=sources, http=http, season=CYCLE.season,
                   budget_chars=P.DEFAULT_BUDGET_CHARS,
                   now=None)


def test_polls_allowance_matches_a_live_shaped_packet_within_5_percent(tmp_path):
    cache = _cache(tmp_path)
    headlines = STATS["median_len_headlines"]
    _live_shaped(tmp_path, cache, headlines)
    live_md = (tmp_path / "live" / "packet.md").read_text()
    # the fixture really is live-shaped: headlines section of the measured length
    secs = packets.split_sections(live_md)
    assert len(next(t for h, t in secs.items() if h.startswith("## Headlines"))) == headlines
    packets.build_day(CYCLE, DAY, _questions(), tmp_path / "hist", cache)
    hist_md = (tmp_path / "hist" / "packet.md").read_text()
    live_polls, hist_polls = len(packets.polls_section(live_md)), len(packets.polls_section(hist_md))
    assert abs(hist_polls - live_polls) <= 0.05 * live_polls, (hist_polls, live_polls)
    # the check discriminates: the unmatched 20000 budget would be well outside the tolerance
    packets.build_day(CYCLE, DAY, _questions(), tmp_path / "unmatched", cache,
                      budget_chars=P.DEFAULT_BUDGET_CHARS)
    unmatched = len(packets.polls_section((tmp_path / "unmatched" / "packet.md").read_text()))
    assert abs(unmatched - live_polls) > 0.05 * live_polls


def test_historical_packet_marks_gdelt_unavailable_with_the_historical_reason(tmp_path):
    packets.build_day(CYCLE, DAY, _questions(), tmp_path / "h", _cache(tmp_path))
    pk = json.loads((tmp_path / "h" / "packet.json").read_text())
    gd = next(s for s in pk["sections"] if s["name"] == "gdelt_headlines")
    assert gd["status"] == "unavailable" and gd["reason"] == packets.HIST_GDELT_REASON
    md = (tmp_path / "h" / "packet.md").read_text()
    assert f"_unavailable: {packets.HIST_GDELT_REASON}_" in md
    assert "_unavailable: no market mapping configured_" in md


def test_two_builds_are_byte_identical(tmp_path):
    cache = _cache(tmp_path)
    for name in ("a", "b"):
        packets.build_day(CYCLE, DAY, _questions(), tmp_path / name, cache)
    for f in ("packet.md", "packet.json", "index.json", "attribution.json"):
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes(), f


def test_build_day_only_reaches_the_wikipedia_parse_api(tmp_path, monkeypatch):
    seen: list[tuple[str, dict | None]] = []
    real = wiki_asof.historical_http

    def recording(cache, asof):
        inner = real(cache, asof)

        def http(url, params=None):
            seen.append((url, params))
            return inner(url, params)

        return http

    monkeypatch.setattr(wiki_asof, "historical_http", recording)
    fake = RichWiki()
    packets.build_day(CYCLE, DAY, _questions(), tmp_path / "h", _cache(tmp_path, fake))
    assert seen and all(u == P.WIKI_API and p["action"] == "parse" for u, p in seen)
    assert {c["action"] for c in fake.calls} <= {"query", "parse"}


def test_derive_hist_budget_and_measured_stats_agree():
    assert packets.derive_hist_budget(20000, 68, 3938) == 16160
    assert STATS["hist_budget_chars"] == packets.derive_hist_budget(
        STATS["live_budget_chars"], STATS["median_len_markets"], STATS["median_len_headlines"])
    assert STATS["live_polls_allowance"] == (
        STATS["live_budget_chars"] - STATS["median_len_markets"] - STATS["median_len_headlines"])
    assert 10000 < packets.historical_budget() <= 20000


def test_historical_budget_reads_the_file_and_fails_loudly_when_missing(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"hist_budget_chars": 12345}))
    assert packets.historical_budget(p) == 12345
    with pytest.raises(FileNotFoundError):
        packets.historical_budget(tmp_path / "missing.json")


def test_split_sections_and_polls_section():
    md = ("# Information packet\n\nintro\n## Polls (Wikipedia)\n### k — a\n| x |\n"
          "## Prediction markets\n\n_unavailable: z_\n## Headlines\n\n_unavailable: y_\n")
    secs = packets.split_sections(md)
    assert list(secs) == ["## Polls (Wikipedia)", "## Prediction markets", "## Headlines"]
    assert "### k — a" in packets.polls_section(md) and "markets" not in packets.polls_section(md)
    with pytest.raises(ValueError):
        packets.polls_section("# nothing\n")
