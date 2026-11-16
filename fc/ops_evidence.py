"""OPS-03 evidence gate for the hub-run tier.

    python -m fc.ops_evidence --season 2026-27-rolling --start 2026-11-24 --days 10 --need 7
    python -m fc.ops_evidence --check-bundle runs/<season>/<day>/<entry>/bundle.json

A season day counts only when the job's own success criteria held:

* every tier-S entry in ``model-metadata`` has a runner-signed ``bundle.json`` for the day with
  ``status == "ok"`` and ``parse_rate >= MIN_PARSE_RATE`` (the daily job's own alert threshold),
  bound to that entry (``model_id``) and to the day's round (``round_id``);
* both job records for the day (``job-commit.json``, ``job-reveal.json``) report ``ok`` with no
  failures (schema ``fc-job/v1``, written by ``scripts/run_day.sh``);
* no intervention is logged for the day in ``docs/ops/interventions.md``.

The verdict is PENDING until the last window day's reveal time plus ``EVIDENCE_MARGIN_MIN``.
Exit codes: 0 PASS, 1 FAIL, 2 usage error, 3 PENDING. ``--check-bundle`` exits 0 or 1.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from . import commit as C
from .run import RUN_DOMAIN_SEP

MIN_PARSE_RATE = 0.80
EVIDENCE_MARGIN_MIN = 30
JOB_SCHEMA = "fc-job/v1"
DEFAULT_REVEAL_UTC = "18:00"
DEFAULT_RUNNER_PUB = "hub-config/keys/runner.pub.pem"
DEFAULT_INTERVENTIONS = "docs/ops/interventions.md"
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEAD_LINE = "SCOPE.md: fewer than 7 of 10 — the hub-run tier is declared dead"


# --------------------------------------------------------------------------- inputs

def _valid_day(text: str) -> bool:
    if not DAY_RE.match(text):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def load_interventions(path: Path) -> set[str]:
    """Days named in the 'Season day affected' column of the intervention table."""
    path = Path(path)
    if not path.is_file():
        return set()
    days: set[str] = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3 or not _valid_day(cells[0]):
            continue
        days.update(d for d in (p.strip() for p in cells[2].split(",")) if _valid_day(d))
    return days


def tier_s_entries(root: Path) -> list[str]:
    out = []
    for p in sorted((Path(root) / "model-metadata").glob("*.yml")):
        doc = yaml.safe_load(p.read_text()) or {}
        if isinstance(doc, dict) and doc.get("model_id") and doc.get("tier") == "S":
            out.append(str(doc["model_id"]))
    return out


def _load_season(root: Path, season: str) -> dict[str, Any]:
    p = Path(root) / "rounds" / season / "season.json"
    try:
        doc = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


# --------------------------------------------------------------------------- checks

def bundle_ok(bundle: Any, runner_pk, min_parse_rate: float = MIN_PARSE_RATE,
              identity_hash: str | None = None) -> tuple[bool, str]:
    """A run bundle counts only if the runner signed it and the run itself was good."""
    if not isinstance(bundle, dict):
        return False, "bundle is not a JSON object"
    sig = bundle.get("runner_signature")
    if not isinstance(sig, str) or not sig:
        return False, "no runner_signature"
    payload = C.canonical_json({k: v for k, v in bundle.items() if k != "runner_signature"})
    if not C.verify_sig(runner_pk, RUN_DOMAIN_SEP + payload, sig):
        return False, "runner_signature does not verify"
    if bundle.get("status") != "ok":
        return False, f"status is {bundle.get('status')!r}, not 'ok'"
    pr = bundle.get("parse_rate")
    if isinstance(pr, bool) or not isinstance(pr, (int, float)) or math.isnan(pr):
        return False, f"parse_rate is not numeric ({pr!r})"
    if pr < min_parse_rate:
        return False, f"parse_rate {pr} below {min_parse_rate}"
    if identity_hash is not None and bundle.get("identity_hash") != identity_hash:
        return False, (f"identity_hash {bundle.get('identity_hash')!r} "
                       f"does not match {identity_hash!r}")
    return True, "ok"


def job_record_ok(path: Path, season: str, day: str, mode: str) -> tuple[bool, str]:
    path = Path(path)
    try:
        rec = json.loads(path.read_text())
    except FileNotFoundError:
        return False, "missing"
    except (OSError, ValueError) as e:
        return False, f"unreadable ({type(e).__name__})"
    if not isinstance(rec, dict):
        return False, "not a JSON object"
    if rec.get("schema") != JOB_SCHEMA:
        return False, f"schema is {rec.get('schema')!r}, not {JOB_SCHEMA!r}"
    for key, want in (("season", season), ("day", day), ("mode", mode)):
        if rec.get(key) != want:
            return False, f"{key} is {rec.get(key)!r}, expected {want!r}"
    if rec.get("ok") is not True:
        return False, f"job reports ok={rec.get('ok')!r}"
    failures = rec.get("failures")
    if failures != []:
        return False, f"job recorded failures {failures!r}"
    return True, "ok"


def _entry_check(root: Path, season: str, day: str, entry: str, runner_pk,
                 min_parse_rate: float, round_id: str | None) -> tuple[bool, str]:
    p = root / "runs" / season / day / entry / "bundle.json"
    try:
        bundle = json.loads(p.read_text())
    except FileNotFoundError:
        return False, f"{entry}: no bundle"
    except (OSError, ValueError):
        return False, f"{entry}: bundle unreadable"
    ok, why = bundle_ok(bundle, runner_pk, min_parse_rate)
    if not ok:
        return False, f"{entry}: {why}"
    if bundle.get("model_id") != entry:
        return False, f"{entry}: bundle model_id is {bundle.get('model_id')!r}"
    if round_id is None:
        return False, f"{entry}: no round manifest for {day}"
    if bundle.get("round_id") != round_id:
        return False, f"{entry}: bundle round_id {bundle.get('round_id')!r} is not {round_id!r}"
    return True, "ok"


def _round_id(root: Path, season: str, day: str) -> str | None:
    try:
        rid = json.loads((root / "rounds" / season / day / "manifest.json").read_text())
        rid = rid.get("round_id")
    except (OSError, ValueError, AttributeError):
        return None
    return rid if isinstance(rid, str) and rid else None


# --------------------------------------------------------------------------- time

def parse_now(text: str) -> datetime:
    """ISO-8601 timestamp, or a bare YYYY-MM-DD meaning 00:00:00Z. Naive means UTC."""
    text = text.strip()
    if _valid_day(text):
        return datetime.fromisoformat(text).replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as e:
        raise ValueError(f"not an ISO-8601 time or YYYY-MM-DD: {text!r}") from e
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def window_cutoff(root: Path, season: str, last_day: str) -> datetime:
    """The verdict is final from the last day's reveal time + EVIDENCE_MARGIN_MIN."""
    reveal = ((_load_season(root, season).get("schedule") or {}).get("reveal_utc")
              or DEFAULT_REVEAL_UTC)
    try:
        hh, mm = (int(x) for x in str(reveal).split(":")[:2])
    except ValueError:
        hh, mm = (int(x) for x in DEFAULT_REVEAL_UTC.split(":"))
    d = date.fromisoformat(last_day)
    return (datetime(d.year, d.month, d.day, hh, mm, tzinfo=UTC)
            + timedelta(minutes=EVIDENCE_MARGIN_MIN))


# --------------------------------------------------------------------------- the gate

def evaluate(root: Path, season: str, start: str, days: int, need: int, runner_pub_pem: bytes,
             interventions: set[str], now: datetime,
             min_parse_rate: float = MIN_PARSE_RATE) -> dict[str, Any]:
    root = Path(root)
    runner_pk = C.load_public(runner_pub_pem)
    entries = tier_s_entries(root)
    d0 = date.fromisoformat(start)
    rows = []
    for i in range(days):
        day = (d0 + timedelta(days=i)).isoformat()
        problems: list[str] = []
        if not entries:
            problems.append("no tier S entries in model-metadata")
        rid = _round_id(root, season, day)
        for entry in entries:
            ok, why = _entry_check(root, season, day, entry, runner_pk, min_parse_rate, rid)
            if not ok:
                problems.append(why)
        for mode in ("commit", "reveal"):
            ok, why = job_record_ok(root / "runs" / season / day / f"job-{mode}.json",
                                    season, day, mode)
            if not ok:
                problems.append(f"job-{mode}.json: {why}")
        if day in interventions:
            problems.append("intervention logged for this day")
        rows.append({"day": day, "entries": list(entries), "ok": not problems,
                     "reason": "; ".join(problems) if problems else "ok"})
    ok_days = sum(1 for r in rows if r["ok"])
    cutoff = window_cutoff(root, season, rows[-1]["day"]) if rows else now
    if now < cutoff:
        verdict = "PENDING"
    else:
        verdict = "PASS" if ok_days >= need else "FAIL"
    return {"season": season, "start": start, "window": days, "need": need,
            "ok_days": ok_days, "verdict": verdict, "cutoff": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "now": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "days": rows}


def _resolve(root: Path, p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else root / q


def _check_bundle(args: argparse.Namespace, root: Path) -> int:
    try:
        pk = C.load_public(_resolve(root, args.runner_pub).read_bytes())
    except (OSError, ValueError, TypeError) as e:
        print(f"cannot read runner public key {args.runner_pub}: {e}", file=sys.stderr)
        return 2
    try:
        bundle = json.loads(Path(args.check_bundle).read_text())
    except (OSError, ValueError) as e:
        print(f"BUNDLE FAIL: cannot read {args.check_bundle}: {type(e).__name__}")
        return 1
    ok, why = bundle_ok(bundle, pk, args.min_parse_rate, args.identity_hash)
    if not ok:
        print(f"BUNDLE FAIL: {why}")
        return 1
    print(f"BUNDLE OK model_id={bundle.get('model_id')} parse_rate={bundle.get('parse_rate')} "
          f"image_id={bundle.get('image_id')} identity_hash={bundle.get('identity_hash')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m fc.ops_evidence",
        description="OPS-03 gate: N of M season days with signed ok bundles, clean job records "
                    "and no logged intervention; or verify one signed bundle (--check-bundle).")
    ap.add_argument("--season", default="2026-27-rolling")
    ap.add_argument("--start", help="first window day (default: the season's first_day)")
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--need", type=int, default=7)
    ap.add_argument("--root", default=".")
    ap.add_argument("--runner-pub", default=DEFAULT_RUNNER_PUB)
    ap.add_argument("--interventions", default=DEFAULT_INTERVENTIONS)
    ap.add_argument("--now", help="ISO-8601 time or YYYY-MM-DD (00:00Z); default: current UTC")
    ap.add_argument("--min-parse-rate", type=float, default=MIN_PARSE_RATE)
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    ap.add_argument("--check-bundle", metavar="FILE",
                    help="verify one signed bundle (signature, status ok, parse-rate floor) and exit")
    ap.add_argument("--identity-hash", help="with --check-bundle: required identity_hash")
    args = ap.parse_args(argv)
    root = Path(args.root)

    if args.check_bundle:
        return _check_bundle(args, root)

    try:
        pem = _resolve(root, args.runner_pub).read_bytes()
        C.load_public(pem)
    except (OSError, ValueError, TypeError) as e:
        print(f"cannot read runner public key {args.runner_pub}: {e}", file=sys.stderr)
        return 2
    start = args.start or _load_season(root, args.season).get("first_day")
    if not start or not _valid_day(str(start)):
        print("no --start given and the season has no valid first_day", file=sys.stderr)
        return 2
    if args.days < 1:
        print("--days must be at least 1", file=sys.stderr)
        return 2
    try:
        now = parse_now(args.now) if args.now else datetime.now(UTC)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    interventions = load_interventions(_resolve(root, args.interventions))
    res = evaluate(root, args.season, str(start), args.days, args.need, pem, interventions, now,
                   args.min_parse_rate)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"season {res['season']}  window {res['start']} + {res['window']} days  "
              f"now {res['now']}  verdict final from {res['cutoff']}")
        for r in res["days"]:
            print(f"  {r['day']}  {'PASS' if r['ok'] else 'FAIL'}  {r['reason']}")
        tally = f"{res['ok_days']}/{res['window']} (need {res['need']})"
        if res["verdict"] == "PENDING":
            print(f"VERDICT: PENDING {tally} — not final until {res['cutoff']}")
        else:
            print(f"VERDICT: {res['verdict']} {tally}")
            if res["verdict"] == "FAIL":
                print(DEAD_LINE)
    return {"PASS": 0, "FAIL": 1, "PENDING": 3}[res["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
