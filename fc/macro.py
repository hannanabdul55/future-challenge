"""Macro release template (QSPEC-04): templated, mechanically resolvable questions on scheduled
US macro data releases.

One template row per series (``questions/templates/macro/series.csv``) and one row per scheduled
release (``releases.csv``). For every release in a month the template emits

* a **quantile** question on the first-published value (9 symmetric levels, bounded), and
* a **binary** question "greater than T?" whose threshold T sits at the last print plus a
  seeded offset of ±1 or ±2 steps. The sign and size are drawn from a hash of
  (season, series, period), so the surprise direction varies across questions and a model
  cannot win them all with one standing bias (see docs/macro-template.md for why).

The FOMC row is a single categorical question per meeting.

Resolution is the *first print* as published by the agency (BLS v1 API, key-free; FRED CSV as
fallback). The resolver and the dry-run on history are the same code path, so the checklist's
"ran mechanically on ≥ 12 months of history" is a command, not a promise. Everything that
reaches the network goes through the injected ``http`` callable so tests run offline.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import random
import statistics
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from . import questions as Q
from .packet import Http, Section

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "questions" / "templates" / "macro"
SERIES_CSV = TEMPLATE_DIR / "series.csv"
RELEASES_CSV = TEMPLATE_DIR / "releases.csv"

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
BLS_V1 = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
FOMC_CATEGORIES = ["cut_50bp_or_more", "cut_25bp", "hold", "hike"]
FOMC_BASELINE = {"cut_50bp_or_more": 0.03, "cut_25bp": 0.27, "hold": 0.62, "hike": 0.08}
FAMILY = "macro"
RESOLVER_ID = "fc.macro"
VOID_AFTER_DAYS = 14       # rules.md "void-after-14-days"; tests/test_rules_consistency.py binds it
VOID_IF = [
    f"release delayed more than {VOID_AFTER_DAYS} days past expected_resolution_date",
    "series discontinued, rebased or redefined before the release",
    "agency announces a methodology change affecting this period before the release",
]
HISTORY_WINDOW = 36        # periods used for the baseline spread and the binary base rate
PACKET_ROWS = 24           # transformed observations per series shown in the packet
T_FINAL_DAYS = 21          # first print is final; T_final leaves room for a delayed release


# --------------------------------------------------------------------------- template rows

@dataclass(frozen=True)
class Series:
    series_id: str
    label: str
    agency: str
    source_url: str
    fred_id: str
    bls_id: str
    unit: str
    transform: str          # yoy | mom | diff | level | month_end | fomc
    frequency: str          # monthly | quarterly | meeting
    decimals: int
    lo: float
    hi: float
    integer: bool
    step: float
    release: str
    headline: bool
    headline_label: str

    @classmethod
    def from_row(cls, r: dict[str, str]) -> Series:
        return cls(r["series_id"], r["label"], r["agency"], r["source_url"], r["fred_id"],
                   r.get("bls_id", ""), r["unit"], r["transform"], r["frequency"],
                   int(r["decimals"]), float(r["lo"]), float(r["hi"]),
                   r["integer"].strip().lower() == "true", float(r["step"]), r["release"],
                   r.get("headline", "").strip().lower() == "true", r.get("headline_label", ""))


def load_series(path: Path = SERIES_CSV) -> list[Series]:
    with path.open(newline="") as f:
        return [Series.from_row(r) for r in csv.DictReader(f)]


def load_releases(path: Path = RELEASES_CSV) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


# --------------------------------------------------------------------------- periods

def prev_period(p: str, n: int = 1) -> str:
    """'2026-11' -> '2026-10'; '2026Q4' -> '2026Q3'."""
    if "Q" in p:
        y, q = int(p[:4]), int(p[-1])
        idx = y * 4 + (q - 1) - n
        return f"{idx // 4}Q{idx % 4 + 1}"
    y, m = int(p[:4]), int(p[5:7])
    idx = y * 12 + (m - 1) - n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]


def period_label(p: str, transform: str = "") -> str:
    if "Q" in p:
        return f"Q{p[-1]} {p[:4]}"
    name = f"{_MONTHS[int(p[5:7]) - 1]} {p[:4]}"
    return f"the end of {name}" if transform == "month_end" else name


def date_label(d: str) -> str:
    dd = date.fromisoformat(d)
    return f"{_MONTHS[dd.month - 1]} {dd.day}, {dd.year}"


def period_of(obs_date: str, frequency: str) -> str:
    """FRED observation date -> period key."""
    y, m = int(obs_date[:4]), int(obs_date[5:7])
    if frequency == "quarterly":
        return f"{y}Q{(m - 1) // 3 + 1}"
    return f"{y:04d}-{m:02d}"


# --------------------------------------------------------------------------- fetch + transform

def parse_fred_csv(text: str) -> list[tuple[str, float]]:
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        k = "observation_date" if "observation_date" in row else next(iter(row))
        v = row.get(next(c for c in row if c != k), ".")
        if v in (".", "", None):
            continue
        out.append((row[k], float(v)))
    return out


def fetch_fred(http: Http, fred_id: str) -> list[tuple[str, float]]:
    return parse_fred_csv(http(FRED_CSV, {"id": fred_id}))


def parse_bls_v1(text: str) -> dict[str, float]:
    """BLS v1 JSON -> {period: value} for monthly series (M01..M12; M13 annual is dropped)."""
    doc = json.loads(text)
    if doc.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError(f"BLS: {doc.get('status')} {doc.get('message')}")
    out: dict[str, float] = {}
    for s in doc["Results"]["series"]:
        for d in s["data"]:
            if d["period"].startswith("M") and d["period"] != "M13":
                out[f"{d['year']}-{d['period'][1:]}"] = float(d["value"])
    return out


def fetch_bls(http: Http, bls_id: str) -> dict[str, float]:
    return parse_bls_v1(http(f"{BLS_V1}{bls_id}", None))


def raw_by_period(series: Series, obs: list[tuple[str, float]]) -> dict[str, float]:
    """Collapse dated observations to one raw value per period. ``month_end`` keeps the last
    observation of the month; everything else is one observation per period already."""
    out: dict[str, float] = {}
    for d, v in obs:
        out[period_of(d, series.frequency)] = v   # later dates overwrite earlier within a period
    return out


def transformed(series: Series, raw: dict[str, float]) -> dict[str, float]:
    """Apply the series transform to raw levels: {period: value in question units}."""
    out: dict[str, float] = {}
    for p, v in raw.items():
        t = series.transform
        if t in ("level", "month_end"):
            val = v
        elif t == "yoy":
            prev = raw.get(prev_period(p, 12))
            if prev in (None, 0):
                continue
            val = (v / prev - 1.0) * 100.0
        elif t == "mom":
            prev = raw.get(prev_period(p, 1))
            if prev in (None, 0):
                continue
            val = (v / prev - 1.0) * 100.0
        elif t == "diff":
            prev = raw.get(prev_period(p, 1))
            if prev is None:
                continue
            val = v - prev
        else:
            continue
        out[p] = round(val, series.decimals)
    return dict(sorted(out.items()))


def history(series: Series, http: Http) -> dict[str, float]:
    """Transformed history for a series from FRED (current vintage)."""
    return transformed(series, raw_by_period(series, fetch_fred(http, series.fred_id)))


# --------------------------------------------------------------------------- generation

def _seed(season: str, series_id: str, period: str) -> int:
    return int(hashlib.sha256(f"{season}|{series_id}|{period}".encode()).hexdigest()[:16], 16)


def threshold_offset(season: str, series: Series, period: str, step: float | None = None) -> float:
    """Seeded ±1 or ±2 steps. Deterministic given (season, series, period, step)."""
    rng = random.Random(_seed(season, series.series_id, period))
    return rng.choice([-1, 1]) * rng.choice([1, 2]) * (step or series.step)


def effective_step(series: Series, spread: float) -> float:
    """Offset unit: the larger of the template step and the historical spread of changes, so a
    threshold two steps out is a real question for volatile and for quiet series alike."""
    return max(series.step, round(spread, series.decimals)) or series.step


def _stats(hist: dict[str, float], upto_period: str) -> tuple[list[float], float | None]:
    """Changes between consecutive known periods strictly before ``upto_period`` and the latest
    value before it."""
    keys = [k for k in sorted(hist) if k < upto_period][-(HISTORY_WINDOW + 1):]
    deltas = [hist[b] - hist[a] for a, b in pairwise(keys)]
    ref = hist[keys[-1]] if keys else None
    return deltas, ref


def _fmt(v: float, series: Series) -> str:
    return f"{round(v)}" if series.integer else f"{v:.{series.decimals}f}"


def _resolution(series: Series, rel: dict[str, str], *, period: str) -> dict[str, Any]:
    rd = date.fromisoformat(rel["release_date"])
    return {
        "primary_source": series.source_url,
        "provisional_rule": f"first_print:{series.transform}:{series.bls_id or series.fred_id}:{period}",
        "final_rule": "first_print_is_final; later revisions are ignored",
        "vintage_rule": "first_release",
        "expected_resolution_date": rel["release_date"],
        "fallback_sources": [f"FRED/ALFRED {series.fred_id} initial release (output_type=4)",
                             "agency news release table", "council"],
        "void_if": list(VOID_IF),
        "T_final": (rd + timedelta(days=T_FINAL_DAYS)).isoformat(),
        "resolver": "automated",
        "resolver_id": RESOLVER_ID,
        "schedule_source": rel.get("source", ""),
    }


def _common(series: Series, rel: dict[str, str], period: str) -> dict[str, Any]:
    return {
        "track": "ranked",
        "cluster": f"{FAMILY}/{rel['release_date'][:7]}",
        "template": {"name": "macro", "series_id": series.series_id, "release": series.release,
                     "period": period},
        "unit": series.unit,
        "event_date": rel["release_date"],
        "gdelt_query": f'"{series.agency}" {series.label.split(" ")[0]} sourcelang:english',
    }


def generate_for_release(season: str, series: Series, rel: dict[str, str],
                         hist: dict[str, float], *, as_of: str) -> list[dict[str, Any]]:
    """Questions for one (series, release). ``hist`` is the transformed history known at
    generation time; only periods strictly before ``rel['period']`` are used."""
    period = rel["period"]
    rd_label = date_label(rel["release_date"])
    if series.transform == "fomc":
        start = rel.get("meeting_start") or rel["release_date"]
        d0, d1 = date.fromisoformat(start), date.fromisoformat(rel["release_date"])
        dates = f"{_MONTHS[d0.month - 1]} {d0.day}–{d1.day}, {d1.year}"
        q = Q._base(
            f"macro-fomc-{period}", FAMILY,
            f"At its {dates} meeting, how will the FOMC change the federal funds target range "
            f"relative to the range in force before the meeting? (cut_50bp_or_more, cut_25bp, "
            f"hold, hike)",
            "categorical", categories=list(FOMC_CATEGORIES),
            baseline={"probabilities": dict(FOMC_BASELINE), "fav": "hold"},
            headline=series.headline,
            fomc={"meeting_start": start, "meeting_end": rel["release_date"]},
            resolution=_resolution(series, rel, period=period), **_common(series, rel, period))
        if series.headline and series.headline_label:
            q["headline_label"] = series.headline_label
        return [q]

    deltas, ref = _stats(hist, period)
    if ref is None:
        raise ValueError(f"{series.series_id}: no history before {period}")
    plabel = period_label(period, series.transform)
    spread = (statistics.pstdev(deltas) if len(deltas) >= 6 else 2 * series.step) or series.step
    reference = {"period": prev_period(period), "value": ref, "as_of": as_of,
                 "source": f"fred:{series.fred_id}", "vintage": "latest"}
    base = _common(series, rel, period)
    base["reference"] = reference

    quant = Q._base(
        f"macro-{series.series_id}-{period}", FAMILY,
        f"What will the first-published value of the {series.label} for {plabel} be, as "
        f"released by the {series.agency} on {rd_label}? Answer in {series.unit}.",
        "quantile", quantile_levels=list(Q.SEAT_LEVELS), bounds=[series.lo, series.hi],
        baseline={"centre": ref, "spread": round(spread, series.decimals + 1),
                  "integer": series.integer},
        headline=series.headline,
        resolution=_resolution(series, rel, period=period), **base)
    if series.headline and series.headline_label:
        quant["headline_label"] = series.headline_label

    off = threshold_offset(season, series, period, effective_step(series, spread))
    thr = round(ref + off, series.decimals)
    p_hist = (sum(1 for d in deltas if d > off) / len(deltas)) if deltas else 0.5
    binary = Q._base(
        f"macro-{series.series_id}-{period}-gt", FAMILY,
        f"Will the first-published value of the {series.label} for {plabel}, released by the "
        f"{series.agency} on {rd_label}, be strictly greater than {_fmt(thr, series)} "
        f"{series.unit}?",
        "binary", threshold=thr, threshold_offset=off,
        baseline={"p": round(min(max(p_hist, 0.05), 0.95), 3)},
        resolution=_resolution(series, rel, period=period), **base)
    return [quant, binary]


def releases_in_month(releases: list[dict[str, str]], month: str) -> list[dict[str, str]]:
    return [r for r in releases if r["release_date"][:7] == month]


def generate_month(season: str, month: str, *, http: Http, series: list[Series] | None = None,
                   releases: list[dict[str, str]] | None = None,
                   histories: dict[str, dict[str, float]] | None = None,
                   as_of: str | None = None) -> list[dict[str, Any]]:
    """All macro questions whose release falls in ``month`` (YYYY-MM)."""
    series = series or load_series()
    releases = releases or load_releases()
    as_of = as_of or datetime.now(UTC).date().isoformat()
    by_release: dict[str, list[Series]] = {}
    for s in series:
        by_release.setdefault(s.release, []).append(s)
    histories = dict(histories or {})
    out: list[dict[str, Any]] = []
    for rel in releases_in_month(releases, month):
        for s in by_release.get(rel["release"], []):
            if s.transform != "fomc" and s.series_id not in histories:
                histories[s.series_id] = history(s, http)
            out.extend(generate_for_release(season, s, rel, histories.get(s.series_id, {}),
                                            as_of=as_of))
    return out


# --------------------------------------------------------------------------- resolution

def fomc_outcome(upper_before: float, upper_after: float) -> str:
    bp = round((upper_after - upper_before) * 100)
    if bp <= -50:
        return "cut_50bp_or_more"
    if bp < 0:
        return "cut_25bp"
    if bp == 0:
        return "hold"
    return "hike"


def resolve_question(q: dict[str, Any], *, http: Http, today: date | None = None,
                     series_by_id: dict[str, Series] | None = None,
                     cache: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a truth row {question_id, y, status, vintage, value}. ``status`` is ``resolved``,
    ``pending`` (release not yet published) or ``void`` (past the void window with no data)."""
    today = today or datetime.now(UTC).date()
    cache = cache if cache is not None else {}
    series_by_id = series_by_id or {s.series_id: s for s in load_series()}
    t = q["template"]
    s = series_by_id[t["series_id"]]
    period = t["period"]
    exp = date.fromisoformat(q["resolution"]["expected_resolution_date"])
    row = {"question_id": q["question_id"], "y": None, "status": "pending",
           "vintage": today.isoformat(), "value": None}
    if today < exp:
        return row

    if s.transform == "fomc":
        key = f"fred:{s.fred_id}"
        if key not in cache:
            cache[key] = fetch_fred(http, s.fred_id)
        obs = cache[key]
        start, end = q["fomc"]["meeting_start"], q["fomc"]["meeting_end"]
        before = [v for d, v in obs if d < start]
        after = [v for d, v in obs if d > end]
        if not before or not after:
            return _maybe_void(row, today, exp)
        row.update(y=fomc_outcome(before[-1], after[0]), status="resolved",
                   value=after[0])
        return row

    value = None
    if s.bls_id:
        key = f"bls:{s.bls_id}"
        try:
            if key not in cache:
                cache[key] = fetch_bls(http, s.bls_id)
            value = transformed(s, cache[key]).get(period)
        except Exception:  # noqa: BLE001 — fall through to FRED
            value = None
    if value is None:
        key = f"fred:{s.fred_id}"
        if key not in cache:
            cache[key] = fetch_fred(http, s.fred_id)
        value = transformed(s, raw_by_period(s, cache[key])).get(period)
    if value is None:
        return _maybe_void(row, today, exp)
    row["value"] = value
    if q["output_type"] == "quantile":
        row.update(y=value, status="resolved")
    else:
        row.update(y=int(value > float(q["threshold"])), status="resolved")
    return row


def _maybe_void(row: dict[str, Any], today: date, exp: date) -> dict[str, Any]:
    if (today - exp).days > VOID_AFTER_DAYS:
        row["status"] = "void"
    return row


def resolve_all(questions: list[dict[str, Any]], *, http: Http,
                today: date | None = None) -> list[dict[str, Any]]:
    cache: dict[str, Any] = {}
    sb = {s.series_id: s for s in load_series()}
    return [resolve_question(q, http=http, today=today, series_by_id=sb, cache=cache)
            for q in questions if q.get("template", {}).get("name") == "macro"]


# --------------------------------------------------------------------------- dry run on history

def dry_run(series: Series, hist: dict[str, float], *, season: str = "dry-run",
            n: int = 12) -> list[dict[str, Any]]:
    """Replay the template over the last ``n`` known periods: generate the pair with only the
    history before each period, then resolve against the actual value. Mechanical, no judgment."""
    rows = []
    for period in sorted(hist)[-n:]:
        deltas, ref = _stats(hist, period)
        if ref is None:
            continue
        spread = (statistics.pstdev(deltas) if len(deltas) >= 6 else 2 * series.step) or series.step
        off = threshold_offset(season, series, period, effective_step(series, spread))
        thr = round(ref + off, series.decimals)
        actual = hist[period]
        p_hist = (sum(1 for d in deltas if d > off) / len(deltas)) if deltas else 0.5
        rows.append({"period": period, "reference": ref, "threshold": thr, "actual": actual,
                     "binary_y": int(actual > thr), "baseline_p": round(p_hist, 3),
                     "abs_error_vs_reference": round(abs(actual - ref), series.decimals)})
    return rows


# --------------------------------------------------------------------------- packet source

class MacroSeries:
    """Packet section: the last ``PACKET_ROWS`` transformed observations for every template
    series, from FRED (current vintage, which for these series equals the first print except
    for seasonally adjusted and revised items; the packet says so)."""
    name = "macro_series"
    licence = "FRED terms of use (public-domain US government data)"
    budget_weight = 2

    def __init__(self, series: list[Series] | None = None):
        self.series = [s for s in (series or load_series()) if s.transform != "fomc"]

    def fetch(self, http: Http, day: date) -> Section:
        data: dict[str, Any] = {}
        raws: dict[str, str] = {}
        errors: list[str] = []
        for s in self.series:
            try:
                txt = http(FRED_CSV, {"id": s.fred_id})
                raws[s.fred_id] = txt
                hist = transformed(s, raw_by_period(s, parse_fred_csv(txt)))
                # Only observations dated before `day` (packet is D-1 information).
                cutoff = period_of(day.isoformat(), s.frequency)
                items = [(p, v) for p, v in hist.items() if p < cutoff][-PACKET_ROWS:]
                data[s.series_id] = {"label": s.label, "unit": s.unit, "fred_id": s.fred_id,
                                     "observations": items}
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{s.series_id}: {type(exc).__name__}: {exc}")
        if not data:
            return Section(self.name, "unavailable", self.licence, {},
                           reason="; ".join(errors) or "no data")
        return Section(self.name, "ok", self.licence,
                       {"provider": "Federal Reserve Bank of St. Louis (FRED)",
                        "note": "latest vintage as of the snapshot"},
                       data=data, reason="; ".join(errors) or None, raw=json.dumps(raws))

    def render(self, section: Section, budget: int) -> str:
        if section.status != "ok":
            return f"## Macro data (FRED)\n\n_unavailable: {section.reason}_\n"
        lines = ["## Macro data (FRED, latest vintage; values may differ from first prints)\n"]
        for sid, d in section.data.items():
            lines.append(f"### {sid} — {d['label']} ({d['unit']})")
            lines.append("| period | value |")
            lines.append("|---|---|")
            lines.extend(f"| {p} | {v} |" for p, v in d["observations"])
            lines.append("")
        return "\n".join(lines)[:budget] + "\n"
