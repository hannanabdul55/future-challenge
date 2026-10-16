"""Strictly proper scoring rules (DESIGN.md §8.1). Lower is better throughout."""

from __future__ import annotations

import numpy as np
import pandas as pd

LOG_CLIP = 1e-3


def pinball(q: float, pred: float, y: float) -> float:
    d = y - pred
    return float(max(q * d, (q - 1) * d))


def interval_score(alpha: float, lower: float, upper: float, y: float) -> float:
    s = upper - lower
    if y < lower:
        s += (2 / alpha) * (lower - y)
    elif y > upper:
        s += (2 / alpha) * (y - upper)
    return float(s)


def wis(levels: np.ndarray, values: np.ndarray, y: float) -> float:
    """Weighted interval score for a symmetric quantile set containing 0.5.

    WIS = 1/(K+1/2) * [ 1/2 |y-m| + sum_k alpha_k/2 * IS_alpha_k ]
    Equivalent to (2/N) * sum of pinball losses over the N quantiles.
    """
    levels = np.asarray(levels, dtype=float)
    values = np.asarray(values, dtype=float)
    order = np.argsort(levels)
    levels, values = levels[order], values[order]
    if not np.isclose(levels, 1 - levels[::-1]).all():
        raise ValueError("quantile levels must be symmetric around 0.5")
    if not np.any(np.isclose(levels, 0.5)):
        raise ValueError("quantile set must include the median")
    m = values[np.isclose(levels, 0.5)][0]
    K = (len(levels) - 1) // 2
    total = 0.5 * abs(y - m)
    for k in range(K):
        alpha = 2 * levels[k]
        total += (alpha / 2) * interval_score(alpha, values[k], values[-(k + 1)], y)
    return float(total / (K + 0.5))


def crps_samples(samples: np.ndarray, y: float) -> float:
    """CRPS from an empirical sample: E|X-y| - 1/2 E|X-X'|."""
    x = np.sort(np.asarray(samples, dtype=float))
    n = len(x)
    term1 = np.mean(np.abs(x - y))
    # E|X-X'| via sorted formula: (2/n^2) * sum_i (2i - n - 1) x_i  (i 1-indexed)
    i = np.arange(1, n + 1)
    term2 = (2.0 / (n * n)) * np.sum((2 * i - n - 1) * x)
    return float(term1 - 0.5 * term2)


def log_score(p: float, y: int, clip: float = LOG_CLIP) -> float:
    p = min(max(p, clip), 1 - clip)
    return float(-np.log(p if y == 1 else 1 - p))


def brier(p: float, y: int) -> float:
    return float((p - y) ** 2)


def score_quantile_frame(pred: pd.DataFrame, truth: pd.DataFrame) -> pd.DataFrame:
    """Score a hubverse-style quantile frame.

    pred columns : model_id, question_id, output_type(=='quantile'), output_type_id, value
    truth columns: question_id, y
    returns      : model_id, question_id, score (WIS)
    """
    pred = pred[pred["output_type"] == "quantile"]
    t = truth.set_index("question_id")["y"]
    rows = []
    for (m, qid), g in pred.groupby(["model_id", "question_id"], sort=False):
        if qid not in t.index:
            continue
        rows.append(
            {
                "model_id": m,
                "question_id": qid,
                "score": wis(
                    g["output_type_id"].astype(float).to_numpy(),
                    g["value"].astype(float).to_numpy(),
                    float(t[qid]),
                ),
            }
        )
    return pd.DataFrame(rows, columns=["model_id", "question_id", "score"])


def score_frame(pred: pd.DataFrame, truth: pd.DataFrame, *, clip: float = LOG_CLIP) -> pd.DataFrame:
    """Score a mixed-type hubverse-style frame (rules 2026.10 §6).

    pred columns : model_id, question_id, output_type, output_type_id, value
                   (optionally round_id — carried through when present)
    truth columns: question_id, y   where y is 0/1 for binary, the category label for
                   categorical, and a number for quantile questions
    returns      : [round_id,] model_id, question_id, output_type, score
                   (log score for binary/categorical; WIS for quantile; lower is better)
    """
    t = truth.drop_duplicates("question_id").set_index("question_id")["y"]
    keys = ["model_id", "question_id"]
    has_round = "round_id" in pred.columns
    if has_round:
        keys = ["round_id", *keys]
    rows = []
    for key, g in pred.groupby(keys, sort=False):
        qid = key[-1]
        if qid not in t.index:
            continue
        y = t[qid]
        otype = g["output_type"].iloc[0]
        if otype == "quantile":
            s = wis(g["output_type_id"].astype(float).to_numpy(),
                    g["value"].astype(float).to_numpy(), float(y))
        elif otype == "binary":
            s = log_score(float(g["value"].iloc[0]), int(float(y)), clip)
        elif otype == "categorical":
            probs = dict(zip(g["output_type_id"].astype(str), g["value"].astype(float)))
            p = probs.get(str(y), 0.0)
            s = float(-np.log(min(max(p, clip), 1.0)))
        else:
            continue
        row = dict(zip(keys, key))
        row.update({"output_type": otype, "score": s})
        rows.append(row)
    cols = [*keys, "output_type", "score"]
    return pd.DataFrame(rows, columns=cols)


def ensemble_mean_probs(pred: pd.DataFrame, exclude: str | None = None,
                        model_id: str = "hub-ensemble") -> pd.DataFrame:
    """Hub ensemble for mixed types: mean of probabilities (binary/categorical, renormalised
    per question) and quantile-wise median (quantile)."""
    p = pred if exclude is None else pred[pred["model_id"] != exclude]
    probs = p[p["output_type"].isin(["binary", "categorical"])]
    quants = p[p["output_type"] == "quantile"]
    keys = [c for c in ("round_id", "question_id", "output_type", "output_type_id") if c in p.columns]
    parts = []
    if len(probs):
        m = probs.groupby(keys, as_index=False)["value"].mean()
        gk = [k for k in keys if k != "output_type_id"]
        m["value"] = m["value"] / m.groupby(gk)["value"].transform("sum")
        parts.append(m)
    if len(quants):
        parts.append(quants.groupby(keys, as_index=False)["value"].median())
    e = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=[*keys, "value"])
    e.insert(0, "model_id", model_id)
    return e
