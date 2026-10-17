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


# =================================================================== Task 2: Wikipedia, pres, audit

PLAIN = """
==General election==
{| class="wikitable"
! Party !! Candidate !! Votes !! %
|-
| Democratic || Jane Roe || 1,000 || 55.6%
|-
| Republican || John Doe || 800 || 44.4%
|-
| Total || || 1,800 || 100%
|}
"""

BOX = """
==Democratic primary==
{{Election box begin |title=Democratic primary}}
{{Election box winning candidate with party link|party=Democratic Party (United States)|candidate=Primary Winner|votes=9|percentage=90%}}
{{Election box candidate with party link|party=Democratic Party (United States)|candidate=Primary Loser|votes=1|percentage=10%}}
{{Election box end}}
==General election==
===Results===
{{Election box begin |title=General<ref>{{cite web |url=https://example.org/a|b}}</ref>}}
{{Election box candidate with party link|party=Democratic Party (United States)|candidate=''Pat Fusion''|votes=700|percentage=50.0%}}
{{Election box candidate with party link|party=Working Families Party|candidate=''Pat Fusion''|votes=100|percentage=7.1%}}
{{Election box winning candidate|party=Total| candidate = [[Pat Fusion]] (incumbent)
| votes = 800
| percentage = 57.1%
}}
{{Election box candidate with party link|party=Republican Party (United States)|candidate=[[Lee Other]]|votes=600|percentage=42.9%}}
{{Election box write-in with party link|votes=5|percentage=0.0%}}
{{Election box total
  |votes      =1,405
  |percentage =100.00%
}}
{{Election box end}}
"""

RUNOFF = BOX + """
==Runoff==
{{Election box begin |title=Runoff}}
{{Election box candidate with party link|party=Republican Party (United States)|candidate=Lee Other|votes=900|percentage=55%}}
{{Election box candidate with party link|party=Democratic Party (United States)|candidate=Pat Fusion|votes=700|percentage=45%}}
{{Election box end}}
"""

COMBINED = """
==Regular election==
{{Election box begin |title=Regular}}
{{Election box candidate|party=Democratic Party (United States)|candidate=A|votes=60|percentage=60%}}
{{Election box candidate|party=Republican Party (United States)|candidate=B|votes=40|percentage=40%}}
{{Election box end}}
==Special election==
{{Election box begin |title=Special}}
{{Election box candidate|party=Democratic Party (United States)|candidate=A|votes=30|percentage=30%}}
{{Election box candidate|party=Republican Party (United States)|candidate=B|votes=70|percentage=70%}}
{{Election box end}}
"""


def test_results_table_winner_plain_wikitable():
    w = truth.results_table_winner(PLAIN)
    assert w["party"] == "D" and w["name"] == "Jane Roe"
    assert w["d_pct"] == pytest.approx(1000 / 1800) and w["r_pct"] == pytest.approx(800 / 1800)


def test_results_table_winner_election_box_fusion_and_primary():
    w = truth.results_table_winner(BOX)
    assert w["name"] == "Pat Fusion" and w["party"] == "D"   # the primary block is ignored
    assert w["votes"] == 800                                  # the Total line, not 700 or 100
    assert w["d_pct"] == pytest.approx(800 / 1400)            # write-ins are not candidates
    assert w["section"].endswith("Results")


def test_results_table_winner_runoff_replaces_and_special_hint():
    w = truth.results_table_winner(RUNOFF)
    assert w["party"] == "R" and w["name"] == "Lee Other"
    assert truth.results_table_winner(COMBINED)["party"] == "D"
    assert truth.results_table_winner(COMBINED, contest="special")["party"] == "R"
    assert truth.results_table_winner("no results here") is None


SUMMARY = """
==Race summary==
===States===
{| class="wikitable"
! State !! Incumbent !! Party !! First elected !! Result !! Candidates
|-
| Alpha || {{sortname|Al|Fa}} || Republican || 2014 || Incumbent re-elected. || x
|-
| Beta || {{sortname|Be|Ta}} || {{party shading/Text/Democratic}} || 2018 || Incumbent term-limited.Republican gain. || x
|-
| Gamma || x || DFL || 2018 || Incumbent re-elected. || x
|-
| Delta || x || garbled party note || 2018 || Incumbent re-elected. || x
|}
"""


def test_governor_summary_reads_gain_hold_and_incumbent_party(monkeypatch):
    monkeypatch.setattr(truth, "_NAME_TO_PO", {"Alpha": "AA", "Beta": "BB", "Gamma": "GG",
                                               "Delta": "DD"})
    g = truth.governor_truth(SUMMARY)
    assert g["AA"]["party"] == "R" and g["BB"]["party"] == "R" and g["GG"]["party"] == "D"
    assert g["DD"]["party"] is None                       # unreadable: flagged, never guessed


def _contest(state, party, year=2022, office="senate", special=False):
    return truth.Contest(year, office, state, special, f"{state}-x", party, "N", party, None,
                         None, "medsl")


def test_disagreement_between_sources_is_one_audit_row_with_both_values(tmp_path):
    a = {(2022, "senate", "XX", False): "D", (2022, "senate", "YY", False): "D"}
    b = {(2022, "senate", "XX", False): {"party": "R", "d_pct": 0.4, "r_pct": 0.6},
         (2022, "senate", "YY", False): {"party": "D", "d_pct": 0.6, "r_pct": 0.4}}
    contests = [_contest("XX", "D"), _contest("YY", "D")]
    rows = truth.audit_rows(contests, a, b)
    xx = next(r for r in rows if r["state"] == "XX")
    assert (xx["source_a"], xx["source_b"]) == ("D", "R")
    assert xx["final"] == "" and "UNRESOLVED" in xx["note"]   # no override yet
    assert len(rows) == 2 and len({(r["year"], r["state"]) for r in rows}) == 2
    fixed = [_contest("XX", "R"), _contest("YY", "D")]
    fixed[0] = truth.Contest(2022, "senate", "XX", False, "XX-x", "R", "N", "R", None, None,
                             "medsl+override")
    ov = tmp_path / "o.csv"
    _write(ov, [{"year": 2022, "office": "senate", "state": "XX", "special": "false",
                 "winner_party": "R", "caucus": "R", "reason": "certified R, see URL"}])
    rows = truth.audit_rows(fixed, a, b, overrides_csv=ov)
    xx = next(r for r in rows if r["state"] == "XX")
    assert xx["final"] == "R" and "certified R, see URL" in xx["note"]


def test_agreeing_sources_have_empty_note():
    a = {(2022, "senate", "YY", False): "D"}
    b = {(2022, "senate", "YY", False): {"party": "D", "d_pct": 0.6, "r_pct": 0.4}}
    row = truth.audit_rows([_contest("YY", "D")], a, b)[0]
    assert row["note"] == "" and row["final"] == "D" and row["d_pct"] == pytest.approx(0.6)


def test_missing_second_source_is_unresolved_without_override():
    a = {(2022, "senate", "YY", False): "D"}
    row = truth.audit_rows([_contest("YY", "D")], a, {})[0]
    assert row["source_b"] == "?" and "UNRESOLVED" in row["note"] and row["final"] == ""


PRES_HEAD = ("year,state,state_po,state_fips,state_cen,state_ic,office,candidate,party_detailed,"
             "writein,candidatevotes,totalvotes,version,notes,party_simplified\n")


def test_pres_margins_statewide_fusion_and_dc(tmp_path):
    rows = [
        # NY-like: the Democrat is on two lines whose sum beats the Republican
        "2016,NEW YORK,NY,36,13,11,US PRESIDENT,\"CLINTON, HILLARY\",DEMOCRAT,False,4000,10000,1,,DEMOCRAT",
        "2016,NEW YORK,NY,36,13,11,US PRESIDENT,\"CLINTON, HILLARY\",WORKING FAMILIES,False,500,10000,1,,OTHER",
        "2016,NEW YORK,NY,36,13,11,US PRESIDENT,\"TRUMP, DONALD J.\",REPUBLICAN,False,4200,10000,1,,REPUBLICAN",
        "2016,NEW YORK,NY,36,13,11,US PRESIDENT,,,False,1300,10000,1,,OTHER",
        "2016,DISTRICT OF COLUMBIA,DC,11,53,55,US PRESIDENT,\"CLINTON, HILLARY\",DEMOCRAT,False,90,100,1,,DEMOCRAT",
        "2020,TEXAS,TX,48,74,49,US PRESIDENT,\"TRUMP, DONALD J.\",REPUBLICAN,False,520,1000,1,,REPUBLICAN",
        "2020,TEXAS,TX,48,74,49,US PRESIDENT,\"BIDEN, JOSEPH R. JR\",DEMOCRAT,False,460,1000,1,,DEMOCRAT",
    ]
    p = tmp_path / "pres.csv"
    p.write_text(PRES_HEAD + "\n".join(rows) + "\n")
    out = {(r["year"], r["state"]): r for r in truth.pres_margins(p, (2016, 2020))}
    assert (2016, "DC") not in out                                # fifty states only
    ny = out[(2016, "NY")]
    assert ny["winner"] == "D" and ny["d_pct"] == pytest.approx(0.45)
    assert ny["margin"] == pytest.approx(0.45 - 0.42)
    tx = out[(2020, "TX")]
    assert tx["winner"] == "R" and tx["margin"] == pytest.approx(0.46 - 0.52)


PRES = paths.MEDSL / "president"


@pytest.mark.skipif(not (PRES / "1976-2024-president.csv").is_file(),
                    reason="MEDSL president file not downloaded on this machine")
def test_pres_margins_full_file_has_fifty_states_per_year():
    rows = truth.pres_margins(PRES, (2016, 2020))
    for y, (d_states, r_states) in {2016: (20, 30), 2020: (25, 25)}.items():
        yr = [r for r in rows if r["year"] == y]
        assert len(yr) == 50 and {r["winner"] for r in yr} <= {"D", "R"}
        assert sum(r["winner"] == "D" for r in yr) == d_states
        assert sum(r["winner"] == "R" for r in yr) == r_states
        assert all(r["margin"] == pytest.approx(r["d_pct"] - r["r_pct"]) for r in yr)
    assert next(r for r in rows if (r["year"], r["state"]) == (2020, "ME"))["winner"] == "D"
    assert next(r for r in rows if (r["year"], r["state"]) == (2020, "NE"))["winner"] == "R"


AUDIT = paths.REPO_DATA / "truth_audit.csv"


@pytest.mark.skipif(not AUDIT.is_file(), reason="truth_audit.csv not built yet")
def test_real_audit_is_complete_and_every_difference_has_an_override():
    rows = list(csv.DictReader(AUDIT.open(newline="")))
    assert list(rows[0])[:8] == ["year", "office", "state", "special", "source_a", "source_b",
                                 "final", "note"]
    keys = [(r["year"], r["office"], r["state"], r["special"]) for r in rows]
    assert len(keys) == len(set(keys))                        # each contest exactly once
    per = {}
    for r in rows:
        per[(r["year"], r["office"])] = per.get((r["year"], r["office"]), 0) + 1
    assert per == {("2018", "senate"): 35, ("2020", "senate"): 35, ("2022", "senate"): 36,
                   ("2024", "senate"): 35, ("2018", "governor"): 36, ("2020", "governor"): 11,
                   ("2022", "governor"): 36, ("2024", "governor"): 11}
    ov = {(r["year"], r["office"], r["state"], r["special"])
          for r in csv.DictReader(OVERRIDES.open(newline=""))}
    for r in rows:
        assert r["final"] in {"D", "R", "Other"}
        if r["source_a"] != r["final"] or r["source_b"] != r["final"]:
            assert r["note"].strip(), r
        if r["source_a"] != r["source_b"]:
            assert (r["year"], r["office"], r["state"], r["special"]) in ov, r
