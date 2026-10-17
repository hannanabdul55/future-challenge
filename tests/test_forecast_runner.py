"""RUN-01 (quick task 261016-fc2, finding 5): the runner's ``generate`` honours the frozen
thinking setting. Offline: the module is loaded from its file, the llm is a fake, and the
chat renderer is injected so llama_cpp is never imported."""

from __future__ import annotations

import importlib.util
from pathlib import Path

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
