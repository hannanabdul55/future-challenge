"""Reference models (DESIGN.md §13). Scaffold ships persistence with empirical residual spread."""

from __future__ import annotations

import numpy as np
import pandas as pd


def persistence_quantiles(history: pd.DataFrame, question_ids: list[str], horizons: list[int],
                          levels: list[float], round_id: str, model_id: str = "fc-baseline",
                          location_col: str = "location", value_col: str = "value",
                          question_fmt: str = "{round_id}-{location}-h{h}") -> pd.DataFrame:
    """Persistence: median = last value; spread from historical h-step absolute changes.

    history: long frame with columns [location, date, value], sorted by date.
    question_fmt builds the question_id for (location, horizon); keep in sync with tasks.json.
    """
    rows = []
    wanted = set(question_ids)
    for loc, g in history.groupby(location_col):
        v = g.sort_values("date")[value_col].to_numpy(dtype=float)
        last = v[-1]
        for h in horizons:
            qid = question_fmt.format(round_id=round_id, location=loc, h=h)
            if qid not in wanted:
                continue
            diffs = v[h:] - v[:-h] if h > 0 and len(v) > h else np.array([0.0])
            qs = np.quantile(diffs, levels) if len(diffs) > 1 else np.zeros(len(levels))
            vals = np.maximum(last + qs, 0.0)
            vals = np.maximum.accumulate(vals)  # enforce monotone
            for lv, val in zip(levels, vals):
                rows.append(
                    {"round_id": round_id, "question_id": qid, "output_type": "quantile",
                     "output_type_id": lv, "value": float(val)}
                )
    out = pd.DataFrame(rows)
    out.insert(0, "model_id", model_id)
    return out
