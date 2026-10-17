"""Token-level parity between the HF chat template (training) and llama-cpp-python (runtime).

SFT prompts are rendered and tokenised by the Hugging Face tokenizer in the training venv;
at inference the runner feeds the GGUF through llama-cpp-python's chat path
(runner/forecast/forecast.py generate(): a template without "<think>" goes to
``create_chat_completion``). If the two disagree on a single character or token, the model is
trained on prompts it never sees at runtime (RESEARCH Pattern 4, Pitfall 2).

Import-light: transformers and llama_cpp are imported inside the functions that need them, so
``compare`` and ``sample_prompts`` run in any interpreter, including the default test suite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

EXPECTED_EOS_ID = 100257  # <|endoftext|> for Olmo-3

Record = dict[str, Any]

# Two synthetic prompts that exercise characters the tracer prompt may not: em dash, ellipsis,
# accented letters, a "Name (D)" candidate header, tabs, blank lines and a JSON fragment.
SYNTHETIC_PROMPTS = (
    (
        "Race: Jos\u00e9 Pe\u00f1a (D) \u2014 Zo\u00eb O\u2019Brien (R)\u2026\n"
        "\n"
        "Incumbent:\tJos\u00e9 Pe\u00f1a (D)\n"
        "Challenger:\tZo\u00eb O\u2019Brien (R)\n"
        "\n"
        "\n"
        'Return JSON like {"p_dem": 0.55, "note": "caf\u00e9 \u2014 na\u00efve"}.'
    ),
    (
        "  Name (D)\n"
        "\u2026 leading by 3 points \u2014 see poll:\n"
        '\t{"pollster": "S\u00e3o Paulo Survey", "n": 812, "margin": [-3.0, 3.0]}\n'
        "\n"
        "Q: \u00bfWho wins? \u2014 answer with a probability.\n"
    ),
)


def sample_prompts(prompt_file: str | Path) -> list[str]:
    """The tracer prompt followed by the two synthetic prompts."""
    tracer = Path(prompt_file).read_text(encoding="utf-8")
    return [tracer, *SYNTHETIC_PROMPTS]


def hf_side(snapshot_dir: str | Path, prompts: list[str]) -> list[Record]:
    """Render and tokenise ``prompts`` with the HF tokenizer's chat template (training side)."""
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(str(snapshot_dir), trust_remote_code=False)
    out: list[Record] = []
    for p in prompts:
        rendered = tok.apply_chat_template(
            [{"role": "user", "content": p}], add_generation_prompt=True, tokenize=False)
        ids = tok(rendered, add_special_tokens=False)["input_ids"]
        out.append({"rendered": rendered, "ids": list(ids), "eos_id": tok.eos_token_id})
    return out


def llama_side(gguf_path: str | Path, prompts: list[str]) -> list[Record]:
    """Render and tokenise ``prompts`` the way llama-cpp-python 0.3.35's chat path does.

    Mirrors Llama.__init__ (default handler built from ``tokenizer.chat_template`` with the
    GGUF's eos/bos token texts and ``stop_token_ids=[eos_id]``) and
    chat_formatter_to_chat_completion_handler (``tokenize(add_bos=not added_special,
    special=True)``).
    """
    from llama_cpp import Llama
    from llama_cpp.llama_chat_format import Jinja2ChatFormatter

    llm = Llama(model_path=str(gguf_path), vocab_only=True, verbose=False)
    tmpl = (llm.metadata or {}).get("tokenizer.chat_template")
    if not tmpl:
        raise ValueError(f"{gguf_path} has no tokenizer.chat_template metadata")
    eos_id, bos_id = llm.token_eos(), llm.token_bos()
    eos = llm._model.token_get_text(eos_id) if eos_id != -1 else ""
    bos = llm._model.token_get_text(bos_id) if bos_id != -1 else ""
    fmt = Jinja2ChatFormatter(template=tmpl, eos_token=eos, bos_token=bos,
                              stop_token_ids=[eos_id])
    out: list[Record] = []
    for p in prompts:
        res = fmt(messages=[{"role": "user", "content": p}])
        ids = llm.tokenize(res.prompt.encode("utf-8"), add_bos=not res.added_special,
                           special=True)
        out.append({"rendered": res.prompt, "ids": [int(i) for i in ids], "eos_id": int(eos_id)})
    return out


def _context(s: str, i: int, width: int = 40) -> str:
    return repr(s[max(0, i - width):i + width])


def compare(hf: list[Record], ll: list[Record]) -> list[str]:
    """Problems found between the HF and llama records (empty list means parity)."""
    problems: list[str] = []
    if len(hf) != len(ll):
        return [f"record count differs: hf {len(hf)} vs llama {len(ll)}"]
    for n, (a, b) in enumerate(zip(hf, ll, strict=True)):
        ra, rb = a["rendered"], b["rendered"]
        if ra != rb:
            i = next((k for k, (x, y) in enumerate(zip(ra, rb, strict=False)) if x != y),
                     min(len(ra), len(rb)))
            problems.append(
                f"prompt {n}: rendered text differs at index {i}: "
                f"hf {_context(ra, i)} vs llama {_context(rb, i)}")
        ia, ib = list(a["ids"]), list(b["ids"])
        if ia != ib:
            i = next((k for k, (x, y) in enumerate(zip(ia, ib, strict=False)) if x != y),
                     min(len(ia), len(ib)))
            problems.append(
                f"prompt {n}: token ids differ at position {i}: "
                f"hf {ia[i:i + 5]} vs llama {ib[i:i + 5]} (lengths {len(ia)} vs {len(ib)})")
        for side, rec in (("hf", a), ("llama", b)):
            if rec["eos_id"] != EXPECTED_EOS_ID:
                problems.append(
                    f"prompt {n}: {side} eos id {rec['eos_id']} != {EXPECTED_EOS_ID}")
    return problems
