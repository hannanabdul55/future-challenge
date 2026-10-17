"""Election cycles 2018-2026 and their question sets (D-07, D-16, D-17).

``fc.questions.generate_midterms`` and ``fc.packet.race_page_title`` keep their 2026 constants:
season.json pins the live questions hash and the live packet code is in use. The
year-parameterised copies live here (assumption-delta decision "add-alongside"). The guard
against silent drift is that ``build_questions`` with ``CYCLES[2026]`` reproduces the live
questions hash and ``race_title(q, 2026) == race_page_title(q)`` for every live race.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fc import packet as P
from fc import questions as Q

from . import wiki_asof

DAYS_BEFORE = 8  # D-16: the nine packet days are E-8 .. E


@dataclass(frozen=True)
class Cycle:
    year: int
    election_date: date
    congress: str            # ordinal of the Congress seated after the election, e.g. "118th"
    seating_date: date
    senate_class: str        # the regular Senate class up this year
    prev_pres_year: int
    season: str
    t_final_races: date
    t_final_chambers: date

    def days(self) -> list[date]:
        """The nine packet days E-8 .. E (D-16)."""
        return [self.election_date - timedelta(days=n) for n in range(DAYS_BEFORE, -1, -1)]

    @property
    def date_text(self) -> str:
        return f"{self.election_date:%B} {self.election_date.day}, {self.year}"


CYCLES: dict[int, Cycle] = {c.year: c for c in (
    Cycle(2018, date(2018, 11, 6), "116th", date(2019, 1, 3), "class1", 2016,
          "2018-us-midterms", date(2018, 12, 15), date(2019, 1, 6)),
    Cycle(2020, date(2020, 11, 3), "117th", date(2021, 1, 3), "class2", 2016,
          "2020-us-elections", date(2020, 12, 15), date(2021, 1, 6)),
    Cycle(2022, date(2022, 11, 8), "118th", date(2023, 1, 3), "class3", 2020,
          "2022-us-midterms", date(2022, 12, 15), date(2023, 1, 6)),
    Cycle(2024, date(2024, 11, 5), "119th", date(2025, 1, 3), "class1", 2020,
          "2024-us-elections", date(2024, 12, 15), date(2025, 1, 6)),
    Cycle(2026, date(2026, 11, 3), "120th", date(2027, 1, 3), "class2", 2024,
          "2026-us-midterms", date(2026, 12, 15), date(2027, 1, 6)),
)}


# --------------------------------------------------------------------------- titles

def national_pages(year: int) -> dict[str, str]:
    return {"house": f"{year} United States House of Representatives elections",
            "senate": f"{year} United States Senate elections"}


def senate_title(year: int, state_name: str, *, special: bool) -> str:
    kind = "special election" if special else "election"
    return f"{year} United States Senate {kind} in {state_name}"


def governor_title(year: int, state_name: str) -> str:
    return f"{year} {state_name} gubernatorial election"


def race_title(q: dict[str, Any], year: int) -> str | None:
    """Year-parameterised copy of ``fc.packet.race_page_title``."""
    fam = q.get("family")
    name = q.get("state_name") or P.STATE_NAMES.get(q.get("state", ""))
    if not name:
        return None
    if fam == "senate":
        return senate_title(year, name, special="special" in q.get("statement", ""))
    if fam == "governor":
        return governor_title(year, name)
    return None


# --------------------------------------------------------------------------- questions

def _results_source(cycle: Cycle) -> dict[str, str]:
    y = cycle.year
    return {
        "senate": f"https://en.wikipedia.org/wiki/{y}_United_States_Senate_elections",
        "governor": f"https://en.wikipedia.org/wiki/{y}_United_States_gubernatorial_elections",
        "house": f"https://en.wikipedia.org/wiki/{y}_United_States_House_of_Representatives_elections",
    }


def _race_resolution(office: str, cycle: Cycle) -> dict[str, Any]:
    res = Q._race_resolution(office)
    res["primary_source"] = _results_source(cycle)[office]
    res["T_final"] = cycle.t_final_races.isoformat()
    return res


def _chamber_resolution(chamber: str, cycle: Cycle) -> dict[str, Any]:
    res = Q._chamber_resolution(chamber)
    res["primary_source"] = _results_source(cycle)[chamber]
    res["final_rule"] = (f"membership_at_seating_of_the_{cycle.congress}_congress_"
                         f"{cycle.seating_date.isoformat()}")
    res["void_if"] = [f"seating of the {cycle.congress} Congress delayed beyond T_final"]
    res["T_final"] = cycle.t_final_chambers.isoformat()
    return res


def build_questions(races_csv: Path, cycle: Cycle) -> list[dict[str, Any]]:
    """Parameterised copy of ``Q.generate_midterms`` for any cycle."""
    ev = cycle.election_date.isoformat()
    cong = cycle.congress
    qs: list[dict[str, Any]] = []
    qs.append(Q._base(
        "control-house", "control",
        "Will the Democratic Party hold a majority of seats (at least 218 of 435) in the "
        f"U.S. House of Representatives when the {cong} Congress is seated?",
        "binary", event_date=ev, resolution=_chamber_resolution("house", cycle)))
    qs.append(Q._base(
        "control-senate", "control",
        "Will the Democratic caucus (Democrats plus independents who caucus with them) hold at "
        f"least 51 of 100 seats in the U.S. Senate when the {cong} Congress is seated?",
        "binary", event_date=ev, resolution=_chamber_resolution("senate", cycle)))
    qs.append(Q._base(
        "seats-house-d", "seats",
        "How many seats will the Democratic Party hold in the U.S. House of Representatives "
        f"when the {cong} Congress is seated (excluding independents)?",
        "quantile", event_date=ev, quantile_levels=Q.SEAT_LEVELS, bounds=[0, 435], unit="seats",
        resolution=_chamber_resolution("house", cycle)))
    qs.append(Q._base(
        "seats-senate-dcaucus", "seats",
        "How many of the 100 U.S. Senate seats will the Democratic caucus (Democrats plus "
        f"independents who caucus with them) hold when the {cong} Congress is seated?",
        "quantile", event_date=ev, quantile_levels=Q.SEAT_LEVELS, bounds=[0, 100], unit="seats",
        resolution=_chamber_resolution("senate", cycle)))

    with Path(races_csv).open(newline="") as f:
        rows = list(csv.DictReader(f))
    regular = {(r["office"], r["state"]) for r in rows if r.get("seat") != "special"}
    pres_key = f"pres_{cycle.prev_pres_year}"
    for row in rows:
        office, st = row["office"], row["state"]
        is_special = row.get("seat") == "special"
        seat = f" ({row['seat']} seat)" if row.get("seat") else ""
        special = " special" if is_special else ""
        qid = f"{office}-{st.lower()}"
        if is_special and (office, st) in regular:
            qid += "-special"
        stmt = (f"Which party's nominee will win the {cycle.date_text}{special} election for "
                f"{Q.OFFICE_NAME[office]} in {row['state_name']}{seat}?")
        qs.append(Q._base(
            qid, office, stmt, "categorical", event_date=ev, categories=list(Q.PARTIES),
            state=st, incumbent_party=row["incumbent_party"],
            open_seat=row["open_seat"].lower() == "true", **{pres_key: row[pres_key]},
            resolution=_race_resolution(office, cycle)))
    rep = Q.validate_questions(qs, event_date=ev)
    if not rep.ok:
        raise ValueError(f"{cycle.year} question set invalid:\n{rep}")
    return qs


def write_questions(path: Path, qs: list[dict[str, Any]], cycle: Cycle) -> str:
    return Q.write_question_set(Path(path), qs, season=cycle.season)


# --------------------------------------------------------------------------- race list

RACE_COLUMNS = ["office", "state", "state_name", "seat", "incumbent_party", "open_seat",
                "pres_2020"]


def derive_races(cycle: Cycle, cache: wiki_asof.WikiCache) -> list[dict[str, str]]:
    """Race list by title existence at the first packet day's 00:00 UTC.

    A race exists when the title resolves to a revision without a redirect to another title.
    One exception: when one seat has a regular and a special contest in the same year,
    Wikipedia may keep a single combined article, "{Y} United States Senate elections in
    {State}" (California 2022), and both per-contest titles redirect to it. Both contests
    exist then (D-17 keeps both questions), and both questions read the combined page.
    ``incumbent_party``, ``open_seat`` and the previous presidential winner are not shown to
    the model and are filled by plan 04.1-13."""
    asof = f"{cycle.days()[0].isoformat()}T00:00:00Z"
    pres_key = f"pres_{cycle.prev_pres_year}"
    cols = [*RACE_COLUMNS[:6], pres_key]

    def exists(title: str, combined: str | None = None) -> bool:
        try:
            rec = cache.rev_at(title, asof)
        except LookupError:
            return False
        if rec["title"] == combined:
            return True
        return rec["title"] == title and rec["redirected_from"] is None

    out: list[dict[str, str]] = []
    for office in ("senate", "governor"):
        for st, name in sorted(P.STATE_NAMES.items()):
            combined = None
            if office == "senate":
                combined = f"{cycle.year} United States Senate elections in {name}"
                found = [(senate_title(cycle.year, name, special=False), cycle.senate_class),
                         (senate_title(cycle.year, name, special=True), "special")]
            else:
                found = [(governor_title(cycle.year, name), "")]
            for title, seat in found:
                if exists(title, combined):
                    row = dict.fromkeys(cols, "")
                    row.update(office=office, state=st, state_name=name, seat=seat)
                    out.append(row)
    return out


def write_races(path: Path, rows: list[dict[str, str]], cycle: Cycle) -> None:
    cols = [*RACE_COLUMNS[:6], f"pres_{cycle.prev_pres_year}"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
