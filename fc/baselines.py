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


# --------------------------------------------------------------------------- season baselines

def _rows(model_id, round_id, qid, otype, pairs):
    return [{"model_id": model_id, "round_id": round_id, "question_id": qid, "output_type": otype,
             "output_type_id": k, "value": float(v)} for k, v in pairs]


def season_baselines(questions: list[dict], round_id: str, *,
                     persistence_p: float = 0.85) -> pd.DataFrame:
    """Reference entries for a frozen-model season (SCORE-02), computable at T_open from
    the question set alone:

    - ``fc-uniform``: ignorance — 0.5 / equal categories / a wide flat quantile fan
    - ``fc-baseline``: prior-election persistence — the party that won the state in the
      2024 presidential election (race questions) with probability ``persistence_p``;
      the sitting majority for control; the current split for seat counts
    """
    rows: list[dict] = []
    for q in questions:
        qid, t = q["question_id"], q["output_type"]
        if t == "binary":
            rows += _rows("fc-uniform", round_id, qid, t, [("p", 0.5)])
            # Both chambers are Republican-held going in; persistence says they stay so.
            rows += _rows("fc-baseline", round_id, qid, t, [("p", 1 - persistence_p)])
        elif t == "categorical":
            cats = q["categories"]
            rows += _rows("fc-uniform", round_id, qid, t, [(c, 1 / len(cats)) for c in cats])
            fav = q.get("pres_2024") or q.get("incumbent_party") or cats[0]
            other = (1 - persistence_p) / (len(cats) - 1)
            rows += _rows("fc-baseline", round_id, qid, t,
                          [(c, persistence_p if c == fav else other) for c in cats])
        else:
            levels = [float(x) for x in q["quantile_levels"]]
            lo, hi = q.get("bounds", [0, 1])
            rows += _rows("fc-uniform", round_id, qid, t,
                          [(str(lv), lo + (hi - lo) * lv) for lv in levels])
            centre = {"seats-house-d": 214, "seats-senate-dcaucus": 47}.get(qid, (lo + hi) / 2)
            spread = {"seats-house-d": 12, "seats-senate-dcaucus": 2.5}.get(qid, (hi - lo) / 8)
            z = {0.025: -1.96, 0.1: -1.28, 0.25: -0.67, 0.4: -0.25, 0.5: 0.0, 0.6: 0.25,
                 0.75: 0.67, 0.9: 1.28, 0.975: 1.96}
            vals = [min(max(round(centre + spread * z.get(lv, 0.0)), lo), hi) for lv in levels]
            vals = list(np.maximum.accumulate(np.array(vals, dtype=float)))
            rows += _rows("fc-baseline", round_id, qid, t, list(zip(map(str, levels), vals)))
    return pd.DataFrame(rows)
