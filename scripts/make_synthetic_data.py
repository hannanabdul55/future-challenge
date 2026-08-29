"""Generate synthetic weekly hospital-admission series for the scaffold round.

Writes target-data/vintages/synthetic-hosp/2026-09-05.csv  (history as of T_open)
and    target-data/vintages/synthetic-hosp/truth-2026-w37.csv (question_id, y) for scoring.
Clearly synthetic: SIR-ish wave + noise; not real surveillance data.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("target-data/vintages/synthetic-hosp")
LOCS = {"US": 20000, "CA": 2500, "TX": 2000}
ROUND = "2026-w37"
LAST_OBS = pd.Timestamp("2026-09-05")   # last observed Saturday (data vintage at T_open)
REF = pd.Timestamp("2026-09-12")        # reference date: Saturday after the Wed deadline; horizon 0
HORIZONS = [0, 1, 2, 3]


def series(scale: float, rng: np.random.Generator, n: int = 120) -> np.ndarray:
    t = np.arange(n)
    seasonal = 1 + 0.6 * np.sin(2 * np.pi * (t - 20) / 52)
    wave = 1 + 1.5 * np.exp(-((t - 118) / 6) ** 2)  # wave peaking ~2 weeks after the reference date
    ar = np.zeros(n)
    for i in range(1, n):
        ar[i] = 0.8 * ar[i - 1] + rng.normal(0, 0.05)
    return np.maximum(scale * seasonal * wave * np.exp(ar), 0)


def main():
    rng = np.random.default_rng(2026)
    OUT.mkdir(parents=True, exist_ok=True)
    n = 120
    dates = pd.date_range(end=REF + pd.Timedelta(weeks=3), periods=n, freq="W-SAT")
    assert LAST_OBS in dates and REF in dates
    hist, truth = [], []
    for loc, scale in LOCS.items():
        v = series(scale, rng, n)
        for d, x in zip(dates, v):
            if d <= LAST_OBS:
                hist.append({"location": loc, "date": d.date().isoformat(), "value": round(float(x))})
        for h in HORIZONS:
            d = REF + pd.Timedelta(weeks=h)
            y = v[list(dates).index(d)]
            truth.append({"question_id": f"{ROUND}-hosp-{loc}-h{h}", "y": round(float(y))})
    # extra questions from tasks.json with made-up truth
    truth.append({"question_id": f"{ROUND}-vuln-kev-additions", "y": 4})
    pd.DataFrame(hist).to_csv(OUT / "2026-09-05.csv", index=False)
    pd.DataFrame(truth).to_csv(OUT / f"truth-{ROUND}.csv", index=False)
    print(f"wrote {OUT}/2026-09-05.csv ({len(hist)} rows) and truth-{ROUND}.csv ({len(truth)} rows)")


if __name__ == "__main__":
    main()
