"""Relative skill, ranking uncertainty, ensemble contribution (DESIGN.md §8.2–8.6)."""

from __future__ import annotations

import numpy as np
import pandas as pd

EPS = 1e-9


def _wide(scores: pd.DataFrame) -> pd.DataFrame:
    return scores.pivot_table(index="question_id", columns="model_id", values="score")


def pairwise_theta(scores: pd.DataFrame, min_shared: int = 20) -> pd.DataFrame:
    """theta[m, m'] = geometric mean over shared questions of S_m / S_m'. NaN if too few shared."""
    w = _wide(scores)
    models = list(w.columns)
    out = pd.DataFrame(np.nan, index=models, columns=models, dtype=float)
    logw = np.log(w + EPS)
    for a in models:
        for b in models:
            if a == b:
                out.loc[a, b] = 1.0
                continue
            both = logw[[a, b]].dropna()
            if len(both) < min_shared:
                continue
            out.loc[a, b] = float(np.exp((both[a] - both[b]).mean()))
    return out


def relative_skill(scores: pd.DataFrame, baseline: str, min_shared: int = 20) -> pd.Series:
    """rel_skill_m = theta_m / theta_baseline, theta_m = geometric mean over m' of theta[m,m']."""
    th = pairwise_theta(scores, min_shared)
    theta_m = np.exp(np.log(th).mean(axis=1, skipna=True))
    if baseline not in theta_m.index or not np.isfinite(theta_m[baseline]):
        raise ValueError(f"baseline {baseline!r} not scorable")
    return (theta_m / theta_m[baseline]).sort_values()


def bootstrap_relative_skill(scores: pd.DataFrame, baseline: str, n_boot: int = 500,
                             block: pd.Series | None = None, seed: int = 0,
                             min_shared: int = 20) -> pd.DataFrame:
    """95 % bootstrap intervals on relative skill.

    block: optional Series question_id -> block key (e.g. ISO week) for block bootstrap.
    """
    rng = np.random.default_rng(seed)
    qids = scores["question_id"].unique()
    groups: dict = {}
    keys: list = []
    if block is not None:
        groups = pd.Series(qids).groupby(block.reindex(qids).to_numpy()).apply(list).to_dict()
        keys = list(groups)
    draws = []
    for _ in range(n_boot):
        if block is None:
            sample = rng.choice(qids, size=len(qids), replace=True)
        else:
            picked = rng.choice(keys, size=len(keys), replace=True)
            sample = np.concatenate([groups[k] for k in picked])
        # resample with multiplicity by repeating rows
        counts = pd.Series(sample).value_counts()
        sub = scores[scores["question_id"].isin(counts.index)].copy()
        sub = sub.loc[sub.index.repeat(sub["question_id"].map(counts).to_numpy())]
        try:
            draws.append(relative_skill(sub, baseline, min_shared))
        except ValueError:
            continue
    boot = pd.concat(draws, axis=1)
    point = relative_skill(scores, baseline, min_shared)
    return pd.DataFrame(
        {
            "rel_skill": point,
            "lo95": boot.quantile(0.025, axis=1).reindex(point.index),
            "hi95": boot.quantile(0.975, axis=1).reindex(point.index),
            "n_questions": scores.groupby("model_id")["question_id"].nunique().reindex(point.index),
        }
    )


def ensemble_median(pred: pd.DataFrame, exclude: str | None = None, model_id: str = "hub-ensemble") -> pd.DataFrame:
    """Quantile-wise median across models (hubverse default ensemble)."""
    p = pred if exclude is None else pred[pred["model_id"] != exclude]
    e = (
        p.groupby(["question_id", "output_type", "output_type_id"], as_index=False)["value"]
        .median()
    )
    e.insert(0, "model_id", model_id)
    return e


def contribution(pred: pd.DataFrame, truth: pd.DataFrame, scorer) -> pd.Series:
    """Delta_m = S(ensemble without m) - S(ensemble). Positive = m adds information."""
    full = scorer(ensemble_median(pred), truth)["score"].mean()
    out = {}
    for m in pred["model_id"].unique():
        loo = scorer(ensemble_median(pred, exclude=m), truth)["score"].mean()
        out[m] = float(loo - full)
    return pd.Series(out, name="contribution").sort_values(ascending=False)
