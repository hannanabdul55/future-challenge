"""Historical race truth for 2018, 2020, 2022 and 2024 (D-07).

Senate results come from the MIT Election Data and Science Lab "U.S. Senate statewide
1976-2024" file (CC0, doi:10.7910/DVN/PEJ5QU). The file has known traps (RESEARCH, "Traps found
in the MEDSL Senate file"); each is corrected by a reasoned row in ``data/phase-04.1/overrides.csv``
rather than by code, so every correction is visible and reviewable. A contest the file omits
altogether (CT 2022) is added through ``data/phase-04.1/missing_contests.csv``.

Everything read here is untrusted data: stdlib ``csv`` only, size-capped, never evaluated.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

MAX_BYTES = 20_000_000          # the real Senate file is about 530 KB
PARTIES = ("D", "R", "Other")

# The regular Senate class that is up in each cycle.
CYCLE_CLASS = {2018: "class1", 2020: "class2", 2022: "class3", 2024: "class1"}

# Class of each special-election seat. MEDSL does not carry it. A special whose seat is also
# up in the regular election of the same state and year (CA 2022, CA 2024) shares that seat.
SPECIAL_CLASS = {
    (2018, "MN"): "class2",   # Franken seat, Smith
    (2018, "MS"): "class2",   # Cochran seat, Hyde-Smith
    (2020, "AZ"): "class3",   # McCain seat, Kelly
    (2020, "GA"): "class3",   # Isakson seat, Warnock
    (2022, "CA"): "class3",   # Harris seat, same seat as the regular contest
    (2022, "OK"): "class2",   # Inhofe seat, Mullin
    (2024, "CA"): "class1",   # Feinstein seat, same seat as the regular contest
    (2024, "NE"): "class2",   # Ricketts seat, a different seat from Fischer's
}


@dataclass(frozen=True)
class Contest:
    year: int
    office: str              # "senate" | "governor"
    state: str               # postal code
    special: bool
    seat_key: str            # "<state>-<class>"; same-seat contests share it
    winner_party: str        # "D" | "R" | "Other"
    winner_name: str
    caucus: str              # Senate caucus of the winner: "D" | "R" | "" (none or not applicable)
    d_pct: float | None      # top D candidate's share of the vote, 0..1
    r_pct: float | None
    source: str              # "medsl" | "medsl+override" | "addition" | ...


def normalise_stage(stage: str) -> str:
    """``gen``/``GEN`` -> "gen"; ``GEN RUNOFF`` (and ``runoff``) -> "runoff"; else the lowered text."""
    s = re.sub(r"\s+", " ", (stage or "").strip().lower())
    return "runoff" if "runoff" in s else s


def seat_key(year: int, state: str, special: bool) -> str:
    if special:
        try:
            return f"{state}-{SPECIAL_CLASS[(year, state)]}"
        except KeyError:
            raise ValueError(f"no seat class recorded for the {year} {state} special") from None
    try:
        return f"{state}-{CYCLE_CLASS[year]}"
    except KeyError:
        raise ValueError(f"no Senate class recorded for {year}") from None


# ------------------------------------------------------------------ MEDSL loader

def _party(simplified: str) -> str:
    s = (simplified or "").strip().upper()
    return "D" if s == "DEMOCRAT" else "R" if s == "REPUBLICAN" else "Other"


def _share_party(simplified: str, detailed: str) -> str:
    """Party used only for the vote shares: ``party_detailed`` rescues the rows whose
    ``party_simplified`` is wrong (IL and MD 2022 say OTHER for DEMOCRATIC)."""
    p = _party(simplified)
    if p != "Other":
        return p
    d = (detailed or "").strip().upper()
    return "D" if d.startswith("DEMOCRAT") else "R" if d == "REPUBLICAN" else "Other"


def _source_file(path: Path) -> Path:
    path = Path(path)
    if path.is_dir():
        files = sorted(p for p in path.iterdir() if p.suffix.lower() in (".csv", ".tab"))
        if len(files) != 1:
            raise ValueError(f"expected exactly one .csv/.tab file in {path}, found {len(files)}")
        path = files[0]
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"{path} is larger than {MAX_BYTES} bytes; refusing to parse it")
    return path


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        head = f.readline()
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(head, delimiters=",\t;|")
        except csv.Error:
            dialect = csv.excel
        return list(csv.DictReader(f, dialect=dialect))


def _num(s: str) -> float:
    return float(s) if s not in ("", None) else 0.0


def load_medsl_senate(path: str | Path, years: Iterable[int]) -> list[Contest]:
    """General-election Senate contests of ``years`` from the MEDSL file (or the directory that
    holds it), sorted by (year, state, special).

    A runoff round replaces the first round of the same contest. Candidates listed on several
    party lines (fusion voting) are summed before the top vote-getter is chosen. The winner's
    party is MEDSL's ``party_simplified`` as is; wrong labels are the overrides' job."""
    want = {int(y) for y in years}
    groups: dict[tuple[int, str, bool], dict[str, list[dict[str, str]]]] = {}
    for r in _rows(_source_file(Path(path))):
        if (r.get("office") or "").strip().upper() != "US SENATE":
            continue
        if (r.get("mode") or "total").strip().lower() != "total":
            continue
        year = int(r["year"])
        stage = normalise_stage(r["stage"])
        if year not in want or stage not in ("gen", "runoff"):
            continue
        special = (r.get("special") or "").strip().lower() == "true"
        groups.setdefault((year, r["state_po"].strip().upper(), special), {}).setdefault(
            stage, []).append(r)

    out: list[Contest] = []
    for (year, state, special), by_stage in sorted(groups.items()):
        rows = by_stage.get("runoff") or by_stage["gen"]
        total = max(_num(r["totalvotes"]) for r in rows)
        cands: dict[str, dict] = {}
        for i, r in enumerate(rows):
            name = re.sub(r"\s+", " ", r["candidate"]).strip().upper() or f"<blank {i}>"
            c = cands.setdefault(name, {"votes": 0.0, "top": -1.0, "row": r, "label": r["candidate"]})
            v = _num(r["candidatevotes"])
            c["votes"] += v
            if v > c["top"]:                 # the candidate's party is that of their largest line
                c["top"], c["row"] = v, r
        winner = max(cands.values(), key=lambda c: c["votes"])
        wparty = _party(winner["row"]["party_simplified"])
        shares: dict[str, float] = {}
        for c in cands.values():
            p = _share_party(c["row"]["party_simplified"], c["row"]["party_detailed"])
            shares[p] = max(shares.get(p, 0.0), c["votes"])
        out.append(Contest(
            year=year, office="senate", state=state, special=special,
            seat_key=seat_key(year, state, special), winner_party=wparty,
            winner_name=re.sub(r"\s+", " ", winner["label"]).strip(),
            caucus=wparty if wparty != "Other" else "",
            d_pct=shares["D"] / total if "D" in shares and total else None,
            r_pct=shares["R"] / total if "R" in shares and total else None,
            source="medsl"))
    return out


# ------------------------------------------------------------------ overrides and additions

OVERRIDE_COLUMNS = ["year", "office", "state", "special", "winner_party", "caucus", "reason"]
MISSING_COLUMNS = ["year", "office", "state", "special", "winner_party", "winner_name",
                   "caucus", "reason"]


def _csv_rows(path: str | Path, columns: list[str]) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        if rd.fieldnames != columns:
            raise ValueError(f"{path}: header must be {','.join(columns)}, got {rd.fieldnames}")
        return list(rd)


def _ckey(c: Contest) -> tuple[int, str, str, bool]:
    return (c.year, c.office, c.state, c.special)


def _rkey(r: dict[str, str]) -> tuple[int, str, str, bool]:
    return (int(r["year"]), r["office"].strip(), r["state"].strip().upper(),
            r["special"].strip().lower() == "true")


def apply_overrides(contests: Iterable[Contest], overrides_csv: str | Path,
                    offices: Iterable[str] | None = None) -> list[Contest]:
    """Apply the reasoned override rows. An override always wins (certified results,
    RESEARCH trap 6). A row with an empty reason, or matching no contest, raises ValueError.
    ``offices`` restricts which rows are applied (rows of other offices are ignored)."""
    contests = list(contests)
    by_key = {_ckey(c): i for i, c in enumerate(contests)}
    only = set(offices) if offices is not None else None
    seen: set[tuple[int, str, str, bool]] = set()
    out = list(contests)
    for n, r in enumerate(_csv_rows(overrides_csv, OVERRIDE_COLUMNS), start=2):
        if only is not None and r["office"].strip() not in only:
            continue
        if not r["reason"].strip():
            raise ValueError(f"{overrides_csv} line {n}: an override needs a non-empty reason")
        k = _rkey(r)
        if k not in by_key:
            raise ValueError(f"{overrides_csv} line {n}: override {k} matches no contest")
        if k in seen:
            raise ValueError(f"{overrides_csv} line {n}: second override for {k}")
        seen.add(k)
        party = r["winner_party"].strip()
        if party not in PARTIES:
            raise ValueError(f"{overrides_csv} line {n}: winner_party {party!r} not in {PARTIES}")
        cur = out[by_key[k]]
        caucus = r["caucus"].strip() or (party if party != "Other" else cur.caucus)
        if caucus not in ("D", "R", ""):
            raise ValueError(f"{overrides_csv} line {n}: caucus {caucus!r} not in D, R or blank")
        out[by_key[k]] = replace(cur, winner_party=party, caucus=caucus,
                                 source=cur.source.split("+override")[0] + "+override")
    return out


def add_missing(contests: Iterable[Contest], missing_csv: str | Path) -> list[Contest]:
    """Add contests the source file omits. A row for a contest already present raises
    ValueError, so an addition can never silently replace loaded data."""
    out = list(contests)
    have = {_ckey(c) for c in out}
    for n, r in enumerate(_csv_rows(missing_csv, MISSING_COLUMNS), start=2):
        if not r["reason"].strip():
            raise ValueError(f"{missing_csv} line {n}: an addition needs a non-empty reason")
        k = _rkey(r)
        if k in have:
            raise ValueError(f"{missing_csv} line {n}: contest {k} is already in the data")
        party = r["winner_party"].strip()
        if party not in PARTIES:
            raise ValueError(f"{missing_csv} line {n}: winner_party {party!r} not in {PARTIES}")
        year, office, state, special = k
        caucus = r["caucus"].strip() or (party if party != "Other" else "")
        key = seat_key(year, state, special) if office == "senate" else f"{state}-gov"
        out.append(Contest(year=year, office=office, state=state, special=special, seat_key=key,
                           winner_party=party, winner_name=r["winner_name"].strip(),
                           caucus=caucus, d_pct=None, r_pct=None, source="addition"))
        have.add(k)
    return sorted(out, key=lambda c: (c.year, c.office, c.state, c.special))


def senate_truth(medsl: str | Path, years: Iterable[int], *, overrides_csv: str | Path,
                 missing_csv: str | Path) -> list[Contest]:
    """MEDSL Senate contests with the omitted contests added and the overrides applied."""
    contests = add_missing(load_medsl_senate(medsl, years), missing_csv)
    return apply_overrides(contests, overrides_csv, offices=("senate",))
