"""Race lists and live-schema question sets for 2018, 2020, 2022 and 2024 (D-07, D-16, D-17).

    uv run python scripts/train/build_history.py races --years 2018 2020 2022 2024
    uv run python scripts/train/build_history.py questions --years 2018 2020 2022 2024

``races`` writes data/phase-04.1/races_<Y>.csv. The contests come from the audited
truth_audit.csv (plan 04.1-04), one race row per (year, office, state, special). The facts that
are not shown to the model, ``incumbent_party`` and ``open_seat``, are read from the "Race
summary" table of "{Y} United States Senate elections" and "{Y} United States gubernatorial
elections" at the revision current at the cycle's E-8 00:00 UTC, through the plan-02 disk cache;
``pres_<prev>`` is the winner in pres_margins.csv. Every race title must resolve at the same
instant: a title that does not is a stop-and-report, never a skip. The revisions read and any
title that resolved through a redirect are written to data/phase-04.1/races_sources.json.

``questions`` writes data/phase-04.1/questions_<Y>.json with ``history.write_questions``.

Wikipedia responses are untrusted data: they are read through WikiCache only and cut apart with
regular expressions here; nothing in them is evaluated.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from typing import Any

from fc import packet as P
from fc import questions as Q
from fc.train import history, paths, wiki_asof

AUDIT = paths.REPO_DATA / "truth_audit.csv"
PRES = paths.REPO_DATA / "pres_margins.csv"
SOURCES = paths.REPO_DATA / "races_sources.json"
YEARS = (2018, 2020, 2022, 2024)

_NAMES = sorted(P.STATE_NAMES.values(), key=len, reverse=True)
_ABBR = {name: abbr for abbr, name in P.STATE_NAMES.items()}

# A status cell starts with one of these words; the history and candidate cells never do.
_STATUS_START = re.compile(
    r"^(incumbent|interim|appointee|renominated|nominated|term-limited|retiring|lost|resign)",
    re.IGNORECASE)
# The incumbent is not on the general-election ballot.
_NOT_RUNNING = re.compile(
    r"retir|term-limited|term limited|lost (?:re)?nomination|lost primary|defeated in primary"
    r"|resign|to be elected", re.IGNORECASE)


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


# --------------------------------------------------------------------------- wikitext tables

def table_after(text: str, heading_re: str) -> str | None:
    """Outer wikitable that follows the first heading matching ``heading_re``. Tables nested
    inside a row (the 2020 governor page) are dropped, so row splitting is not confused."""
    m = re.search(heading_re, text, flags=re.MULTILINE)
    if not m:
        return None
    start = text.find("{|", m.end())
    if start < 0:
        return None
    depth, out = 0, []
    for line in text[start:].split("\n"):
        s = line.strip()
        if s.startswith("{|"):
            depth += 1
            if depth > 1:
                continue
        elif s.startswith("|}"):
            if depth > 1:
                depth -= 1
                continue
            break
        elif depth > 1:
            continue
        out.append(line)
    return "\n".join(out)


def split_rows(table: str) -> list[list[str]]:
    """Rows as lists of raw cells. A cell starts on a line that begins with ``|`` or ``!``."""
    rows: list[list[str]] = []
    cur: list[str] | None = None
    for line in table.split("\n"):
        s = line.rstrip()
        if s.startswith("|-"):
            cur = []
            rows.append(cur)
        elif cur is None:
            continue
        elif s.startswith(("|", "!")):
            cur.append(s[1:])
        elif cur:
            cur[-1] += "\n" + s
    return [r for r in rows if r]


def clean(cell: str) -> str:
    c = re.sub(r"<ref[^>]*/>", "", cell)
    c = re.sub(r"<ref[^>]*>.*?</ref>", "", c, flags=re.DOTALL)
    prev = None
    while prev != c:  # innermost templates first, so nested ones go too
        prev = c
        c = re.sub(r"\{\{[^{}]*\}\}", "", c)
    c = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", c)
    c = re.sub(r"<br\s*/?>", " ", c)
    c = re.sub(r"\s+", " ", c).strip()
    if "|" in c:  # leftover attribute prefix such as "data-sort-value=2 |" or "nowrap |"
        c = c.split("|", 1)[1].strip()
    return c.strip(" |")


def state_of(cell: str) -> str | None:
    text = clean(cell).lstrip("#")
    for name in _NAMES:
        if text.startswith(name):
            return name
    return None


def party_of(cells: list[str]) -> str | None:
    """D, R or Other from the first party-shading template that is not a hold/gain marker."""
    for c in cells:
        m = re.search(r"\{\{[Pp]arty shading/([^}]*)\}\}", c)
        if m and m.group(1).strip().lower() not in ("hold", "gain"):
            name = m.group(1).strip().lower()
            if "democrat" in name or name == "dfl":  # DFL is Minnesota's Democratic party
                return "D"
            return "R" if "republican" in name else "Other"
    return None


def parse_summary(table: str) -> dict[str, dict[str, Any]]:
    """{state_name: {party, status, open}} for every state row of a Race summary table."""
    out: dict[str, dict[str, Any]] = {}
    for cells in split_rows(table):
        state = state_of(cells[0])
        if state is None:
            continue
        party = party_of(cells[1:5])
        status = [clean(c) for c in cells[1:] if _STATUS_START.match(clean(c))]
        if party is None or len(status) != 1:
            raise ValueError(f"{state}: party {party!r}, status cells {status!r}")
        if state in out:
            raise ValueError(f"{state} appears twice in one summary table")
        out[state] = {"party": party, "status": status[0],
                      "open": bool(_NOT_RUNNING.search(status[0]))}
    return out


# --------------------------------------------------------------------------- inputs

def read_audit() -> dict[int, set[tuple[str, str, bool]]]:
    out: dict[int, set[tuple[str, str, bool]]] = {y: set() for y in YEARS}
    with AUDIT.open(newline="") as f:
        for r in csv.DictReader(f):
            y = int(r["year"])
            if y in out:
                out[y].add((r["office"], r["state"], r["special"].lower() == "true"))
    return out


def read_pres() -> dict[tuple[int, str], str]:
    with PRES.open(newline="") as f:
        return {(int(r["year"]), r["state"]): r["winner"] for r in csv.DictReader(f)}


def summary_page(cache: wiki_asof.WikiCache, year: int, kind: str,
                 asof: str) -> tuple[str, dict[str, Any]]:
    title = f"{year} United States {kind} elections"
    rec = cache.rev_at(title, asof)
    return cache.wikitext(rec["revid"]), {
        "title": rec["title"], "revid": rec["revid"], "timestamp": rec["timestamp"]}


def incumbents(cache: wiki_asof.WikiCache, cycle: history.Cycle,
               asof: str) -> tuple[dict[str, dict], dict[str, dict]]:
    """(senate, governor) tables for a cycle; senate maps "regular" and "special" tables."""
    y = cycle.year
    stext, smeta = summary_page(cache, y, "Senate", asof)
    gtext, gmeta = summary_page(cache, y, "gubernatorial", asof)
    main = table_after(stext, r"^=+\s*Elections leading to the next Congress\s*=+\s*$")
    spec = table_after(stext, r"^=+\s*Special elections during the preceding Congress\s*=+\s*$")
    gov = table_after(gtext, r"^=+\s*States\s*=+\s*$")
    if main is None or spec is None or gov is None:
        raise ValueError(f"{y}: a Race summary table is missing "
                         f"(main {main is not None}, special {spec is not None}, "
                         f"governor {gov is not None})")
    senate = {"regular": parse_summary(main), "special": parse_summary(spec)}
    return ({"tables": senate, "page": smeta},
            {"table": parse_summary(gov), "page": gmeta})


# --------------------------------------------------------------------------- races

def race_order(contests: set[tuple[str, str, bool]]) -> list[tuple[str, str, bool]]:
    """Senate before governor, state abbreviation order, regular before special: the order
    ``history.derive_races`` produced, so races_2022.csv keeps its row order."""
    return sorted(contests, key=lambda c: (c[0] != "senate", c[1], c[2]))


def build_races(year: int, cache: wiki_asof.WikiCache, contests: set[tuple[str, str, bool]],
                pres: dict[tuple[int, str], str]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    cycle = history.CYCLES[year]
    asof = f"{cycle.days()[0].isoformat()}T00:00:00Z"
    sen, gov = incumbents(cache, cycle, asof)
    notes: dict[str, Any] = {"asof": asof, "senate_page": sen["page"],
                             "governor_page": gov["page"], "redirects": [], "same_seat": []}
    rows: list[dict[str, str]] = []
    for office, st, special in race_order(contests):
        name = P.STATE_NAMES[st]
        if office == "senate":
            own, other = ("special", "regular") if special else ("regular", "special")
            info = sen["tables"][own].get(name)
            if info is None:  # one article and one incumbent for a regular and special pair
                info = sen["tables"][other].get(name)
                if info is None:
                    raise ValueError(f"{year} senate {st} special={special}: in neither table")
                notes["same_seat"].append(f"{st}: {own} contest read from the {other} row")
            title = history.senate_title(year, name, special=special)
            seat = "special" if special else cycle.senate_class
        else:
            info = gov["table"].get(name)
            if info is None:
                raise ValueError(f"{year} governor {st}: not in the Race summary table")
            title = history.governor_title(year, name)
            seat = ""
        rec = cache.rev_at(title, asof)  # LookupError here is the stop-and-report
        combined = f"{year} United States Senate elections in {name}"
        if rec["redirected_from"] is not None or rec["title"] != title:
            notes["redirects"].append({"title": title, "resolves_to": rec["title"],
                                       "revid": rec["revid"], "timestamp": rec["timestamp"],
                                       "combined_article": rec["title"] == combined})
        prev = pres.get((cycle.prev_pres_year, st))
        if prev is None:
            raise ValueError(f"no pres_{cycle.prev_pres_year} winner for {st}")
        rows.append({"office": office, "state": st, "state_name": name, "seat": seat,
                     "incumbent_party": info["party"],
                     "open_seat": "true" if info["open"] else "false",
                     f"pres_{cycle.prev_pres_year}": prev})
    return rows, notes


def cmd_races(years: list[int]) -> None:
    audit, pres = read_audit(), read_pres()
    cache = wiki_asof.WikiCache()
    sources: dict[str, Any] = {}
    for year in years:
        cycle = history.CYCLES[year]
        path = paths.REPO_DATA / f"races_{year}.csv"
        contests = audit[year]
        if year == 2022 and path.is_file():
            with path.open(newline="") as f:
                have = {(r["office"], r["state"], r["seat"] == "special")
                        for r in csv.DictReader(f)}
            if have != contests:
                fail("2022 title-derived list differs from the audited contests: "
                     f"only in races_2022.csv {sorted(have - contests)}; "
                     f"only in truth_audit.csv {sorted(contests - have)}")
        try:
            rows, notes = build_races(year, cache, contests, pres)
        except (LookupError, ValueError) as exc:
            fail(f"{year}: {exc}")
        history.write_races(path, rows, cycle)
        sources[str(year)] = notes
        n_sen = sum(r["office"] == "senate" for r in rows)
        n_gov = sum(r["office"] == "governor" for r in rows)
        n_open = sum(r["open_seat"] == "true" for r in rows)
        print(f"{year}: senate {n_sen}, governor {n_gov}, open seats {n_open}, "
              f"redirected titles {len(notes['redirects'])}, wrote {path}")
    previous: dict[str, Any] = {}
    if SOURCES.is_file():
        previous = json.loads(SOURCES.read_text())
    previous.update(sources)
    SOURCES.write_text(json.dumps(previous, indent=1, ensure_ascii=False, sort_keys=True) + "\n")


# --------------------------------------------------------------------------- questions

def cmd_questions(years: list[int]) -> None:
    for year in years:
        cycle = history.CYCLES[year]
        races = paths.REPO_DATA / f"races_{year}.csv"
        out = paths.REPO_DATA / f"questions_{year}.json"
        qs = history.build_questions(races, cycle)  # raises on an invalid set
        h = history.write_questions(out, qs, cycle)
        loaded = Q.load_questions(out)
        rep = Q.validate_questions(loaded, event_date=cycle.election_date.isoformat())
        if not rep.ok or Q.questions_hash(loaded) != h:
            fail(f"{out} does not validate or re-hash:\n{rep}")
        print(f"{year}: {len(loaded)} questions, questions_hash {h}, wrote {out}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("races", "questions"):
        p = sub.add_parser(name)
        p.add_argument("--years", type=int, nargs="+", default=list(YEARS), choices=YEARS)
    args = ap.parse_args(argv)
    {"races": cmd_races, "questions": cmd_questions}[args.cmd](args.years)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
