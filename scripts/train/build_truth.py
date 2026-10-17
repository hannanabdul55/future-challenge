"""Audited race outcomes for 2018, 2020, 2022 and 2024 (D-07).

    uv run python scripts/train/build_truth.py senate --medsl /mnt/d/fc-train/data/medsl/senate \
        --years 2018 2020 2022 2024

``senate`` loads the MEDSL Senate file, adds the contests it omits, applies the reasoned
overrides, prints the contest count per cycle and the override hits, and records the file's
sha256 in data/phase-04.1/truth_sources.json. Governors, presidential margins and the audit are
further subcommands.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from fc.train import paths, truth

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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("senate", help="load, correct and count MEDSL Senate contests")
    s.add_argument("--medsl", type=Path, required=True, help="the MEDSL file or its directory")
    s.add_argument("--years", type=int, nargs="+", required=True)
    s.set_defaults(fn=cmd_senate)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
