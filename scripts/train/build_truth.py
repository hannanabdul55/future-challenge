"""Audited race outcomes for 2018, 2020, 2022 and 2024 (D-07).

    uv run python scripts/train/build_truth.py senate --medsl /mnt/d/fc-train/data/medsl/senate \
        --years 2018 2020 2022 2024

``senate`` loads the MEDSL Senate file, adds the contests it omits, applies the reasoned
overrides, prints the contest count per cycle and the override hits, and records the file's
sha256 in data/phase-04.1/truth_sources.json.

    governor --years 2018 2020 2022 2024    governor winners from Wikipedia, two readings
    pres --years 2016 2020                  statewide presidential margins (MEDSL president file)
    audit                                   every Senate and governor contest, both sources

Wikipedia pages are read at the revision current at ASOF (a fixed instant, so reruns are
reproducible) through a disk cache; every revision id used is written to truth_sources.json.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from fc import packet as P
from fc.train import history, paths, truth, wiki_asof

SOURCES = paths.REPO_DATA / "truth_sources.json"
OVERRIDES = paths.REPO_DATA / "overrides.csv"
MISSING = paths.REPO_DATA / "missing_contests.csv"

MEDSL_SENATE = {
    "name": "MIT Election Data and Science Lab, U.S. Senate statewide 1976-2024",
    "doi": "doi:10.7910/DVN/PEJ5QU",
    "url": "https://dataverse.harvard.edu/api/access/datafile/13887039?format=original",
    "licence": "CC0 1.0",
    "file": "1976-2024-senate-state.csv",
}


ASOF = "2026-10-17T00:00:00Z"
TRUTH_CACHE = paths.DATA / "wiki-truth"
MEDSL_PRESIDENT = {
    "name": "MIT Election Data and Science Lab, U.S. President 1976-2024",
    "doi": "doi:10.7910/DVN/42MVDX",
    "url": "https://dataverse.harvard.edu/api/access/datafile/13887042",
    "licence": "CC0 1.0",
    "file": "1976-2024-president.csv",
}
WIKI_LICENCE = "CC BY-SA 4.0; attribution by revision id (page title + revid, see revisions)"


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def load_sources() -> dict:
    return json.loads(SOURCES.read_text()) if SOURCES.is_file() else {}


def save_sources(doc: dict) -> None:
    SOURCES.write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_senate(args: argparse.Namespace) -> int:
    src = truth._source_file(args.medsl)
    raw = truth.load_medsl_senate(src, args.years)
    final = truth.senate_truth(src, args.years, overrides_csv=OVERRIDES, missing_csv=MISSING)
    changed = {(a.year, a.state, a.special): (a, b) for a, b in zip(
        sorted(truth.add_missing(raw, MISSING), key=lambda c: (c.year, c.state, c.special)),
        sorted(final, key=lambda c: (c.year, c.state, c.special)), strict=True)
        if (a.winner_party, a.caucus) != (b.winner_party, b.caucus)}
    added = [c for c in final if c.source == "addition"]
    per = Counter(c.year for c in final)
    seats = {y: len({c.seat_key for c in final if c.year == y}) for y in args.years}
    for y in args.years:
        print(f"{y}: {per[y]} Senate contests ({seats[y]} seats); medsl rows gave "
              f"{sum(c.year == y for c in raw)}")
    print(f"additions (absent from MEDSL): {[(c.year, c.state) for c in added]}")
    hits = sum(c.source.endswith("+override") for c in final)
    print(f"override hits: {hits} contests overridden, {len(changed)} changed a value")
    for (y, st, sp), (a, b) in sorted(changed.items()):
        print(f"  {y} {st}{' special' if sp else ''}: {a.winner_party}/{a.caucus or '-'} -> "
              f"{b.winner_party}/{b.caucus or '-'}")
    doc = load_sources()
    prev = doc.get("medsl_senate", {})
    doc["medsl_senate"] = {
        **MEDSL_SENATE, "bytes": src.stat().st_size, "sha256": sha256_of(src),
        "downloaded": prev.get("downloaded") or datetime.fromtimestamp(
            src.stat().st_mtime, UTC).strftime("%Y-%m-%d"),
        "local_dir": str(src.parent)}
    save_sources(doc)
    return 0


class Wiki:
    """Pages at ASOF through a disk cache; remembers every (title, revid) it served."""

    def __init__(self, root: Path = TRUTH_CACHE) -> None:
        self.cache = wiki_asof.WikiCache(root)
        self.revisions: dict[str, dict] = {}

    def page(self, title: str) -> str | None:
        try:
            rev = self.cache.rev_at(title, ASOF)
        except LookupError:
            return None
        self.revisions[title] = {"page": rev["title"], "revid": rev["revid"],
                                 "timestamp": rev["timestamp"],
                                 "redirected_from": rev["redirected_from"]}
        return self.cache.wikitext(rev["revid"])


def record_revisions(wiki: Wiki) -> None:
    doc = load_sources()
    block = doc.setdefault("wikipedia", {})
    block.update({"asof": ASOF, "licence": WIKI_LICENCE, "api": P.WIKI_API})
    block.setdefault("revisions", {}).update(wiki.revisions)
    save_sources(doc)


def gather_governors(years: list[int], wiki: Wiki) -> tuple[list[truth.Contest], dict, dict]:
    """Governor contests (winner = source A, the summary table) with source A's party per
    key and source B's result per key (the per-race page's results block)."""
    contests: list[truth.Contest] = []
    a: dict = {}
    b: dict = {}
    for y in years:
        text = wiki.page(f"{y} United States gubernatorial elections")
        if text is None:
            fail(f"summary page for {y} not found")
        for st, row in sorted(truth.governor_truth(text).items()):
            key = (y, "governor", st, False)
            page = wiki.page(history.governor_title(y, P.STATE_NAMES[st]))
            res = truth.results_table_winner(page) if page else None
            a[key] = row["party"]
            if res:
                b[key] = res
            contests.append(truth.Contest(
                y, "governor", st, False, f"{st}-gov", row["party"] or "?",
                res["name"] if res else "", "",
                res.get("d_pct") if res else None, res.get("r_pct") if res else None,
                "wikipedia-summary"))
    return contests, a, b


def gather_senate(years: list[int], medsl: Path, wiki: Wiki) -> tuple[list[truth.Contest], dict, dict]:
    raw = truth.add_missing(truth.load_medsl_senate(medsl, years), MISSING)
    final = truth.apply_overrides(raw, OVERRIDES, offices=("senate",))
    a = {(c.year, "senate", c.state, c.special): c.winner_party for c in raw}
    b: dict = {}
    for c in final:
        page = wiki.page(history.senate_title(c.year, P.STATE_NAMES[c.state], special=c.special))
        res = truth.results_table_winner(
            page, contest="special" if c.special else "regular") if page else None
        if res:
            b[(c.year, "senate", c.state, c.special)] = res
    return final, a, b


def cmd_governor(args: argparse.Namespace) -> int:
    wiki = Wiki()
    _, a, b = gather_governors(args.years, wiki)
    record_revisions(wiki)
    for y in args.years:
        keys = [k for k in a if k[0] == y]
        differ = [k[2] for k in keys if k not in b or b[k]["party"] != a[k]]
        print(f"{y}: {len(keys)} governor contests; sources differ or missing for {differ}")
    return 0


def cmd_pres(args: argparse.Namespace) -> int:
    src = truth._source_file(args.medsl)
    rows = truth.pres_margins(src, args.years)
    out = paths.REPO_DATA / "pres_margins.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["year", "state", "d_pct", "r_pct", "margin", "winner",
                                          "source"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    for y in args.years:
        yr = [r for r in rows if r["year"] == y]
        print(f"{y}: {len(yr)} states, D {sum(r['winner'] == 'D' for r in yr)}, "
              f"R {sum(r['winner'] == 'R' for r in yr)}")
    doc = load_sources()
    doc["medsl_president"] = {
        **MEDSL_PRESIDENT, "bytes": src.stat().st_size, "sha256": sha256_of(src),
        "downloaded": doc.get("medsl_president", {}).get("downloaded") or datetime.fromtimestamp(
            src.stat().st_mtime, UTC).strftime("%Y-%m-%d"),
        "local_dir": str(src.parent), "output": "data/phase-04.1/pres_margins.csv",
        "notes": "fractions of the statewide total vote; DC excluded; ME and NE statewide"}
    save_sources(doc)
    print(f"wrote {out} ({len(rows)} rows)")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    wiki = Wiki()
    years = args.years
    sen, sa, sb = gather_senate(years, args.medsl, wiki)
    gov, ga, gb = gather_governors(years, wiki)
    gov = truth.apply_overrides(gov, OVERRIDES, offices=("governor",))
    rows = truth.audit_rows(sen + gov, {**sa, **ga}, {**sb, **gb}, overrides_csv=OVERRIDES)
    record_revisions(wiki)
    out = paths.REPO_DATA / "truth_audit.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=truth.AUDIT_COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows({k: "" if v is None else v for k, v in r.items()} for r in rows)
    for y in years:
        for office in ("senate", "governor"):
            n = sum(r["year"] == y and r["office"] == office for r in rows)
            print(f"{y} {office}: {n} contests")
    open_ = [r for r in rows if not r["final"]]
    differ = [r for r in rows if r["source_a"] != r["source_b"]]
    print(f"audit rows {len(rows)}; sources differ in {len(differ)}; unresolved {len(open_)}")
    for r in open_:
        print(f"  UNRESOLVED {r['year']} {r['office']} {r['state']} special={r['special']}: "
              f"A={r['source_a']} B={r['source_b']}")
    if open_:
        fail("unresolved disagreements: add a reasoned override row (with the authority URL) "
             "to data/phase-04.1/overrides.csv for each")
    print(f"wrote {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("senate", help="load, correct and count MEDSL Senate contests")
    s.add_argument("--medsl", type=Path, required=True, help="the MEDSL file or its directory")
    s.add_argument("--years", type=int, nargs="+", required=True)
    s.set_defaults(fn=cmd_senate)
    g = sub.add_parser("governor", help="governor winners from Wikipedia (two readings)")
    g.add_argument("--years", type=int, nargs="+", required=True)
    g.set_defaults(fn=cmd_governor)
    pr = sub.add_parser("pres", help="statewide presidential margins")
    pr.add_argument("--years", type=int, nargs="+", required=True)
    pr.add_argument("--medsl", type=Path, default=paths.MEDSL / "president")
    pr.set_defaults(fn=cmd_pres)
    au = sub.add_parser("audit", help="two-source audit of every Senate and governor contest")
    au.add_argument("--years", type=int, nargs="+", default=[2018, 2020, 2022, 2024])
    au.add_argument("--medsl", type=Path, default=paths.MEDSL / "senate")
    au.set_defaults(fn=cmd_audit)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
