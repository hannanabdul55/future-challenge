"""QSPEC-04: the macro release template generates deterministically from a release calendar and
offline history, validates, places thresholds with seeded ±1/±2-step offsets, resolves first
prints mechanically (BLS first, FRED fallback), replays on history, and renders a packet
section. The season loop asks only active questions and ranks only the `ranked` track."""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from pathlib import Path

import pandas as pd

from fc import macro as M
from fc import questions as Q

SEASON = "test-rolling"
SERIES = {s.series_id: s for s in M.load_series()}
CPI = SERIES["cpi-headline-yoy"]
PAY = SERIES["payrolls-change"]
FOMC = SERIES["fomc-decision"]


def _fred_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["observation_date", "X"])
    w.writerows(rows)
    return buf.getvalue()


def _cpi_levels(n: int = 48, start: str = "2023-01-01"):
    """Synthetic CPI index: 3 %/yr trend plus a small deterministic wiggle."""
    y, m = int(start[:4]), int(start[5:7])
    out = []
    for i in range(n):
        d = date(y + (m - 1 + i) // 12, (m - 1 + i) % 12 + 1, 1)
        out.append((d.isoformat(), round(300 * (1.0025 ** i) + (i % 3) * 0.4, 3)))
    return out


def fake_http(url, params=None):
    if url == M.FRED_CSV:
        sid = params["id"]
        if sid == "CPIAUCNS":
            return _fred_csv(_cpi_levels())
        if sid == "PAYEMS":
            return _fred_csv([(d, 150000 + 120 * i + (i % 4) * 30)
                              for i, (d, _) in enumerate(_cpi_levels())])
        if sid == "DFEDTARU":
            return _fred_csv([("2026-12-04", 4.00), ("2026-12-07", 4.00),
                              ("2026-12-10", 3.75), ("2026-12-11", 3.75)])
        if sid == "DGS10":
            return _fred_csv([("2026-10-30", 4.9), ("2026-11-02", 5.0), ("2026-11-13", 5.1)])
        return _fred_csv(_cpi_levels())
    if url.startswith(M.BLS_V1):
        # BLS first print for 2026-11 CPI: level implying a YoY the FRED path would not give.
        lv = dict(_cpi_levels())
        data = [{"year": k[:4], "period": f"M{k[5:7]}", "value": str(v)} for k, v in lv.items()]
        data.append({"year": "2026", "period": "M11", "value": str(round(lv["2025-11-01"] * 1.031, 3))})
        data.append({"year": "2026", "period": "M13", "value": "0"})
        return json.dumps({"status": "REQUEST_SUCCEEDED",
                           "Results": {"series": [{"seriesID": "CUUR0000SA0", "data": data}]}})
    raise AssertionError(f"unexpected url {url}")


RELEASES = [
    {"release": "cpi", "period": "2026-11", "release_date": "2026-12-10", "meeting_start": "", "source": "bls"},
    {"release": "empsit", "period": "2026-11", "release_date": "2026-12-04", "meeting_start": "", "source": "bls"},
    {"release": "fomc", "period": "2026-12", "release_date": "2026-12-09", "meeting_start": "2026-12-08", "source": "frb"},
]


def test_periods_and_transforms():
    assert M.prev_period("2026-01") == "2025-12" and M.prev_period("2026-11", 12) == "2025-11"
    assert M.prev_period("2027Q1") == "2026Q4"
    raw = {"2025-01": 100.0, "2025-02": 101.0, "2026-01": 103.0, "2026-02": 105.06}
    assert M.transformed(CPI, raw) == {"2026-01": 3.0, "2026-02": 4.0}
    assert M.transformed(PAY, raw)["2025-02"] == 1


def test_generate_month_is_deterministic_and_valid():
    qs = M.generate_month(SEASON, "2026-12", http=fake_http, series=[CPI, PAY, FOMC],
                          releases=RELEASES, as_of="2026-11-14")
    assert {q["question_id"] for q in qs} == {
        "macro-payrolls-change-2026-11", "macro-payrolls-change-2026-11-gt",
        "macro-fomc-2026-12", "macro-cpi-headline-yoy-2026-11", "macro-cpi-headline-yoy-2026-11-gt"}
    rep = Q.validate_questions(qs)
    assert rep.ok, rep
    assert not [w for w in rep.warnings if "vintage_rule" in w or "expected_resolution" in w]
    again = M.generate_month(SEASON, "2026-12", http=fake_http, series=[CPI, PAY, FOMC],
                             releases=RELEASES, as_of="2026-11-14")
    assert Q.questions_hash(qs) == Q.questions_hash(again)
    cpi_q = next(q for q in qs if q["question_id"] == "macro-cpi-headline-yoy-2026-11")
    assert cpi_q["track"] == "ranked" and cpi_q["cluster"] == "macro/2026-12"
    assert cpi_q["resolution"]["vintage_rule"] == "first_release"
    assert cpi_q["resolution"]["expected_resolution_date"] == "2026-12-10"
    assert cpi_q["resolution"]["T_final"] == "2026-12-31"
    assert cpi_q["reference"]["period"] == "2026-10" and cpi_q["baseline"]["integer"] is False
    assert cpi_q["headline"] and cpi_q["headline_label"]
    assert "first-published" in cpi_q["statement"] and "December 10, 2026" in cpi_q["statement"]


def test_threshold_offsets_are_seeded_and_vary_in_sign():
    offs = {M.threshold_offset(SEASON, CPI, f"2027-{m:02d}") for m in range(1, 13)}
    assert offs <= {-0.4, -0.2, 0.2, 0.4}
    assert M.effective_step(CPI, 0.05) == CPI.step and M.effective_step(CPI, 0.5) == 0.5
    assert any(o < 0 for o in offs) and any(o > 0 for o in offs)
    assert M.threshold_offset(SEASON, CPI, "2027-01") == M.threshold_offset(SEASON, CPI, "2027-01")
    assert M.threshold_offset("other-season", CPI, "2027-01") in {-0.4, -0.2, 0.2, 0.4}
    qs = M.generate_month(SEASON, "2026-12", http=fake_http, series=[CPI], releases=RELEASES[:1])
    b = next(q for q in qs if q["output_type"] == "binary")
    assert abs(b["threshold"] - (b["reference"]["value"] + b["threshold_offset"])) < 1e-9
    assert 0.05 <= b["baseline"]["p"] <= 0.95


def test_resolver_prefers_bls_first_print_and_handles_pending_and_fomc():
    qs = M.generate_month(SEASON, "2026-12", http=fake_http, series=[CPI, FOMC], releases=RELEASES,
                          as_of="2026-11-14")
    pending = M.resolve_all(qs, http=fake_http, today=date(2026, 12, 9))
    assert {r["status"] for r in pending if "cpi" in r["question_id"]} == {"pending"}
    rows = {r["question_id"]: r for r in M.resolve_all(qs, http=fake_http, today=date(2026, 12, 12))}
    q = rows["macro-cpi-headline-yoy-2026-11"]
    assert q["status"] == "resolved" and q["y"] == 3.1          # BLS first print, not FRED
    b = rows["macro-cpi-headline-yoy-2026-11-gt"]
    thr = next(x for x in qs if x["question_id"] == b["question_id"])["threshold"]
    assert b["y"] == int(3.1 > thr)
    f = rows["macro-fomc-2026-12"]
    assert f["status"] == "resolved" and f["y"] == "cut_25bp"
    assert M.fomc_outcome(4.0, 3.5) == "cut_50bp_or_more" and M.fomc_outcome(4.0, 4.25) == "hike"


def test_resolver_voids_after_fourteen_days_without_data():
    rel = [{"release": "cpi", "period": "2027-06", "release_date": "2027-07-14",
            "meeting_start": "", "source": "bls"}]
    qs = M.generate_month(SEASON, "2027-07", http=fake_http, series=[CPI], releases=rel,
                          as_of="2026-11-14")
    late = M.resolve_all(qs, http=fake_http, today=date(2027, 7, 20))
    assert {r["status"] for r in late} == {"pending"}
    void = M.resolve_all(qs, http=fake_http, today=date(2027, 7, 30))
    assert {r["status"] for r in void} == {"void"}


def test_dry_run_replays_history_mechanically():
    hist = M.history(CPI, fake_http)
    rows = M.dry_run(CPI, hist, season=SEASON, n=12)
    assert len(rows) == 12
    assert {r["binary_y"] for r in rows} <= {0, 1}
    assert all(r["threshold"] != r["reference"] for r in rows)
    # Replay uses only history strictly before each period.
    periods = sorted(hist)
    assert rows[0]["reference"] == hist[periods[-13]]


def test_packet_source_renders_prior_observations_only():
    src = M.MacroSeries([CPI, SERIES["ust10y-month-end"]])
    sec = src.fetch(fake_http, date(2026, 11, 20))
    assert sec.status == "ok"
    obs = dict(sec.data["cpi-headline-yoy"]["observations"])
    assert "2026-11" not in obs and len(obs) <= M.PACKET_ROWS
    assert dict(sec.data["ust10y-month-end"]["observations"]) == {"2026-10": 4.9}
    md = src.render(sec, 20_000)
    assert "## Macro data" in md and "| 2026-10 |" in md and "latest vintage" in md


def test_active_questions_and_ranked_track(tmp_path: Path):
    qs = M.generate_month(SEASON, "2026-12", http=fake_http, series=[CPI, PAY], releases=RELEASES,
                          as_of="2026-11-14")
    world = {"schema": Q.SCHEMA, "question_id": "world-x", "family": "politics", "track": "world",
             "statement": "x", "output_type": "binary", "core": False, "manipulation_risk": "low",
             "rules_version": Q.RULES, "event_date": "2026-12-31",
             "resolution": {"primary_source": "s", "provisional_rule": "p", "final_rule": "f",
                            "fallback_sources": [], "void_if": [], "T_final": "2027-01-15",
                            "resolver": "council", "vintage_rule": "value_as_of(T_final)",
                            "expected_resolution_date": "2027-01-01"}}
    allq = qs + [world]
    assert Q.validate_questions(allq).ok
    ids = lambda d: {q["question_id"] for q in Q.active_on(allq, d)}
    assert "macro-payrolls-change-2026-11" in ids("2026-12-03")
    assert "macro-payrolls-change-2026-11" not in ids("2026-12-04")      # release day excluded
    assert "macro-cpi-headline-yoy-2026-11" in ids("2026-12-09")
    assert ids("2026-12-31") == {"world-x"}                             # macro items closed
    assert ids("2027-01-01") == set()                                   # world item closes too
    assert Q.ranked_ids(allq) == {q["question_id"] for q in qs}
    assert Q.cluster_of(qs[0], "2026-12-01") == "macro/2026-12" and Q.cluster_of(world, "d") == "d"

    # assemble: world.json + macro/*.json -> one deterministic set
    sdir = tmp_path / "questions" / SEASON
    (sdir / "macro").mkdir(parents=True)
    Q.write_question_set(sdir / "world.json", [world], season=SEASON)
    Q.write_question_set(sdir / "macro" / "2026-12.json", qs, season=SEASON)
    merged = Q.assemble_season(sdir)
    assert {q["question_id"] for q in merged} == {q["question_id"] for q in allq}
    (sdir / "macro" / "dup.json").write_text(json.dumps({"questions": [world]}))
    try:
        Q.assemble_season(sdir)
        raise AssertionError("duplicate id accepted")
    except ValueError as exc:
        assert "duplicate" in str(exc)


def test_bls_parser_drops_annual_rows():
    txt = fake_http(M.BLS_V1 + "CUUR0000SA0")
    d = M.parse_bls_v1(txt)
    assert "2026-11" in d and not any(k.endswith("-13") for k in d)


def test_release_calendar_rows_are_well_formed():
    rel = M.load_releases()
    known = {s.release for s in M.load_series()}
    for r in rel:
        assert r["release"] in known, r
        date.fromisoformat(r["release_date"])
        assert len(r["period"]) in (6, 7)
    df = pd.DataFrame(rel)
    assert not df.duplicated(["release", "period"]).any()
