"""run_day with a fake forecaster: tree, path rewrites, env and chat-mode assertion."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from fc.train import evalrun

FAKE = textwrap.dedent('''
    import json, os, sys
    from pathlib import Path

    MODEL_DIR = Path("/model")
    PACKET = Path("/packet/packet.md")
    QUESTIONS = Path("/questions/questions.json")
    TEMPLATE = Path("/prompt/template.md")
    OUT = Path("/out")

    OUT.mkdir(parents=True, exist_ok=True)
    seen = {k: os.environ.get(k) for k in (
        "FC_ROUND_ID", "FC_DAY", "FC_SEED", "FC_WEIGHTS_FILE", "FC_CTX", "FC_MAX_TOKENS",
        "FC_BATCH", "FC_THINKING")}
    status = {"env": seen, "script": __file__, "model_exists": (MODEL_DIR / seen["FC_WEIGHTS_FILE"]).exists(),
              "packet": PACKET.read_text(), "questions": json.loads(QUESTIONS.read_text()),
              "template": TEMPLATE.read_text(), "model_dir": str(MODEL_DIR),
              "isolated": sys.flags.isolated,
              "parse_rate": 1.0, "n_missing": 0,
              "batches": [{"batch": 0, "family": "senate", "seconds": 1.0, "parse_rate": 1.0,
                           "error": None, "mode": "chat", "think_prefill": False}]}
    (OUT / "forecast.csv").write_text("question_id\\n")
    (OUT / "status.json").write_text(json.dumps(status))
''')


@pytest.fixture
def world(tmp_path):
    gdir = tmp_path / "gguf"
    gdir.mkdir()
    (gdir / "m-Q4_K_M.gguf").write_bytes(b"GGUF")
    packet = tmp_path / "packet"
    packet.mkdir()
    (packet / "packet.md").write_text("PACKET")
    qdir = tmp_path / "q"
    qdir.mkdir()
    (qdir / "questions.json").write_text(json.dumps({"questions": [{"question_id": "a"}]}))
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "forecaster-v1.md").write_text("TEMPLATE")
    script = tmp_path / "fake_forecast.py"
    script.write_text(FAKE)
    return tmp_path, gdir / "m-Q4_K_M.gguf", packet, qdir / "questions.json", prompts, script


def run(world, **kw):
    tmp, gguf, packet, q, prompts, script = world
    tmp_root = tmp / "tmproot"
    status = evalrun.run_day(gguf, packet, q, tmp / "out", day="2022-10-31",
                             round_id="hist-2022-2022-10-31", python=sys.executable,
                             script=script, tmp_root=tmp_root, prompt_dir=prompts, **kw)
    return status, tmp_root


def test_the_tree_the_rewrites_and_the_env_reach_the_script(world):
    status, tmp_root = run(world)
    assert status["returncode"] == 0 and status["wall_s"] >= 0
    assert status["model_exists"] is True
    assert status["packet"] == "PACKET" and status["template"] == "TEMPLATE"
    assert status["questions"]["questions"][0]["question_id"] == "a"
    assert status["env"] == {
        "FC_ROUND_ID": "hist-2022-2022-10-31", "FC_DAY": "2022-10-31", "FC_SEED": "0",
        "FC_WEIGHTS_FILE": "m-Q4_K_M.gguf", "FC_CTX": "16384", "FC_MAX_TOKENS": "4096",
        "FC_BATCH": "20", "FC_THINKING": "0"}
    assert status["isolated"] == 1  # python -I
    # mkdtemp used tmp_root, and the tree has the five entries
    trees = list(tmp_root.glob("fc-eval-*"))
    assert len(trees) == 1
    tree = trees[0]
    assert {p.name for p in tree.iterdir()} >= {"model", "packet", "questions", "prompt", "out"}
    assert status["script"] == str(tree / "forecast_local.py")
    assert status["model_dir"] == f"{tree}/model"
    assert (tree / "out" / "forecast.csv").exists()


def test_all_five_path_replacements_are_applied():
    code = Path(evalrun.DEFAULT_SCRIPT).read_text()
    tree = Path("/tmp/xx")
    out = evalrun.rewrite_script(code, tree, "forecaster-v1.md")
    for old in ('Path("/model")', 'Path("/packet/packet.md")', 'Path("/questions/questions.json")',
                'Path("/prompt/template.md")', 'Path("/out")'):
        assert old in code and old not in out
    assert 'Path("/tmp/xx/prompt/forecaster-v1.md")' in out
    # nothing else changes: undoing the five replacements restores the original
    assert len(out.splitlines()) == len(code.splitlines())


def test_rewrites_match_fc_run_local():
    """The five replacements are the ones fc/run.py _run_local applies."""
    src = (Path(__file__).resolve().parents[1] / "fc" / "run.py").read_text()
    for frag in ('Path("/model")', 'Path("/packet/packet.md")',
                 'Path("/questions/questions.json")', 'Path("/prompt/template.md")',
                 'Path("/out")'):
        assert f".replace('{frag}'" in src


def test_assert_chat_mode_accepts_chat_batches():
    evalrun.assert_chat_mode({"batches": [{"mode": "chat", "think_prefill": False}] * 3})


@pytest.mark.parametrize("batch", [
    {"mode": "prefill", "think_prefill": True},
    {"mode": "chat", "think_prefill": True},
    {"mode": None, "think_prefill": False},
])
def test_assert_chat_mode_rejects_other_batches(batch):
    with pytest.raises(AssertionError):
        evalrun.assert_chat_mode({"batches": [{"mode": "chat", "think_prefill": False}, batch]})


def test_assert_chat_mode_rejects_no_batches():
    with pytest.raises(AssertionError):
        evalrun.assert_chat_mode({"batches": []})
