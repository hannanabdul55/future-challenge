"""History invariants (plan 04.1-13), offline.

The parameterised generator in fc.train.history must stay equal to the live one for 2026, the
race lists must equal the audited contests, and the registered eval windows must be the days the
packets are built for.
"""

from __future__ import annotations

import csv
import importlib.util
import json
from collections import Counter
from datetime import date
from itertools import pairwise
from pathlib import Path

import pytest

from fc import forecast_io as F
from fc import packet as P
from fc import questions as Q
from fc.train import history, paths, schedule

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "phase-04.1"
LIVE_RACES = ROOT / "questions" / "2026-us-midterms" / "races.csv"
HISTORY_YEARS = (2018, 2020, 2022, 2024)


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "build_history_script", ROOT / "scripts" / "train" / "build_history.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _audit_counts() -> Counter:
    with (DATA / "truth_audit.csv").open(newline="") as f:
        return Counter((int(r["year"]), r["office"]) for r in csv.DictReader(f))


def _races(year: int) -> list[dict[str, str]]:
    with (DATA / f"races_{year}.csv").open(newline="") as f:
        return list(csv.DictReader(f))


def _questions(year: int) -> list[dict]:
    return Q.load_questions(DATA / f"questions_{year}.json")


# --------------------------------------------------------------------------- 2026 guard

def test_live_hash_invariant():
    live_hash = json.loads((ROOT / "rounds" / "2026-us-midterms" / "season.json")
                           .read_text())["questions_hash"]
    mine = history.build_questions(LIVE_RACES, history.CYCLES[2026])
    assert Q.questions_hash(mine) == live_hash


def test_race_title_parity_with_the_live_packet_code():
    live = Q.generate_midterms(LIVE_RACES)
    races = [q for q in live if q["family"] in ("senate", "governor")]
    assert len(races) > 60  # every live race, not an empty loop
    for q in races:
        assert history.race_title(q, 2026) == P.race_page_title(q), q["question_id"]


# --------------------------------------------------------------------------- per cycle

@pytest.mark.parametrize("year", HISTORY_YEARS)
def test_question_set_validates_and_is_current(year):
    cycle = history.CYCLES[year]
    qs = _questions(year)
    rep = Q.validate_questions(qs, event_date=cycle.election_date.isoformat())
    assert rep.ok, str(rep)
    ids = [q["question_id"] for q in qs]
    assert len(ids) == len(set(ids))
    doc = json.loads((DATA / f"questions_{year}.json").read_text())
    assert doc["questions_hash"] == Q.questions_hash(qs)
    assert doc["season"] == cycle.season
    # the file on disk is what the generator makes from the race list today
    rebuilt = history.build_questions(DATA / f"races_{year}.csv", cycle)
    assert Q.questions_hash(rebuilt) == doc["questions_hash"]


@pytest.mark.parametrize("year", HISTORY_YEARS)
def test_special_suffix_only_for_a_second_same_office_contest(year):
    ids = {q["question_id"] for q in _questions(year)}
    for qid in ids:
        if qid.endswith("-special"):
            assert qid.removesuffix("-special") in ids, f"{qid} has no regular contest"
    rows = _races(year)
    per_state = Counter((r["office"], r["state"]) for r in rows)
    for r in rows:
        qid = f"{r['office']}-{r['state'].lower()}"
        if r["seat"] == "special" and per_state[(r["office"], r["state"])] > 1:
            assert f"{qid}-special" in ids
        else:
            assert qid in ids


@pytest.mark.parametrize("year,has_ca", [(2018, False), (2020, False), (2022, True), (2024, True)])
def test_california_same_seat_questions(year, has_ca):
    ids = {q["question_id"] for q in _questions(year)}
    assert ("senate-ca-special" in ids) is has_ca
    if has_ca:
        assert "senate-ca" in ids  # D-17: both questions are kept


@pytest.mark.parametrize("year", [*HISTORY_YEARS, 2026])
def test_days_are_nine_dates_ending_on_the_election(year):
    cycle = history.CYCLES[year]
    days = cycle.days()
    assert len(days) == 9
    assert days[-1] == cycle.election_date
    assert all((b - a).days == 1 for a, b in pairwise(days))
    assert days[0] == date.fromordinal(cycle.election_date.toordinal() - 8)


@pytest.mark.parametrize("year", HISTORY_YEARS)
def test_registered_windows_equal_the_days_the_packets_are_built_for(year):
    reg = schedule.load_registration()["windows"]["cycles"][str(year)]
    cycle = history.CYCLES[year]
    # a mismatch is a stop-and-report; registration.json is never edited to fit the code
    assert reg["days"] == [d.isoformat() for d in cycle.days()]
    assert reg["election"] == cycle.election_date.isoformat()


def test_schedule_reads_the_registration_in_the_data_directory():
    assert schedule.REGISTRATION_PATH == paths.REPO_DATA / "registration.json"


# --------------------------------------------------------------------------- race lists

@pytest.mark.parametrize("year", HISTORY_YEARS)
def test_race_list_equals_the_audited_contests(year):
    audit = _audit_counts()
    rows = _races(year)
    for office in ("senate", "governor"):
        assert sum(r["office"] == office for r in rows) == audit[(year, office)]
    with (DATA / "truth_audit.csv").open(newline="") as f:
        contests = {(r["office"], r["state"], r["special"].lower() == "true")
                    for r in csv.DictReader(f) if int(r["year"]) == year}
    assert {(r["office"], r["state"], r["seat"] == "special") for r in rows} == contests
    assert len(rows) == len(contests)


@pytest.mark.parametrize("year", HISTORY_YEARS)
def test_race_list_columns_are_complete(year):
    cycle = history.CYCLES[year]
    prev = f"pres_{cycle.prev_pres_year}"
    rows = _races(year)
    assert list(rows[0]) == ["office", "state", "state_name", "seat", "incumbent_party",
                             "open_seat", prev]
    for r in rows:
        assert r["incumbent_party"] in Q.PARTIES, r
        assert r["open_seat"] in ("true", "false"), r
        assert r[prev] in ("D", "R"), r
        assert r["state_name"] == P.STATE_NAMES[r["state"]]
        if r["office"] == "governor":
            assert r["seat"] == ""
        else:
            assert r["seat"] in (cycle.senate_class, "special")


def test_governor_counts_per_cycle():
    assert [sum(r["office"] == "governor" for r in _races(y)) for y in HISTORY_YEARS] == [
        36, 11, 36, 11]


# --------------------------------------------------------------------------- batches

def _expected_sizes(n: int) -> list[int]:
    return [20] * (n // 20) + ([n % 20] if n % 20 else [])


@pytest.mark.parametrize("year,expected", [
    (2018, {"control": [2], "governor": [20, 16], "seats": [2], "senate": [20, 15]}),
    (2020, {"control": [2], "governor": [11], "seats": [2], "senate": [20, 15]}),
    (2024, {"control": [2], "governor": [11], "seats": [2], "senate": [20, 15]}),
])
def test_batch_family_sizes(year, expected):
    got: dict[str, list[int]] = {}
    for b in F.batches(_questions(year)):
        got.setdefault(b[0]["family"], []).append(len(b))
    assert got == expected


def test_batch_family_sizes_2022_follow_the_audited_counts():
    audit = _audit_counts()
    got: dict[str, list[int]] = {}
    for b in F.batches(_questions(2022)):
        got.setdefault(b[0]["family"], []).append(len(b))
    assert got == {"control": [2], "seats": [2],
                   "governor": _expected_sizes(audit[(2022, "governor")]),
                   "senate": _expected_sizes(audit[(2022, "senate")])}


# --------------------------------------------------------------------------- summary parser

SUMMARY = """== Race summary ==
=== States ===
{| class="wikitable sortable"
! State
! Governor
! Party
! Status
|-
! [[#Minnesota|Minnesota]]
| {{sortname|Tim|Walz}}
| {{party shading/DFL}} | Democratic-Farmer-Labor
| {{Party shading/Hold}} | Incumbent retir{{show by date|2025|01|03|ing|ed}}.<ref name=x>{{cite web |url=u}}</ref>
|-
|[[2020 Montana gubernatorial election|Montana]]
|[[Steve Bullock]]
|{{party shading/Democratic}} |
|Democratic
|Term-limited{{efn|Bullock resigned later {{cite web|url=u}}}}
{|class="wikitable"
|-
|{{party shading/Republican}} |
|[[Greg Gianforte]]
|}
|-
! [[Washington]]
| [[Jay Inslee]]
| {{Party shading/Democratic}} | Democratic
| data-sort-value=1 | Incumbent running.
|}
"""


def test_summary_parser_reads_party_openness_and_nested_tables():
    mod = _load_script()
    table = mod.table_after(SUMMARY, r"^=+\s*States\s*=+\s*$")
    got = mod.parse_summary(table)
    assert got["Minnesota"]["party"] == "D" and got["Minnesota"]["open"] is True
    assert got["Montana"]["party"] == "D" and got["Montana"]["open"] is True
    assert got["Washington"]["party"] == "D" and got["Washington"]["open"] is False
    assert set(got) == {"Minnesota", "Montana", "Washington"}  # the nested table is not a row


def test_summary_parser_refuses_an_unreadable_row():
    mod = _load_script()
    bad = "{| class=\"wikitable\"\n|-\n! [[Ohio]]\n| Someone\n|}\n"
    with pytest.raises(ValueError, match="Ohio"):
        mod.parse_summary(bad)
