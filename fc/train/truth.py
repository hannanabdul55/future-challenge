"""Historical race truth (D-07). Stub for the RED commit; implemented in the GREEN commit."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Contest:
    year: int
    office: str
    state: str
    special: bool
    seat_key: str
    winner_party: str
    winner_name: str
    caucus: str
    d_pct: float | None
    r_pct: float | None
    source: str


def normalise_stage(stage: str) -> str:
    return ""


def load_medsl_senate(path, years):
    return []


def apply_overrides(contests, overrides_csv):
    return list(contests)


def add_missing(contests, missing_csv):
    return list(contests)
