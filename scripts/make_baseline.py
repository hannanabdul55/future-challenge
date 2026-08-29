"""Produce model-output for fc-baseline and two illustrative synthetic entries for round 2026-w37."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from fc.baselines import persistence_quantiles

ROUND = "2026-w37"
TASKS = json.loads(Path("hub-config/tasks.json").read_text())
RC = next(r for r in TASKS["rounds"] if r["round_id"] == ROUND)
LEVELS = RC["quantile_levels"]
QIDS = [q["question_id"] for q in RC["questions"] if q["family"] == "epi-synthetic"]


def write(df: pd.DataFrame, model_id: str):
    out = Path("model-output") / model_id / f"{ROUND}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.drop(columns=["model_id"]).to_csv(out, index=False)
    print(f"wrote {out} ({len(df)} rows)")


def main():
    hist = pd.read_csv("target-data/vintages/synthetic-hosp/2026-09-05.csv")
    base = persistence_quantiles(hist, QIDS, [0, 1, 2, 3], LEVELS, ROUND,
                                 question_fmt="{round_id}-hosp-{location}-h{h}")
    write(base, "fc-baseline")

    # illustrative entries: a "trend-aware" model and a noisy copy of the baseline
    rng = np.random.default_rng(7)
    trend = base.copy()
    for qid, g in trend.groupby("question_id"):
        h = int(qid.rsplit("h", 1)[1])
        trend.loc[g.index, "value"] = g["value"] * (1 + 0.12 * h)  # anticipates the wave
    trend["model_id"] = "example-trend"
    write(trend, "example-trend")

    noisy = base.copy()
    noisy["value"] = noisy["value"] * rng.uniform(0.97, 1.03)
    noisy["model_id"] = "example-copycat"
    write(noisy, "example-copycat")


if __name__ == "__main__":
    main()
