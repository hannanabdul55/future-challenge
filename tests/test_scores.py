import numpy as np
import pandas as pd

from fc.eval import (
    bootstrap_relative_skill,
    brier,
    contribution,
    crps_samples,
    ensemble_median,
    log_score,
    pinball,
    relative_skill,
    score_quantile_frame,
    wis,
)

LEVELS = np.array([0.025, 0.1, 0.25, 0.5, 0.75, 0.9, 0.975])


def test_wis_equals_mean_pinball_times_two():
    rng = np.random.default_rng(0)
    for _ in range(50):
        vals = np.sort(rng.normal(size=len(LEVELS)) * 10)
        y = rng.normal() * 10
        w = wis(LEVELS, vals, y)
        pb = np.mean([pinball(q, v, y) for q, v in zip(LEVELS, vals)]) * 2
        assert np.isclose(w, pb)


def test_wis_rewards_calibrated_sharpness():
    y = 0.0
    narrow = np.array([-1, -0.5, -0.2, 0, 0.2, 0.5, 1])
    wide = narrow * 5
    assert wis(LEVELS, narrow, y) < wis(LEVELS, wide, y)
    assert wis(LEVELS, narrow + 10, y) > wis(LEVELS, wide, y)  # miss badly


def test_crps_sample_matches_closed_form_for_point_mass():
    assert np.isclose(crps_samples(np.array([2.0, 2.0, 2.0]), 5.0), 3.0)


def test_log_and_brier():
    assert log_score(0.9, 1) < log_score(0.5, 1) < log_score(0.1, 1)
    assert np.isfinite(log_score(0.0, 1))
    assert brier(1.0, 1) == 0 and brier(0.0, 1) == 1


def _norm_ppf(p):
    # Acklam approximation, fine for tests
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    if p < 0.02425:
        q = np.sqrt(-2 * np.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > 1 - 0.02425:
        return -_norm_ppf(1 - p)
    q = p - 0.5; r = q*q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def synthetic(n_q=40, seed=1):
    rng = np.random.default_rng(seed)
    truth = pd.DataFrame({"question_id": [f"q{i}" for i in range(n_q)], "y": rng.normal(100, 10, n_q)})
    rows = []
    for m, (bias, spread) in {"good": (0, 8), "bad": (15, 8), "fc-baseline": (0, 30)}.items():
        for i in range(n_q):
            center = truth.y[i] + bias + rng.normal(0, 3)
            for lv in LEVELS:
                rows.append({"model_id": m, "round_id": "r", "question_id": f"q{i}",
                             "output_type": "quantile", "output_type_id": lv,
                             "value": center + spread * _norm_ppf(lv)})
    return pd.DataFrame(rows), truth


def test_relative_skill_orders_models():
    pred, truth = synthetic()
    scores = score_quantile_frame(pred, truth)
    rs = relative_skill(scores, "fc-baseline", min_shared=10)
    assert rs["good"] < rs["fc-baseline"] == 1.0 < rs["bad"]
    boot = bootstrap_relative_skill(scores, "fc-baseline", n_boot=50, min_shared=10)
    assert boot.loc["good", "hi95"] < 1.0
    assert boot.loc["bad", "lo95"] > 1.0


def test_contribution_positive_for_informative_model():
    pred, truth = synthetic()
    ens = ensemble_median(pred)
    assert set(ens["model_id"]) == {"hub-ensemble"}
    c = contribution(pred, truth, score_quantile_frame)
    assert c["good"] > c["bad"]
