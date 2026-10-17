"""Deadlines, per-wave time budget and slippage rules for phase 04.1.

Everything is read from the dated registration file `data/phase-04.1/registration.json`
(fixed before any model output exists). Standard library only, so it does not wait on the
other wave-1 modules. `python -m fc.train.schedule status --wave N` prints one JSON line.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

REGISTRATION_PATH = Path(__file__).resolve().parents[2] / "data" / "phase-04.1" / "registration.json"

SFT_BUILD = dt.timedelta(hours=1)
LAST_PRE_RUN_A_WAVE = 5


def _utc(text: str) -> dt.datetime:
    when = dt.datetime.fromisoformat(text)
    if when.tzinfo is None:
        raise ValueError(f"timestamp without a timezone: {text!r}")
    return when.astimezone(dt.UTC)


def _iso(when: dt.datetime) -> str:
    return when.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_registration(path: Path = REGISTRATION_PATH) -> dict:
    return json.loads(Path(path).read_text())


_REG = load_registration()
_SC = _REG["schedule"]
TRAIN_CUTOFF_UTC = _utc(_SC["train_cutoff_utc"])
RUN_A_START_BY_UTC = _utc(_SC["run_a_start_by_utc"])
RUN_A_DEADLINE_UTC = _utc(_SC["run_a_deadline_utc"])
FALLBACK_DECISION_UTC = _utc(_SC["fallback_decision_utc"])
FREEZE_DEADLINE_UTC = _utc(_SC["freeze_deadline_utc"])


def train_allowed(now: dt.datetime) -> bool:
    """No training process runs at or after the train cutoff."""
    return now < TRAIN_CUTOFF_UTC


def run_a_allowed(now: dt.datetime) -> bool:
    """Run A may only start strictly before run_a_start_by_utc (rule S1 otherwise)."""
    return now < RUN_A_START_BY_UTC


def past_fallback_decision(now: dt.datetime) -> bool:
    return now >= FALLBACK_DECISION_UTC


def _waves(registration: dict) -> list[dict]:
    return registration["schedule"]["waves"]


def _hours_after(registration: dict, first: int, last: int) -> dt.timedelta:
    high = sum(w["expected_hours"][1] for w in _waves(registration) if first <= w["wave"] <= last)
    return dt.timedelta(hours=high)


def wave_status(now: dt.datetime, wave: int, registration: dict | None = None) -> dict:
    """Report one wave's overrun and the slippage rule that applies. A report, not a gate."""
    reg = registration or _REG
    row = next((w for w in _waves(reg) if w["wave"] == wave), None)
    if row is None:
        raise ValueError(f"unknown wave {wave}; registered waves are 1-{len(_waves(reg))}")
    overrun = now >= _utc(row["latest_done_utc"])
    rules = reg["schedule"]["slippage_rules"]
    out = {
        "wave": wave,
        "plans": row["plans"],
        "expected_hours": row["expected_hours"],
        "latest_done_utc": row["latest_done_utc"],
        "overrun": overrun,
        "rule": ",".join(row["on_overrun"]) if overrun else None,
        "action": " | ".join(rules[r] for r in row["on_overrun"]) if overrun else None,
    }
    if wave <= LAST_PRE_RUN_A_WAVE:
        later = _hours_after(reg, wave + 1, LAST_PRE_RUN_A_WAVE)
        out["projected_run_a_start_utc"] = _iso(now + later + SFT_BUILD)
    return out


def achievable(registration: dict) -> bool:
    """True when registered_at plus the high estimates of waves 1-5 plus the SFT build fits."""
    start = _utc(registration["registered_at"])
    ready = start + _hours_after(registration, 1, LAST_PRE_RUN_A_WAVE) + SFT_BUILD
    return ready <= _utc(registration["schedule"]["run_a_start_by_utc"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fc.train.schedule")
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("status", help="print one JSON line with a wave's overrun and rule")
    st.add_argument("--wave", type=int, required=True)
    st.add_argument("--now", help="ISO-8601 UTC override (tests only)")
    args = ap.parse_args(argv)
    now = _utc(args.now) if args.now else dt.datetime.now(dt.UTC)
    try:
        status = wave_status(now, args.wave)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    status["now_utc"] = _iso(now)
    status["achievable_at_registration"] = achievable(_REG)
    print(json.dumps(status, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
