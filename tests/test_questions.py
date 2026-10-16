"""QSPEC-01/02: the Season 1 question set generates deterministically, validates, and the
reduced tasks round it produces drives `fc validate` for all three output types."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from fc import questions as Q
from fc.validate import validate_frame

RACES = Path(__file__).resolve().parents[1] / "questions" / "2026-us-midterms" / "races.csv"


def test_generate_season_one_set():
    qs = Q.generate_midterms(RACES)
    fam = pd.Series([q["family"] for q in qs]).value_counts().to_dict()
    assert fam == {"governor": 36, "senate": 35, "control": 2, "seats": 2}
    assert all(q["core"] for q in qs)
    assert Q.validate_questions(qs, event_date=Q.EVENT_DATE).ok
    assert Q.questions_hash(qs) == Q.questions_hash(list(reversed(qs)))  # order-independent
    # Statements name offices and parties, never a candidate.
    for q in qs:
        assert "nominee" in q["statement"] or q["family"] in ("control", "seats")


def test_validator_rejects_malformed_specs():
    qs = Q.generate_midterms(RACES)
    bad = dict(qs[0])
    bad["manipulation_risk"] = "none"
    rep = Q.validate_questions([bad])
    assert not rep.ok and any("manipulation_risk" in e for e in rep.errors)

    bad = json.loads(json.dumps(next(q for q in qs if q["output_type"] == "quantile")))
    bad["quantile_levels"] = [0.1, 0.5, 0.8]  # asymmetric
    assert not Q.validate_questions([bad]).ok

    bad = json.loads(json.dumps(next(q for q in qs if q["output_type"] == "categorical")))
    bad["categories"] = ["D"]
    assert not Q.validate_questions([bad]).ok

    dup = [qs[0], qs[0]]
    assert any("duplicate" in e for e in Q.validate_questions(dup).errors)

    late = json.loads(json.dumps(qs[0]))
    late["resolution"]["T_final"] = "2026-11-01"
    assert not Q.validate_questions([late], event_date=Q.EVENT_DATE).ok


def test_write_and_load_roundtrip(tmp_path: Path):
    qs = Q.generate_midterms(RACES)
    h = Q.write_question_set(tmp_path / "questions.json", qs, season=Q.SEASON)
    loaded = Q.load_questions(tmp_path / "questions.json")
    assert Q.questions_hash(loaded) == h
    assert json.loads((tmp_path / "questions.json").read_text())["questions_hash"] == h


def _rows(round_id, qid, otype, pairs):
    return [{"round_id": round_id, "question_id": qid, "output_type": otype,
             "output_type_id": k, "value": v} for k, v in pairs]


def test_reduced_tasks_round_drives_validate():
    qs = Q.generate_midterms(RACES)
    rc = Q.to_tasks_round(qs, round_id="d1", T_open="2026-10-26T00:00:00Z",
                          T_commit="2026-10-26T12:00:00Z", T_reveal="2026-10-26T18:00:00Z")
    levels = Q.SEAT_LEVELS
    good = (_rows("d1", "control-house", "binary", [("p", 0.55)])
            + _rows("d1", "senate-ga", "categorical", [("D", 0.6), ("R", 0.38), ("Other", 0.02)])
            + _rows("d1", "seats-house-d", "quantile",
                    [(str(lv), 200 + 40 * lv) for lv in levels]))
    rep = validate_frame(pd.DataFrame(good), rc)
    assert rep.ok, str(rep)
    assert any("core question(s) missing" in w for w in rep.warnings)

    bad_sum = _rows("d1", "senate-ga", "categorical", [("D", 0.6), ("R", 0.6), ("Other", 0.0)])
    assert any("sum to" in e for e in validate_frame(pd.DataFrame(bad_sum), rc).errors)

    bad_cats = _rows("d1", "senate-ga", "categorical", [("D", 0.5), ("R", 0.5)])
    assert any("categories" in e for e in validate_frame(pd.DataFrame(bad_cats), rc).errors)

    out_of_bounds = _rows("d1", "seats-house-d", "quantile",
                          [(str(lv), 400 + 100 * lv) for lv in levels])
    assert any("bounds" in e for e in validate_frame(pd.DataFrame(out_of_bounds), rc).errors)
