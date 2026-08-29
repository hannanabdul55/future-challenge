import numpy as np
import pandas as pd

from fc.validate import validate_frame

RC = {
    "round_id": "2026-w37",
    "quantile_levels": [0.1, 0.5, 0.9],
    "questions": [
        {"question_id": "a", "output_type": "quantile", "core": True},
        {"question_id": "b", "output_type": "binary", "core": True},
    ],
}


def frame(vals=(1, 2, 3), qid="a"):
    return pd.DataFrame({
        "round_id": ["2026-w37"] * 3, "question_id": [qid] * 3, "output_type": ["quantile"] * 3,
        "output_type_id": [0.1, 0.5, 0.9], "value": list(vals),
    })


def test_ok_with_core_warning():
    r = validate_frame(frame(), RC)
    assert r.ok and r.warnings and "core" in r.warnings[0]


def test_non_monotone_rejected():
    assert not validate_frame(frame((3, 2, 1)), RC).ok


def test_unknown_question_and_bad_levels():
    assert not validate_frame(frame(qid="zzz"), RC).ok
    f = frame(); f.loc[0, "output_type_id"] = 0.05
    assert not validate_frame(f, RC).ok


def test_binary_probability_range():
    f = pd.DataFrame({"round_id": ["2026-w37"], "question_id": ["b"], "output_type": ["binary"],
                      "output_type_id": [np.nan], "value": [1.5]})
    assert not validate_frame(f, RC).ok
    f["value"] = 0.3
    assert validate_frame(f, RC).ok
