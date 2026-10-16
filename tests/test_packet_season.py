"""PACKET-01..03 and ROUND-01/02, offline: the packet builds from fake responses, degrades
per source, hashes deterministically, and the daily manifest pins it."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from fc import packet as P
from fc import questions as Q
from fc import season as S

RACES = Path(__file__).resolve().parents[1] / "questions" / "2026-us-midterms" / "races.csv"

WIKITEXT = """
== Polling ==
{| class="wikitable"
! Poll source !! Date !! Sample !! Ossoff (D) !! Collins (R)
|-
| [[Emerson College]] || Oct 1–3, 2026 || 800 || '''49%''' || 45%
|-
| Quinnipiac || Sep 20–24, 2026 || 1,100 || 47% || '''48%'''
|}
"""


def fake_http(url, params=None):
    if "wikipedia" in url:
        return json.dumps({"parse": {"revid": 123456, "wikitext": WIKITEXT}})
    if "polymarket" in url:
        return json.dumps([{"outcomes": '["Democratic","Republican"]',
                            "outcomePrices": '["0.43","0.57"]'}])
    if "kalshi" in url:
        return json.dumps({"market": {"last_price": 41, "yes_bid": 40, "yes_ask": 42}})
    if "gdelt" in url:
        return json.dumps({"articles": [
            {"title": "Senate race tightens in Georgia", "url": "https://x/1",
             "domain": "x", "seendate": "20261025T120000Z"},
            {"title": "Dup", "url": "https://x/1", "domain": "x", "seendate": "20261025T110000Z"},
        ]})
    raise RuntimeError("unexpected url " + url)


def failing_http(url, params=None):
    if "gdelt" in url:
        raise TimeoutError("gdelt down")
    return fake_http(url, params)


def test_wikitable_parser_strips_markup():
    tables = P.parse_wikitables(WIKITEXT)
    assert tables[0]["heading"] == "Polling"
    assert tables[0]["rows"][0] == ["Poll source", "Date", "Sample", "Ossoff (D)", "Collins (R)"]
    assert tables[0]["rows"][1] == ["Emerson College", "Oct 1–3, 2026", "800", "49%", "45%"]


def _build(tmp_path, http, now=None):
    mapping = {"control-house": {"polymarket_slug": "house-control",
                                 "kalshi_ticker": "HOUSE-26"}}
    sources = P.default_sources(mapping)
    sources[-1].pause_s = 0  # type: ignore[attr-defined]
    return P.build(date(2026, 10, 26), tmp_path / "pkt", sources=sources,
                   http=http, season="2026-us-midterms",
                   now=now or datetime(2026, 10, 26, 0, 1, tzinfo=UTC))


def test_packet_builds_and_hashes_deterministically(tmp_path):
    idx = _build(tmp_path, fake_http)
    d = tmp_path / "pkt"
    for f in ("packet.json", "packet.md", "attribution.json", "index.json"):
        assert (d / f).is_file()
    assert idx["sections"] == {"wikipedia_polls": "ok", "prediction_markets": "ok",
                               "gdelt_headlines": "ok"}
    pk = json.loads((d / "packet.json").read_text())
    assert pk["licence"] == "CC-BY-SA-4.0"
    md = (d / "packet.md").read_text()
    assert "Emerson College" in md and "Senate race tightens" in md and "house-control" not in md
    assert md.count("https://x/1") == 0  # titles and domains only in the rendered view
    assert (d / "raw" / "wikipedia_polls.json").is_file()
    attribution = json.loads((d / "attribution.json").read_text())
    assert attribution["sources"][0]["attribution"]["pages"]["senate"]["revid"] == 123456

    # Same inputs → same packet hashes (built_at lives only in the index).
    idx2 = _build(tmp_path / "again", fake_http, now=datetime(2026, 10, 27, tzinfo=UTC))
    assert idx2["packet.json"] == idx["packet.json"]
    assert idx2["packet.md"] == idx["packet.md"]


def test_a_failed_source_degrades_without_blocking(tmp_path):
    idx = _build(tmp_path, failing_http)
    assert idx["sections"]["gdelt_headlines"] == "unavailable"
    assert idx["sections"]["wikipedia_polls"] == "ok"
    md = (tmp_path / "pkt" / "packet.md").read_text()
    assert "unavailable: " in md and "gdelt down" in md


def test_season_and_daily_manifests(tmp_path):
    qs = Q.generate_midterms(RACES)
    qpath = tmp_path / "questions" / "questions.json"
    Q.write_question_set(qpath, qs, season=Q.SEASON)
    sm = S.init_season(tmp_path, season=Q.SEASON, questions_path=qpath,
                       first_day=date(2026, 10, 26), last_day=date(2026, 11, 3),
                       last_admission="2026-11-03T12:00:00Z")
    assert sm["question_count"] == 75 and sm["T_final"]["senate"] == "2026-12-15"
    fm = json.loads((tmp_path / "rounds" / Q.SEASON / "season" / "manifest.json").read_text())
    assert fm["T_commit"] == "2026-11-03T12:00:00Z" and fm["kind"] == "freeze"

    _build(tmp_path, fake_http)
    m = S.open_round(tmp_path, season=Q.SEASON, d=date(2026, 10, 26),
                     packet_dir=tmp_path / "pkt", now=datetime(2026, 10, 26, tzinfo=UTC))
    assert m["round_id"] == "2026-us-midterms-2026-10-26"
    assert m["T_open"] == "2026-10-26T00:00:00Z" and m["T_commit"] == "2026-10-26T12:00:00Z"
    assert m["T_reveal"] == "2026-10-26T18:00:00Z"
    idx = json.loads((tmp_path / "pkt" / "index.json").read_text())
    assert m["packet"]["packet_md_sha256"] == idx["packet.md"]
    assert m["questions_hash"] == sm["questions_hash"]
    assert m["drand"]["round"] > 0
    written = json.loads((tmp_path / "rounds" / Q.SEASON / "2026-10-26" / "manifest.json").read_text())
    assert written == m
    # The service's deadline logic reads T_commit from exactly this file; identifiers pass IDENT_RE.
    from fc.service import IDENT_RE
    assert IDENT_RE.match(m["round_id"])
