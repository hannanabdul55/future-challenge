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


# --------------------------------------------------------------------------- think tags / last answer

IDS = ["control-house", "control-senate"]


def test_extract_json_reads_only_after_the_last_think_close():
    text = ('reasoning {"control-house": {"p": 0.2}} </think> final '
            '{"control-house": {"p": 0.6}}')
    assert F.extract_json(text, IDS)["control-house"]["p"] == 0.6
    only_draft = 'thinking {"control-house": {"p": 0.2}} </think> no answer here'
    assert F.extract_json(only_draft, IDS) is None


def test_extract_json_prefers_the_last_object_with_an_expected_id():
    text = ('{"control-house": {"p": 0.1}} then {"control-house": {"p": 0.9}} '
            'and finally {"note": 1}')
    assert F.extract_json(text, IDS)["control-house"]["p"] == 0.9
    assert F.extract_json(text) == {"note": 1}  # no expected ids: last object of any kind
    fenced = 'x ```json\n{"control-house": {"p": 0.4,},}\n```'
    assert F.extract_json(fenced, IDS)["control-house"]["p"] == 0.4


def test_extract_json_skips_prose_braces():
    text = 'use {S} here and {"control-house": {"p": 0.3}}'
    assert F.extract_json(text, IDS)["control-house"]["p"] == 0.3


def test_binary_accepts_a_bare_number_or_percent():
    qs = [BY_ID["control-house"]]
    p = F.parse_answer('{"control-house": 0.68}', qs, round_id="d")
    assert p.rows[0]["value"] == 0.68
    p = F.parse_answer('{"control-house": "68%"}', qs, round_id="d")
    assert abs(p.rows[0]["value"] - 0.68) < 1e-12
    p = F.parse_answer('{"control-house": 1.4}', qs, round_id="d")
    assert not p.rows and "control-house" in p.missing


def test_parse_answer_takes_the_post_think_answer():
    qs = [BY_ID["control-house"]]
    text = '<think>{"control-house": {"p": 0.2}}</think>\n{"control-house": {"p": 0.7}}'
    assert F.parse_answer(text, qs, round_id="d").rows[0]["value"] == 0.7
