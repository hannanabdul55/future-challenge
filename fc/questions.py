"""Question specs v2 (DESIGN.md §4.6, rules 2026.10) and the Season 1 generator (QSPEC-01/02).

A question set is a JSON list of records. Three output types:

- ``binary``      — one probability, ``output_type_id = "p"``
- ``categorical`` — one probability per declared category; must sum to 1
- ``quantile``    — one value per level in the question's own ``quantile_levels``, with
                    optional integer ``bounds``

Every record carries the resolution block the resolver executes and the editorial fields
the checklist enforces. ``questions_hash`` is what the season manifest pins.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import commit as C

SCHEMA = "fc-question/2026.10"
OUTPUT_TYPES = ("binary", "categorical", "quantile")
RESOLVERS = ("automated", "council")
RISK_LABELS = ("low", "medium", "high")
QID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
PARTIES = ["D", "R", "Other"]

# hubverse 23-level set; the season uses a reduced symmetric 11-level set for seat counts
# because the hub-run models answer in free text and 23 numbers per question is noise.
SEAT_LEVELS = [0.025, 0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9, 0.975]

REQUIRED = ("schema", "question_id", "family", "statement", "output_type", "core",
            "resolution", "manipulation_risk", "rules_version")
REQUIRED_RESOLUTION = ("primary_source", "provisional_rule", "final_rule", "fallback_sources",
                       "void_if", "T_final", "resolver")


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def __str__(self) -> str:
        lines = [f"ERROR   {e}" for e in self.errors] + [f"WARNING {w}" for w in self.warnings]
        return "\n".join(lines) if lines else "OK"


# --------------------------------------------------------------------------- I/O + hash

def load_questions(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text())
    qs = data["questions"] if isinstance(data, dict) else data
    if not isinstance(qs, list):
        raise TypeError("question set must be a list")
    return qs


def questions_hash(questions: list[dict[str, Any]]) -> str:
    """Order-independent hash of the set: sha256 over canonical JSON of the id-sorted list."""
    ordered = sorted(questions, key=lambda q: q["question_id"])
    return hashlib.sha256(C.canonical_json(ordered)).hexdigest()


def write_question_set(path: Path, questions: list[dict[str, Any]], *, season: str) -> str:
    h = questions_hash(questions)
    doc = {"schema": SCHEMA, "season": season, "questions_hash": h,
           "questions": sorted(questions, key=lambda q: q["question_id"])}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    return h


# --------------------------------------------------------------------------- validation

def validate_question(q: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    qid = q.get("question_id", "<missing id>")
    for k in REQUIRED:
        if k not in q:
            errs.append(f"{qid}: missing {k}")
    if errs:
        return errs
    if q["schema"] != SCHEMA:
        errs.append(f"{qid}: schema {q['schema']!r} != {SCHEMA!r}")
    if not QID_RE.match(q["question_id"]):
        errs.append(f"{qid}: question_id must match {QID_RE.pattern}")
    if q["output_type"] not in OUTPUT_TYPES:
        errs.append(f"{qid}: output_type {q['output_type']!r} not in {OUTPUT_TYPES}")
    if q["output_type"] == "categorical":
        cats = q.get("categories")
        if not isinstance(cats, list) or len(cats) < 2 or len(set(cats)) != len(cats):
            errs.append(f"{qid}: categorical needs ≥ 2 distinct categories")
    if q["output_type"] == "quantile":
        lv = q.get("quantile_levels")
        if not isinstance(lv, list) or len(lv) < 3:
            errs.append(f"{qid}: quantile needs quantile_levels")
        else:
            s = sorted(float(x) for x in lv)
            if 0.5 not in s or any(abs(a - (1 - b)) > 1e-9 for a, b in zip(s, s[::-1])):
                errs.append(f"{qid}: quantile_levels must be symmetric and include 0.5")
        b = q.get("bounds")
        if b is not None and (not isinstance(b, list) or len(b) != 2 or b[0] >= b[1]):
            errs.append(f"{qid}: bounds must be [lo, hi] with lo < hi")
    if q["manipulation_risk"] not in RISK_LABELS:
        errs.append(f"{qid}: manipulation_risk must be one of {RISK_LABELS}")
    if not isinstance(q["core"], bool):
        errs.append(f"{qid}: core must be boolean")
    res = q["resolution"]
    if not isinstance(res, dict):
        errs.append(f"{qid}: resolution must be a mapping")
        return errs
    for k in REQUIRED_RESOLUTION:
        if k not in res:
            errs.append(f"{qid}: resolution missing {k}")
    if res.get("resolver") not in RESOLVERS:
        errs.append(f"{qid}: resolver must be one of {RESOLVERS}")
    if not isinstance(res.get("fallback_sources"), list) or not isinstance(res.get("void_if"), list):
        errs.append(f"{qid}: fallback_sources and void_if must be lists")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(res.get("T_final", ""))):
        errs.append(f"{qid}: resolution.T_final must be YYYY-MM-DD")
    # Individuals exclusion, mechanically: statements name offices and parties, not people.
    # A capitalised two-word sequence that is not a known office/state token is suspicious.
    return errs


def validate_questions(questions: list[dict[str, Any]], *,
                       event_date: str | None = None) -> Report:
    rep = Report()
    ids = [q.get("question_id") for q in questions]
    dup = sorted({str(i) for i in ids if ids.count(i) > 1})
    if dup:
        rep.errors.append(f"duplicate question_id(s): {dup}")
    for q in questions:
        rep.errors.extend(validate_question(q))
        if event_date and q.get("resolution", {}).get("T_final", "9999") <= event_date:
            rep.errors.append(f"{q.get('question_id')}: T_final must be after the event date")
    if not any(q.get("core") for q in questions):
        rep.warnings.append("no core questions; coverage imputation has nothing to enforce")
    return rep


# --------------------------------------------------------------------------- Season 1 generator

SEASON = "2026-us-midterms"
EVENT_DATE = "2026-11-03"
RULES = "2026.10"
T_FINAL_RACES = "2026-12-15"
T_FINAL_CHAMBERS = "2027-01-06"

RESULTS_SOURCE = {
    "senate": "https://en.wikipedia.org/wiki/2026_United_States_Senate_elections",
    "governor": "https://en.wikipedia.org/wiki/2026_United_States_gubernatorial_elections",
    "house": "https://en.wikipedia.org/wiki/2026_United_States_House_of_Representatives_elections",
}
OFFICE_NAME = {"senate": "U.S. Senate", "governor": "Governor"}


def _race_resolution(office: str) -> dict[str, Any]:
    return {
        "primary_source": RESULTS_SOURCE[office],
        "provisional_rule": "winner_party_as_recorded_in_results_table_stable_72h",
        "final_rule": "certified_result_from_state_election_authority",
        "fallback_sources": ["state election authority results page", "council"],
        "void_if": ["election for this seat postponed or cancelled",
                    "no winner certified by T_final and no runoff scheduled"],
        "T_final": T_FINAL_RACES,
        "resolver": "automated",
        "notes": "If a runoff is required, the question resolves on the runoff winner; "
                 "T_final extends to runoff certification. Independents and third-party "
                 "winners resolve as Other regardless of caucus.",
    }


def _chamber_resolution(chamber: str) -> dict[str, Any]:
    return {
        "primary_source": RESULTS_SOURCE[chamber],
        "provisional_rule": "seats_by_party_as_recorded_in_results_table_stable_72h",
        "final_rule": "membership_at_seating_of_the_120th_congress_2027-01-03",
        "fallback_sources": ["house.gov / senate.gov official rosters", "council"],
        "void_if": ["seating of the 120th Congress delayed beyond T_final"],
        "T_final": T_FINAL_CHAMBERS,
        "resolver": "automated",
        "notes": "Independents who caucus with the Democratic conference count toward the "
                 "Democratic caucus for seat-count and control questions; vacancies at seating "
                 "count for neither party.",
    }


def _base(qid: str, family: str, statement: str, output_type: str, **kw) -> dict[str, Any]:
    q = {"schema": SCHEMA, "question_id": qid, "family": family, "statement": statement,
         "output_type": output_type, "core": True, "manipulation_risk": "low",
         "rules_version": RULES, "event_date": EVENT_DATE}
    q.update(kw)
    return q


def generate_midterms(races_csv: Path) -> list[dict[str, Any]]:
    qs: list[dict[str, Any]] = []
    qs.append(_base(
        "control-house", "control",
        "Will the Democratic Party hold a majority of seats (at least 218 of 435) in the "
        "U.S. House of Representatives when the 120th Congress is seated?",
        "binary", resolution=_chamber_resolution("house")))
    qs.append(_base(
        "control-senate", "control",
        "Will the Democratic caucus (Democrats plus independents who caucus with them) hold at "
        "least 51 of 100 seats in the U.S. Senate when the 120th Congress is seated?",
        "binary", resolution=_chamber_resolution("senate")))
    qs.append(_base(
        "seats-house-d", "seats",
        "How many seats will the Democratic Party hold in the U.S. House of Representatives "
        "when the 120th Congress is seated (excluding independents)?",
        "quantile", quantile_levels=SEAT_LEVELS, bounds=[0, 435], unit="seats",
        resolution=_chamber_resolution("house")))
    qs.append(_base(
        "seats-senate-dcaucus", "seats",
        "How many of the 100 U.S. Senate seats will the Democratic caucus (Democrats plus "
        "independents who caucus with them) hold when the 120th Congress is seated?",
        "quantile", quantile_levels=SEAT_LEVELS, bounds=[0, 100], unit="seats",
        resolution=_chamber_resolution("senate")))

    with Path(races_csv).open(newline="") as f:
        for row in csv.DictReader(f):
            office, st = row["office"], row["state"]
            seat = f" ({row['seat']} seat)" if row.get("seat") else ""
            special = " special" if row.get("seat") == "special" else ""
            qid = f"{office}-{st.lower()}"
            stmt = (f"Which party's nominee will win the November 3, 2026{special} election for "
                    f"{OFFICE_NAME[office]} in {row['state_name']}{seat}?")
            qs.append(_base(
                qid, office, stmt, "categorical", categories=list(PARTIES),
                state=st, incumbent_party=row["incumbent_party"],
                open_seat=row["open_seat"].lower() == "true", pres_2024=row["pres_2024"],
                resolution=_race_resolution(office)))
    return qs


# --------------------------------------------------------------------------- reduced tasks.json

def to_tasks_round(questions: list[dict[str, Any]], *, round_id: str, T_open: str,
                   T_commit: str, T_reveal: str, rules_version: str = RULES) -> dict[str, Any]:
    """The reduced hubverse-shaped round block `fc validate` consumes (see fc/validate.py)."""
    out = []
    for q in questions:
        item = {"question_id": q["question_id"], "family": q["family"],
                "output_type": q["output_type"], "core": bool(q.get("core", True))}
        if q["output_type"] == "categorical":
            item["categories"] = list(q["categories"])
        if q["output_type"] == "quantile":
            item["quantile_levels"] = list(q["quantile_levels"])
            if q.get("bounds") is not None:
                item["bounds"] = list(q["bounds"])
        out.append(item)
    return {"round_id": round_id, "T_open": T_open, "T_commit": T_commit, "T_reveal": T_reveal,
            "rules_version": rules_version, "quantile_levels": SEAT_LEVELS, "questions": out}
