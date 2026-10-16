"""RUN-04: prompts are built from the pinned template; answers parse forgivingly and fail
closed — a bad answer is missing, never guessed."""

from __future__ import annotations

import json
from pathlib import Path

from fc import forecast_io as F
from fc import questions as Q
from fc.validate import validate_frame

RACES = Path(__file__).resolve().parents[1] / "questions" / "2026-us-midterms" / "races.csv"
QS = Q.generate_midterms(RACES)
BY_ID = {q["question_id"]: q for q in QS}
RC = Q.to_tasks_round(QS, round_id="d", T_open="x", T_commit="y", T_reveal="z")


def test_prompt_contains_packet_questions_and_formats():
    t = F.load_template()
    qs = [BY_ID["control-house"], BY_ID["senate-ga"], BY_ID["seats-house-d"]]
    p = F.build_prompt(t, packet_md="PACKET-TEXT", questions=qs, day="2026-10-26")
    assert "PACKET-TEXT" in p and "2026-10-26" in p
    assert '"control-house"' in p and '"senate-ga"' in p and '"seats-house-d"' in p
    assert '"D": <prob>' in p and '"0.5": <value>' in p and "[0, 435]" in p


def test_batches_are_homogeneous_by_family():
    bs = F.batches(QS, max_per_batch=20)
    assert all(len({q["family"] for q in b}) == 1 for b in bs)
    assert sum(len(b) for b in bs) == len(QS)
    assert max(len(b) for b in bs) <= 20


def test_parse_tolerates_fences_prose_percent_and_trailing_commas():
    text = """Here is my forecast:
```json
{"control-house": {"p": "42%"},
 "senate-ga": {"D": 0.55, "R": "0.43", "Other": 0.02,},
 "seats-house-d": {"0.025": 200, "0.1": "206", "0.25": 212, "0.4": 216, "0.5": 218,
                   "0.6": 220, "0.75": 224, "0.9": 229, "0.975": 231}}
```
Good luck."""
    qs = [BY_ID["control-house"], BY_ID["senate-ga"], BY_ID["seats-house-d"]]
    parsed = F.parse_answer(text, qs, round_id="d")
    assert parsed.missing == {}
    df = parsed.frame()
    assert validate_frame(df, RC).ok
    assert df[df.question_id == "control-house"]["value"].iloc[0] == 0.42
    assert abs(df[df.question_id == "senate-ga"]["value"].sum() - 1) < 1e-9


def test_parse_fails_closed():
    qs = [BY_ID["control-house"], BY_ID["senate-ga"], BY_ID["seats-house-d"], BY_ID["senate-tx"]]
    text = ('{"control-house": {"p": 1.4}, "senate-ga": {"D": 0.9, "R": 0.9, "Other": 0},'
            ' "seats-house-d": {"0.5": 218}}')
    parsed = F.parse_answer(text, qs, round_id="d")
    assert set(parsed.missing) == {"control-house", "senate-ga", "seats-house-d", "senate-tx"}
    assert parsed.rows == []
    assert parsed.parse_rate == 0.0
    assert F.parse_answer("no json here", qs, round_id="d").missing


def test_quantiles_are_made_monotone_and_clipped():
    q = BY_ID["seats-senate-dcaucus"]
    ans = {str(lv): v for lv, v in zip(q["quantile_levels"], [44, 46, 45, 47, 48, 48, 49, 50, 101])}
    text = json.dumps({q["question_id"]: ans})
    parsed = F.parse_answer(text, [q], round_id="d")
    vals = parsed.frame()["value"].tolist()
    assert vals == sorted(vals) and vals[-1] == 100 and vals[2] == 46


def test_categorical_renormalises_small_drift():
    q = BY_ID["governor-wi"]
    parsed = F.parse_answer('{"governor-wi": {"D": 0.5, "R": 0.47, "Other": 0.01}}', [q],
                            round_id="d")
    assert parsed.missing == {}
    assert abs(parsed.frame()["value"].sum() - 1) < 1e-9
