"""RUN-01 (quick task 261016-fc2, finding 5): the runner's ``generate`` honours the frozen
thinking setting. Offline: the module is loaded from its file, the llm is a fake, and the
chat renderer is injected so llama_cpp is never imported."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "runner" / "forecast" / "forecast.py"
_spec = importlib.util.spec_from_file_location("fc_runner_forecast_under_test", SCRIPT)
RUNNER = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RUNNER)


class FakeLlm:
    def __init__(self, template: str):
        self.metadata = {"tokenizer.chat_template": template} if template is not None else {}
        self.calls: list[tuple[str, dict]] = []
        self.tok_kwargs: dict = {}

    def tokenize(self, data: bytes, **kw):
        self.tok_kwargs = kw
        return list(data)

    def create_completion(self, **kw):
        self.calls.append(("completion", kw))
        return {"choices": [{"text": "answer"}], "usage": {"completion_tokens": 1}}

    def create_chat_completion(self, **kw):
        self.calls.append(("chat", kw))
        return {"choices": [{"message": {"content": "chat answer"}}], "usage": {}}


def _render(text, stop=("<|im_end|>",)):
    return lambda llm, tmpl, messages: (text, list(stop))


def _decoded(llm: FakeLlm) -> str:
    kind, kw = llm.calls[0]
    assert kind == "completion"
    return bytes(kw["prompt"]).decode()


def test_template_without_think_uses_plain_chat_completion():
    llm = FakeLlm("{{ messages }}")
    text, _, info = RUNNER.generate(llm, "hi", thinking=False, max_tokens=64, seed=3)
    assert text == "chat answer" and info == {"mode": "chat", "think_prefill": False}
    (kind, kw), = llm.calls
    assert kind == "chat" and kw["temperature"] == 0.0 and kw["max_tokens"] == 64
    assert kw["seed"] == 3 and kw["messages"] == [{"role": "user", "content": "hi"}]


def test_thinking_on_keeps_chat_path_even_for_think_templates():
    llm = FakeLlm("... <think> ...")
    _, _, info = RUNNER.generate(llm, "hi", thinking=True, max_tokens=8, seed=1,
                                 render=_render("never used"))
    assert info["mode"] == "chat" and llm.calls[0][0] == "chat"


def test_thinking_off_prefills_exactly_one_empty_think_block():
    for rendered in ("<|im_start|>assistant\n<think>\n\n</think>\n\n",
                     "<|im_start|>assistant\n<think>\n",
                     "<|im_start|>assistant\n"):
        llm = FakeLlm("... <think> ...")
        text, _, info = RUNNER.generate(llm, "hi", thinking=False, max_tokens=8, seed=1,
                                        render=_render(rendered))
        prompt = _decoded(llm)
        assert prompt.endswith("assistant\n<think>\n\n</think>\n\n"), rendered
        assert prompt.count("</think>") == 1 and prompt.count("<think>") == 1
        assert llm.tok_kwargs == {"add_bos": False, "special": True}
        assert info == {"mode": "prefill", "think_prefill": True} and text == "answer"
        kw = llm.calls[0][1]
        assert kw["temperature"] == 0.0 and kw["stop"] == ["<|im_end|>"]


# --------------------------------------------------------------------------- main()

class _MainLlama:
    """Stands in for llama_cpp.Llama: answers every question id found in the prompt."""

    def __init__(self, **kw):
        self.metadata = {}

    def create_chat_completion(self, *, messages, **kw):
        ids = re.findall(r'^- "([^"]+)":', messages[0]["content"], flags=re.MULTILINE)
        body = {i: {"p": 0.5} for i in ids}
        return {"choices": [{"message": {"content": json.dumps(body)}}], "usage": {}}


def _binary(qid: str, family: str) -> dict:
    return {"question_id": qid, "family": family, "output_type": "binary",
            "statement": f"Will {qid} happen?"}


def _stage(tmp_path: Path, monkeypatch) -> Path:
    qs = [_binary("control-house", "control"), _binary("control-senate", "control"),
          _binary("seats-x", "seats")]
    (tmp_path / "model").mkdir()
    (tmp_path / "model" / "m.gguf").write_bytes(b"")
    (tmp_path / "questions.json").write_text(json.dumps({"questions": qs}))
    (tmp_path / "packet.md").write_text("## Overview\nrows\n")
    (tmp_path / "template.md").write_text("Day {{DAY}}\n{{PACKET}}\n{{QUESTIONS}}\n")
    out = tmp_path / "out"
    monkeypatch.setattr(RUNNER, "MODEL_DIR", tmp_path / "model")
    monkeypatch.setattr(RUNNER, "PACKET", tmp_path / "packet.md")
    monkeypatch.setattr(RUNNER, "QUESTIONS", tmp_path / "questions.json")
    monkeypatch.setattr(RUNNER, "TEMPLATE", tmp_path / "template.md")
    monkeypatch.setattr(RUNNER, "OUT", out)
    fake = types.ModuleType("llama_cpp")
    fake.Llama = _MainLlama
    monkeypatch.setitem(sys.modules, "llama_cpp", fake)
    monkeypatch.setenv("FC_ROUND_ID", "r1")
    monkeypatch.setenv("FC_DAY", "2026-11-24")
    monkeypatch.setenv("FC_WEIGHTS_FILE", "m.gguf")
    monkeypatch.delenv("FC_BATCH", raising=False)
    return out


@pytest.mark.parametrize("batch,lines", [(None, 3), ("20", 2)])
def test_main_prompts_one_question_at_a_time_unless_fc_batch_says_otherwise(
        tmp_path, monkeypatch, batch, lines):
    out = _stage(tmp_path, monkeypatch)
    if batch is not None:
        monkeypatch.setenv("FC_BATCH", batch)
    assert RUNNER.main() == 0
    recs = [json.loads(x) for x in (out / "transcript.jsonl").read_text().splitlines()]
    assert len(recs) == lines
    status = json.loads((out / "status.json").read_text())
    assert status["questions_per_prompt"] == (1 if batch is None else 20)
    assert status["n_missing"] == 0 and status["parse_rate"] == 1.0
