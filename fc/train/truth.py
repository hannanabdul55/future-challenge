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

from fc import packet as P

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
    """All rows as dicts. The delimiter is the most frequent of , tab ; | in the header line
    (the csv sniffer misreads quoting from a header-only sample); quoting is standard."""
    with path.open(newline="", encoding="utf-8") as f:
        head = f.readline()
        f.seek(0)
        delim = max(",\t;|", key=head.count)
        return list(csv.DictReader(f, delimiter=delim))


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
        if cur.office != "senate":
            caucus = ""                     # a Senate notion; governors carry none
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


# ------------------------------------------------------------------ Wikipedia results tables

# Wikipedia results are published either as {{Election box ...}} templates (almost every race
# page) or as a plain wikitable with Party, Candidate, Votes and % columns. Both are parsed
# here from fixed wikitext; the audit compares the outcome with a second, independent source.

_HEADING = re.compile(r"^(=+)\s*(.*?)\s*=+\s*$", re.MULTILINE)


@dataclass
class Box:
    path: str                       # enclosing headings, "General election > Results"
    title: str
    rows: list[dict]                # {"kind", "name", "party", "votes", "pct"}


def _strip(s: str) -> str:
    return P.strip_wiki(s or "")


def _top_templates(text: str) -> list[tuple[int, str]]:
    """Top-level ``{{...}}`` templates as (start offset, body without the outer braces)."""
    out: list[tuple[int, str]] = []
    depth = 0
    start = 0
    i = 0
    n = len(text)
    while i < n - 1:
        two = text[i:i + 2]
        if two == "{{":
            if depth == 0:
                start = i
            depth += 1
            i += 2
        elif two == "}}" and depth:
            depth -= 1
            i += 2
            if depth == 0:
                out.append((start, text[start + 2:i - 2]))
        else:
            i += 1
    return out


def _split_params(body: str) -> list[str]:
    """Split a template body on top-level ``|`` (not inside ``{{ }}`` or ``[[ ]]``)."""
    parts, buf, brace, link = [], [], 0, 0
    i = 0
    while i < len(body):
        two = body[i:i + 2]
        if two == "{{":
            brace += 1
            buf.append(two)
            i += 2
        elif two == "}}":
            brace -= 1
            buf.append(two)
            i += 2
        elif two == "[[":
            link += 1
            buf.append(two)
            i += 2
        elif two == "]]":
            link -= 1
            buf.append(two)
            i += 2
        elif body[i] == "|" and not brace and not link:
            parts.append("".join(buf))
            buf = []
            i += 1
        else:
            buf.append(body[i])
            i += 1
    parts.append("".join(buf))
    return parts


def _number(s: str) -> int | None:
    digits = re.sub(r"[^\d]", "", _strip(s).split("(")[0])
    return int(digits) if digits else None


def _percent(s: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", _strip(s))
    return float(m.group(1)) / 100 if m else None


_LIST_TEMPLATE = re.compile(r"^\s*\{\{\s*(?:ubl|unbulleted list|plainlist|ublist)\s*\|(.*)\}\}\s*$",
                            re.IGNORECASE | re.DOTALL)


def _clean_name(s: str) -> str:
    """Candidate name; for a ticket written as {{ubl|Governor|Running mate}} the first name."""
    m = _LIST_TEMPLATE.match(s or "")
    if m:
        s = _split_params(m.group(1))[0]
    s = _strip(s)
    s = re.sub(r"\((?:incumbent|inc\.?)\)", "", s, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", s).strip(" *")


def parse_election_boxes(wikitext: str) -> list[Box]:
    """Every ``{{Election box begin}} ... {{Election box end}}`` block with its heading path."""
    heads = [(m.start(), len(m.group(1)), m.group(2)) for m in _HEADING.finditer(wikitext)]

    def path_at(pos: int) -> str:
        stack: list[tuple[int, str]] = []
        for p, level, title in heads:
            if p > pos:
                break
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, _strip(title)))
        return " > ".join(t for _, t in stack)

    boxes: list[Box] = []
    cur: Box | None = None
    for pos, body in _top_templates(wikitext):
        params = _split_params(body)
        name = params[0].strip().lower()
        if not name.startswith("election box"):
            continue
        kv: dict[str, str] = {}
        for p in params[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                kv[k.strip().lower()] = v
        if name.startswith("election box begin"):
            cur = Box(path_at(pos), _strip(kv.get("title", "")), [])
            boxes.append(cur)
        elif name.startswith("election box end"):
            cur = None
        elif cur is not None:
            if name.startswith("election box total"):
                kind = "total"
            elif "write-in" in name or "write in" in name:
                kind = "writein"
            elif "candidate" in name:
                kind = "winning" if "winning" in name else "candidate"
            else:
                continue                           # hold, gain, majority, turnout ...
            cur.rows.append({"kind": kind, "name": _clean_name(kv.get("candidate", "")),
                             "party": _strip(kv.get("party", "")),
                             "votes": _number(kv["votes"]) if "votes" in kv else None,
                             "pct": _percent(kv["percentage"]) if "percentage" in kv else None})
    return boxes


_INFOBOX = re.compile(r"^\|\s*(nominee|candidate|party)(\d+)\s*=\s*(.*)$", re.MULTILINE | re.IGNORECASE)


def _name_key(name: str) -> str:
    toks = re.findall(r"[a-z]+", name.lower())
    toks = [t for t in toks if t not in ("jr", "sr", "ii", "iii", "iv")]
    return f"{toks[0]} {toks[-1]}" if toks else ""


def infobox_parties(wikitext: str) -> dict[str, str]:
    """``{first-and-last-name key: party label}`` from the page's election infobox
    (nominee1/party1, nominee2/party2 ...). Used only for results templates that carry no
    party ("... no party ..." variants)."""
    names: dict[str, str] = {}
    labels: dict[str, str] = {}
    for kind, idx, val in _INFOBOX.findall(wikitext):
        (labels if kind.lower() == "party" else names)[idx] = _strip(val)
    return {_name_key(n): labels[i] for i, n in names.items() if i in labels and n}


def _label_party(label: str) -> str:
    s = (label or "").lower()
    if ("democrat" in s or s.strip() == "dfl") and "independent" not in s:
        return "D"
    if "republican" in s:
        return "R"
    return "Other"


def _tally(rows: list[dict]) -> dict | None:
    """Winner of one results block: the top vote-getter once a candidate's party lines are
    summed (a {{Election box ... Total}} line, party "Total", replaces its own party lines)."""
    cands: dict[str, dict] = {}
    for r in rows:
        if r["kind"] not in ("candidate", "winning") or not r["name"]:
            continue
        c = cands.setdefault(r["name"], {"lines": [], "total": None, "flag": False})
        c["flag"] = c["flag"] or r["kind"] == "winning"
        if r["party"].strip().lower() == "total":
            c["total"] = r
        else:
            c["lines"].append(r)
    if not cands:
        return None
    total_votes = None
    for c in cands.values():
        known = [r["votes"] for r in c["lines"] if r["votes"] is not None]
        c["votes"] = (c["total"]["votes"] if c["total"] and c["total"]["votes"] is not None
                      else sum(known) if known else None)
        lines = sorted(c["lines"], key=lambda r: -(r["votes"] or 0))
        c["party"] = _label_party(lines[0]["party"]) if lines else "Other"
    if all(c["votes"] is not None for c in cands.values()):
        winner = max(cands.items(), key=lambda kv: kv[1]["votes"])
        total_votes = sum(c["votes"] for c in cands.values())
    else:                                           # unopposed or unreported: use the flag
        flagged = [kv for kv in cands.items() if kv[1]["flag"]]
        if len(flagged) != 1:
            return None
        winner = flagged[0]

    def share(party: str) -> float | None:
        best = [c for c in cands.values() if c["party"] == party]
        if not best:
            return None
        top = max(best, key=lambda c: c["votes"] or 0)
        if total_votes and top["votes"] is not None:
            return top["votes"] / total_votes
        return None

    return {"party": winner[1]["party"], "name": winner[0], "votes": winner[1]["votes"],
            "d_pct": share("D"), "r_pct": share("R"), "flagged": [n for n, c in cands.items()
                                                                  if c["flag"]]}


def _table_winner(tables: list[dict]) -> tuple[str, dict] | None:
    """The same tally from a plain wikitable with Party, Candidate and Votes columns."""
    for t in tables:
        rows = t["rows"]
        for h, head in enumerate(rows):
            low = [c.lower() for c in head]
            if "candidate" in low and any(c.startswith("votes") for c in low):
                ci = low.index("candidate")
                vi = next(i for i, c in enumerate(low) if c.startswith("votes"))
                pi = low.index("party") if "party" in low else None
                body = []
                for r in rows[h + 1:]:
                    # a row may lose leading cells (rowspan/party shading); align from the right
                    shift = len(head) - len(r)
                    if shift < 0 or ci - shift < 0 or vi - shift < 0:
                        continue
                    name = r[ci - shift]
                    if not name or name.lower().startswith(("total", "turnout", "majority")):
                        continue
                    party = r[pi - shift] if pi is not None and pi - shift >= 0 else ""
                    body.append({"kind": "candidate", "name": name, "party": party,
                                 "votes": _number(r[vi - shift]), "pct": None})
                res = _tally(body)
                if res:
                    return t["path"], res
    return None


def results_table_winner(wikitext: str, *, contest: str = "regular") -> dict | None:
    """Top vote-getter's party and the D and R vote shares from a race page's general-election
    results. ``contest`` is "regular" or "special": on a page that holds both (the combined
    "{Y} United States Senate elections in {State}" article) it picks the block under a
    heading that mentions "special", or the one that does not. A runoff block replaces the
    first round. Returns None when no results block is found."""
    def okay(path: str) -> bool:
        low = path.lower()
        if any(w in low for w in ("primary", "convention", "caucus", "poll")):
            return False
        return not ("special" in low and contest != "special")

    blocks: list[tuple[str, dict]] = []
    info = infobox_parties(wikitext)
    for b in parse_election_boxes(wikitext):
        # a Louisiana-style "blanket primary" is the general election: trust the heading path
        if okay(b.path) and ("primary" not in b.title.lower() or "general" in b.path.lower()):
            for r in b.rows:
                if not r["party"] and r["name"]:
                    r["party"] = info.get(_name_key(r["name"]), "")
            res = _tally(b.rows)
            if res:
                blocks.append((b.path, res))
    if not blocks:
        hit = _table_winner([t for t in P.parse_wikitables(wikitext) if okay(t["path"])])
        if hit:
            blocks.append(hit)
    if contest == "special":
        sp = [b for b in blocks if "special" in b[0].lower()]
        blocks = sp or blocks
    runoff = [b for b in blocks if "runoff" in b[0].lower() or "run-off" in b[0].lower()]
    pick = runoff[-1] if runoff else (blocks[-1] if blocks else None)
    if pick is None:
        return None
    path, res = pick
    return {**res, "section": path}


# ------------------------------------------------------------------ governors: summary table

_NAME_TO_PO = {v: k for k, v in P.STATE_NAMES.items()}
_GAIN_HOLD = re.compile(r"([A-Z][\w\u2013-]*) (?:gain|hold)\b")


_SHADING = re.compile(r"\{\{party shading/(?:Text/)?([A-Za-z]+)\}\}", re.IGNORECASE)
_PARTY_CELL = re.compile(r"(Democratic|DFL|Republican|Independent|Libertarian|Green|Progressive"
                         r"|Nonpartisan|Forward|Conservative)", re.IGNORECASE)


def governor_truth(wikitext: str) -> dict[str, dict]:
    """Winner party per state from the "{Y} United States gubernatorial elections" race summary
    table (source A for governors): ``{postal: {"party", "result"}}``.

    The Result column says "<Party> gain/hold" when the seat was open or changed hands, and
    "Incumbent re-elected" when the incumbent won, in which case the winner is the incumbent's
    party (the Party column; for 2020 the party sits in a shading template, read from there).
    A row with neither reading maps to ``{"party": None}`` so the audit flags it."""
    out: dict[str, dict] = {}
    for t in P.parse_wikitables(_SHADING.sub(r"\1", wikitext)):
        head = t["rows"][0] if t["rows"] else []
        if (not head or head[0].lower() not in ("state", "states")
                or "territor" in t["path"].lower()
                or not any(c.lower().startswith("first") for c in head)):
            continue
        for row in t["rows"][1:]:
            po = _NAME_TO_PO.get(row[0]) if row else None
            if po is None or po in out:
                continue
            text = " ".join(row[1:6])
            m = _GAIN_HOLD.search(text)
            if m:
                party = _label_party(m.group(1))
            elif re.search(r"re-elected|elected to full term|elected\.", text) and len(row) > 2 \
                    and _PARTY_CELL.match(row[2]):
                party = _label_party(row[2])
            else:
                party = None
            out[po] = {"party": party, "result": re.sub(r"\s+", " ", text).strip()[-70:]}
    return out


# ------------------------------------------------------------------ the two-source audit

AUDIT_COLUMNS = ["year", "office", "state", "special", "source_a", "source_b", "final", "note",
                 "caucus", "winner_name", "seat_key", "d_pct", "r_pct"]


def audit_rows(contests: Iterable[Contest], a: dict, b: dict,
               overrides_csv: str | Path | None = None) -> list[dict]:
    """One audit row per contest. ``contests`` are the final (overridden) contests, ``a`` maps
    (year, office, state, special) to source A's party (None when unreadable), ``b`` maps the
    same key to a ``results_table_winner`` result. A contest whose two sources disagree, or
    where one is missing, is final only through an override row; otherwise its row has an
    empty ``final`` and an UNRESOLVED note, which the ``audit`` command refuses to accept."""
    reasons: dict[tuple[int, str, str, bool], str] = {}
    if overrides_csv is not None:
        for r in _csv_rows(overrides_csv, OVERRIDE_COLUMNS):
            reasons[_rkey(r)] = r["reason"].strip()
    rows: list[dict] = []
    for c in contests:
        k = _ckey(c)
        sa = a.get(k) or "?"
        res = b.get(k)
        sb = res["party"] if res else "?"
        if k in reasons:
            final, note = c.winner_party, f"override: {reasons[k]}"
        elif sa == sb and sa != "?":
            final, note = sa, ""
        else:
            final = ""
            note = (f"UNRESOLVED: source A {sa}, source B {sb}; needs an override row with the "
                    "certified result")
        d = res["d_pct"] if res and res.get("d_pct") is not None else c.d_pct
        r = res["r_pct"] if res and res.get("r_pct") is not None else c.r_pct
        rows.append({
            "year": c.year, "office": c.office, "state": c.state,
            "special": "true" if c.special else "false", "source_a": sa, "source_b": sb,
            "final": final, "note": note, "caucus": c.caucus, "winner_name": c.winner_name,
            "seat_key": c.seat_key,
            "d_pct": None if d is None else round(d, 4),
            "r_pct": None if r is None else round(r, 4)})
    return sorted(rows, key=lambda x: (x["year"], x["office"], x["state"], x["special"]))


# ------------------------------------------------------------------ presidential margins

def pres_margins(path: str | Path, years: Iterable[int]) -> list[dict]:
    """Statewide presidential result per state and year from the MEDSL president file: the
    Democratic and Republican candidates' shares of the statewide total vote (fractions,
    Maine and Nebraska statewide, not by district), margin = d_pct - r_pct, and the winner.
    The District of Columbia is not a state and is left out."""
    want = {int(y) for y in years}
    groups: dict[tuple[int, str], list[dict[str, str]]] = {}
    for r in _rows(_source_file(Path(path))):
        if (r.get("office") or "").strip().upper() != "US PRESIDENT" or int(r["year"]) not in want:
            continue
        po = r["state_po"].strip().upper()
        if po == "DC":
            continue
        groups.setdefault((int(r["year"]), po), []).append(r)
    out: list[dict] = []
    for (year, po), rows in sorted(groups.items()):
        total = max(_num(r["totalvotes"]) for r in rows)
        cands: dict[str, dict] = {}
        for i, r in enumerate(rows):
            name = re.sub(r"\s+", " ", r["candidate"]).strip().upper() or f"<blank {i}>"
            c = cands.setdefault(name, {"votes": 0.0, "top": -1.0, "party": "Other"})
            v = _num(r["candidatevotes"])
            c["votes"] += v
            if v > c["top"]:
                c["top"], c["party"] = v, _party(r["party_simplified"])
        best = {p: max((c["votes"] for c in cands.values() if c["party"] == p), default=0.0)
                for p in ("D", "R")}
        winner = max(cands.values(), key=lambda c: c["votes"])["party"]
        d, r_ = round(best["D"] / total, 6), round(best["R"] / total, 6)
        out.append({"year": year, "state": po, "d_pct": d, "r_pct": r_,
                    "margin": round(d - r_, 6), "winner": winner, "source": "medsl"})
    return out
