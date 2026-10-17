"""compare() in fc.train.template_parity (default suite) plus the recorded-parity check."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fc.train.template_parity import EXPECTED_EOS_ID, compare, sample_prompts

PARITY = Path("/mnt/d/fc-train/parity")


def _rec(rendered="<|user|>hi<|assistant|>", ids=(1, 2, 3), eos=EXPECTED_EOS_ID):
    return {"rendered": rendered, "ids": list(ids), "eos_id": eos}


def test_identical_records_pass():
    assert compare([_rec(), _rec("x", (9,))], [_rec(), _rec("x", (9,))]) == []


def test_one_character_render_difference_is_caught():
    problems = compare([_rec()], [_rec(rendered="<|user|>hi<|assistant|> ")])
    assert len(problems) == 1 and "rendered text differs" in problems[0]


def test_one_token_id_difference_is_caught():
    problems = compare([_rec()], [_rec(ids=(1, 2, 4))])
    assert len(problems) == 1 and "position 2" in problems[0]


def test_wrong_eos_id_is_caught():
    problems = compare([_rec()], [_rec(eos=100256)])
    assert len(problems) == 1 and "llama eos id 100256" in problems[0]


def test_record_count_mismatch_is_caught():
    assert compare([_rec()], [_rec(), _rec()])


def test_sample_prompts_has_tracer_plus_two_synthetic(tmp_path):
    f = tmp_path / "prompt.txt"
    f.write_text("tracer prompt")
    prompts = sample_prompts(f)
    assert len(prompts) == 3 and prompts[0] == "tracer prompt"
    joined = "".join(prompts[1:])
    for needle in ("—", "…", "é", "(D)", "\t", "\n\n", '{"'):
        assert needle in joined


@pytest.mark.skipif(
    not ((PARITY / "hf.json").exists() and (PARITY / "llama.json").exists()),
    reason="parity records not generated on this box")
def test_recorded_parity():
    hf = json.loads((PARITY / "hf.json").read_text(encoding="utf-8"))
    ll = json.loads((PARITY / "llama.json").read_text(encoding="utf-8"))
    assert compare(hf, ll) == []
