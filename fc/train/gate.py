"""Registered ship gate for the phase 04.1 entry (SC-3, D-06, D-19).

Pure functions over a per-system report; the rule itself lives in
`data/phase-04.1/registration.json` (eval.gate, eval.day_order) and in `fc.train.schedule`.
Standard library only, plus `schedule`.

`report` is `{"tuned": system | None, "base": system | None}` with
`system = {"valid": bool, "gguf_ok": bool, "days": {day: {"parse_rate", "primary", "modes_ok"}}}`.
`primary` is the mean race log score (lower is better).
"""

from __future__ import annotations

import datetime as dt

from . import schedule


def _days(system: dict | None) -> set[str]:
    return set(system["days"]) if system else set()


def compared_days(registration: dict, done_tuned, done_base) -> list[str]:
    """Days both systems finished, in the registered evaluation order."""
    both = set(done_tuned) & set(done_base)
    return [d for d in registration["eval"]["day_order"] if d in both]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _result(ship: str, reason: str, days: list[str], parse=None, primary=None) -> dict:
    return {"ship": ship, "reason": reason, "compared_days": days,
            "parse": parse or {}, "primary": primary or {}}


def _confounded(system: dict) -> bool:
    return (not system.get("valid", True)
            or any(not d.get("modes_ok", False) for d in system["days"].values()))


def ship_decision(report: dict, *, now: dt.datetime, registration: dict) -> dict:
    tuned, base = report.get("tuned"), report.get("base")
    ev = registration["eval"]
    gate = ev["gate"]

    if base is not None and not base.get("gguf_ok", False):
        reason = "tuned_unavailable" if tuned is None else "base_gguf_failed"
        return _result("base-public", reason, [])

    if tuned is None or not tuned.get("gguf_ok", False) or not tuned["days"]:
        past = tuned is None and now >= schedule.FALLBACK_DECISION_UTC
        return _result("base", "deadline" if past else "tuned_unavailable", [])

    if base is None or not base["days"]:
        return _result("base", "base_unavailable", [])

    if _confounded(tuned) or _confounded(base):
        return _result("base", "thinking_confound", [])

    days = compared_days(registration, _days(tuned), _days(base))
    first = ev["day_order"][:gate["min_first_ordered_days"]]
    if not set(first) <= set(days):
        return _result("base", "insufficient_days", days)

    parse = {"tuned": _mean([tuned["days"][d]["parse_rate"] for d in days]),
             "base": _mean([base["days"][d]["parse_rate"] for d in days])}
    primary = {"tuned": _mean([tuned["days"][d]["primary"] for d in days]),
               "base": _mean([base["days"][d]["primary"] for d in days])}
    if not parse["tuned"] >= parse["base"]:
        return _result("base", "parse_below_base", days, parse, primary)
    if not primary["tuned"] <= primary["base"]:
        return _result("base", "log_worse", days, parse, primary)
    return _result("tuned", "gate_passed", days, parse, primary)
