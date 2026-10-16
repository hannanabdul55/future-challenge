"""SCORE-01/02: mixed-type scoring is proper per type, the season baselines validate, and
relative skill runs over (day, question) pairs."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from fc import baselines as B
from fc import questions as Q
from fc.eval import bootstrap_relative_skill, ensemble_mean_probs, score_frame
from fc.validate import validate_frame

RACES = Path(__file__).resolve().parents[1] / "questions" / "2026-us-midterms" / "races.csv"
QS = Q.generate_midterms(RACES)
RC = Q.to_tasks_round(QS, round_id="r1", T_open="x", T_commit="y", T_reveal="z")


def test_baselines_validate_and_cover_every_question():
    df = B.season_baselines(QS, "r1")
    for m in ("fc-uniform", "fc-baseline"):
        sub = df[df.model_id == m].drop(columns="model_id")
        rep = validate_frame(sub, RC)
        assert rep.ok, (m, str(rep))
        assert not rep.warnings  # every core question answered
    ga = df[(df.model_id == "fc-baseline") & (df.question_id == "senate-ga")]
    assert ga.set_index("output_type_id")["value"]["R"] == 0.85  # 2024 presidential winner


def test_score_frame_is_proper_per_type():
    truth = pd.DataFrame({"question_id": ["control-house", "senate-ga", "seats-house-d"],
                          "y": [1, "D", 221]})
    good = B.season_baselines(QS, "r1")
    good = good[good.question_id.isin(truth.question_id)]
    sharp = good[good.model_id == "fc-baseline"].copy()
    sharp["model_id"] = "sharp"
    # A model that puts more mass on the truth must score better (lower) on log score.
    sharp.loc[(sharp.question_id == "senate-ga") & (sharp.output_type_id == "D"), "value"] = 0.9
    sharp.loc[(sharp.question_id == "senate-ga") & (sharp.output_type_id == "R"), "value"] = 0.09
    sharp.loc[sharp.question_id == "control-house", "value"] = 0.8
    scores = score_frame(pd.concat([good, sharp]), truth)
    s = scores.set_index(["model_id", "question_id"])["score"]
    assert s["sharp", "senate-ga"] < s["fc-baseline", "senate-ga"]
    assert s["sharp", "control-house"] < s["fc-baseline", "control-house"]
    assert np.isclose(s["sharp", "control-house"], -np.log(0.8))
    assert set(scores.output_type) == {"binary", "categorical", "quantile"}
    assert "round_id" in scores.columns


def test_relative_skill_over_days_with_block_bootstrap():
    rows = []
    rng = np.random.default_rng(1)
    truth = pd.DataFrame({"question_id": [q["question_id"] for q in QS],
                          "y": [1 if q["output_type"] == "binary" else
                                (q.get("pres_2024", "D") if q["output_type"] == "categorical"
                                 else 220) for q in QS]})
    for day in ("d1", "d2", "d3"):
        b = B.season_baselines(QS, day)
        good = b[b.model_id == "fc-baseline"].copy()
        good["model_id"] = "good"
        jitter = good.output_type.isin(["binary", "categorical"])
        good.loc[jitter, "value"] = (good.loc[jitter, "value"] * 0.9
                                     + 0.1 * rng.uniform(0.4, 0.6, jitter.sum()))
        rows.append(pd.concat([b, good]))
    pred = pd.concat(rows, ignore_index=True)
    scores = score_frame(pred, truth)
    scores["question_id"] = scores["round_id"] + "/" + scores["question_id"]  # (day, question)
    block = pd.Series({qid: qid.split("/")[0] for qid in scores.question_id.unique()})
    rs = bootstrap_relative_skill(scores, "fc-baseline", n_boot=30, block=block, min_shared=20)
    assert set(rs.index) == {"fc-uniform", "fc-baseline", "good"}
    assert rs.loc["fc-baseline", "rel_skill"] == 1.0
    assert rs.loc["fc-uniform", "rel_skill"] > 1.0  # ignorance loses to persistence here


def test_probability_ensemble_renormalises():
    b = B.season_baselines(QS, "r1")
    e = ensemble_mean_probs(b)
    rep = validate_frame(e.drop(columns="model_id"), RC)
    assert rep.ok, str(rep)
