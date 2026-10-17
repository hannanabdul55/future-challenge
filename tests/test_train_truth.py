"""Historical truth (D-07): MEDSL Senate loader, reasoned overrides, known traps.

Offline: the fixture holds real rows copied from the CC0 MEDSL file; the full-file test is
skipped when the downloaded file is not on this machine.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from fc.train import paths, truth

FIXTURE = Path(__file__).parent / "fixtures" / "medsl_senate_excerpt.csv"
OVERRIDES = paths.REPO_DATA / "overrides.csv"
MISSING = paths.REPO_DATA / "missing_contests.csv"
FULL = paths.MEDSL / "senate"
YEARS = (2018, 2020, 2022, 2024)


def key(c):
    return (c.year, c.state, c.special)


@pytest.fixture(scope="module")
def raw():
    return {key(c): c for c in truth.load_medsl_senate(FIXTURE, YEARS)}


def _real_overrides_for(contests, tmp_path):
    """The real overrides.csv rows whose contest is present in the fixture."""
    have = {(c.year, c.office, c.state, c.special) for c in contests}
    rows = list(csv.DictReader(OVERRIDES.open(newline="")))
    keep = [r for r in rows
            if (int(r["year"]), r["office"], r["state"], r["special"] == "true") in have]
    out = tmp_path / "ov.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(keep)
    return out


@pytest.fixture()
def fixed(raw, tmp_path):
    contests = list(raw.values())
    ov = _real_overrides_for(contests, tmp_path)
    return {key(c): c for c in truth.apply_overrides(contests, ov)}


# ------------------------------------------------------------------ stage and runoff

def test_stage_case_variants_all_load(raw):
    # 2018 rows say "gen", 2022 rows say "GEN", the GA 2022 runoff says "GEN RUNOFF"
    assert (2018, "CT", False) in raw and (2022, "OK", False) in raw and (2022, "GA", False) in raw
    assert truth.normalise_stage("GEN RUNOFF") == "runoff"
    assert truth.normalise_stage("gen") == "gen" == truth.normalise_stage("GEN")


def test_runoff_row_replaces_first_round_winner(raw):
    ga = raw[(2022, "GA", False)]
    assert ga.winner_party == "D" and "WARNOCK" in ga.winner_name.upper()
    # shares come from the runoff round (3,541,877 votes), not the first round
    assert ga.d_pct == pytest.approx(1820633 / 3541877)
    assert ga.r_pct == pytest.approx(1721244 / 3541877)


def test_fusion_lines_are_one_candidate(raw):
    ct = raw[(2018, "CT", False)]  # Murphy appears on the D and the Working Families lines
    assert ct.winner_party == "D"
    assert ct.d_pct == pytest.approx((787685 + 37894) / 1386840)


# ------------------------------------------------------------------ known traps

def test_traps_are_wrong_before_overrides(raw):
    assert raw[(2020, "WY", False)].winner_party == "Other"   # party_detailed empty
    assert raw[(2022, "AK", False)].winner_party == "Other"   # party_detailed empty
    assert raw[(2022, "IL", False)].winner_party == "Other"   # "DEMOCRATIC" not recognised
    assert raw[(2022, "MD", False)].winner_party == "Other"
    assert raw[(2020, "GA", False)].winner_party == "R"       # runoff absent: Perdue led round 1


def test_party_fixes_after_overrides(fixed):
    assert fixed[(2020, "WY", False)].winner_party == "R"
    assert fixed[(2022, "AK", False)].winner_party == "R"
    assert fixed[(2022, "IL", False)].winner_party == "D"
    assert fixed[(2022, "MD", False)].winner_party == "D"
    assert fixed[(2020, "WY", False)].caucus == "R"


def test_georgia_2020_runoffs_resolve_d_with_cited_reason(fixed):
    assert fixed[(2020, "GA", False)].winner_party == "D"
    assert fixed[(2020, "GA", True)].winner_party == "D"
    rows = [r for r in csv.DictReader(OVERRIDES.open(newline="")) if r["year"] == "2020"
            and r["state"] == "GA"]
    assert len(rows) == 2 and all("2021-01-05" in r["reason"] for r in rows)


def test_same_seat_contests_share_a_seat_key(raw):
    assert raw[(2022, "CA", False)].seat_key == raw[(2022, "CA", True)].seat_key == "CA-class3"
    assert raw[(2024, "CA", False)].seat_key == raw[(2024, "CA", True)].seat_key
    # different seats in one state: NE 2024 (Fischer, Ricketts) and OK 2022 (Lankford, Mullin)
    assert raw[(2024, "NE", False)].seat_key != raw[(2024, "NE", True)].seat_key
    assert raw[(2022, "OK", False)].seat_key != raw[(2022, "OK", True)].seat_key


def test_maine_independent_is_other_with_democratic_caucus(fixed):
    me = fixed[(2018, "ME", False)]
    assert me.winner_party == "Other" and me.caucus == "D"
    assert fixed[(2018, "VT", False)].caucus == "D"


def test_ordinary_contest_is_untouched(fixed):
    ct = fixed[(2018, "CT", False)]
    assert ct.winner_party == "D" and ct.caucus == "D" and ct.source == "medsl"


# ------------------------------------------------------------------ overrides contract

def _write(path, rows):
    cols = ["year", "office", "state", "special", "winner_party", "caucus", "reason"]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path


def test_override_for_absent_contest_raises(raw, tmp_path):
    p = _write(tmp_path / "o.csv", [{"year": 2018, "office": "senate", "state": "ZZ", "special": "false",
                                         "winner_party": "D", "caucus": "D", "reason": "x"}])
    with pytest.raises(ValueError, match="matches no contest"):
        truth.apply_overrides(list(raw.values()), p)


def test_override_without_reason_raises(raw, tmp_path):
    p = _write(tmp_path / "o.csv", [{"year": 2020, "office": "senate", "state": "WY", "special": "false",
                                         "winner_party": "R", "caucus": "R", "reason": "  "}])
    with pytest.raises(ValueError, match="reason"):
        truth.apply_overrides(list(raw.values()), p)


def test_override_wins_and_records_provenance(raw, tmp_path):
    p = _write(tmp_path / "o.csv", [{"year": 2020, "office": "senate", "state": "WY", "special": "false",
                                         "winner_party": "R", "caucus": "", "reason": "certified"}])
    out = {key(c): c for c in truth.apply_overrides(list(raw.values()), p)}
    assert out[(2020, "WY", False)].winner_party == "R"
    assert out[(2020, "WY", False)].caucus == "R"       # blank caucus follows a D or R party
    assert out[(2020, "WY", False)].source == "medsl+override"
    assert raw[(2020, "WY", False)].winner_party == "Other"  # input list is not mutated


def test_real_override_file_has_header_and_reasons():
    rows = list(csv.DictReader(OVERRIDES.open(newline="")))
    assert list(rows[0]) == ["year", "office", "state", "special", "winner_party", "caucus",
                             "reason"]
    assert rows and all(r["reason"].strip() for r in rows)


# ------------------------------------------------------------------ contests absent from MEDSL

def test_add_missing_contest_and_refuse_duplicates(raw, tmp_path):
    cols = ["year", "office", "state", "special", "winner_party", "winner_name", "caucus",
            "reason"]
    p = tmp_path / "m.csv"
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerow({"year": 2022, "office": "senate", "state": "CT", "special": "false",
                        "winner_party": "D", "winner_name": "Richard Blumenthal", "caucus": "",
                        "reason": "absent from MEDSL"})
    out = {key(c): c for c in truth.add_missing(list(raw.values()), p)}
    ct = out[(2022, "CT", False)]
    assert ct.winner_party == "D" and ct.seat_key == "CT-class3" and ct.source == "addition"
    w2 = tmp_path / "m2.csv"
    w2.write_text(p.read_text().replace("CT", "OK"))
    with pytest.raises(ValueError, match="already in"):
        truth.add_missing(list(raw.values()), w2)


# ------------------------------------------------------------------ the full downloaded file

@pytest.mark.skipif(not (FULL / "1976-2024-senate-state.csv").is_file(),
                    reason="MEDSL Senate file not downloaded on this machine")
def test_full_file_counts_and_overrides_apply():
    contests = truth.load_medsl_senate(FULL, YEARS)
    per = {y: sum(c.year == y for c in contests) for y in YEARS}
    assert per == {2018: 35, 2020: 35, 2022: 35, 2024: 35}   # CT 2022 is absent from MEDSL
    fixed = truth.add_missing(contests, MISSING)
    fixed = truth.apply_overrides(fixed, OVERRIDES, offices=("senate",))
    per = {y: sum(c.year == y for c in fixed) for y in YEARS}
    assert per == {2018: 35, 2020: 35, 2022: 36, 2024: 35}
    seats = {y: len({c.seat_key for c in fixed if c.year == y}) for y in YEARS}
    assert seats == {2018: 35, 2020: 35, 2022: 35, 2024: 34}
    assert all(c.winner_party in {"D", "R", "Other"} for c in fixed)
    assert all(c.winner_party != "Other" or c.caucus in {"D", "R", ""} for c in fixed)
