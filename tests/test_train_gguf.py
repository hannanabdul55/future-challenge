"""gguf_check: the expected-metadata table, each single mutation, and the real probe file."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

from fc.model_identity import DEFAULT_TIER_S_BUDGET_BYTES
from fc.train import gguf_check as G

TEMPLATE = "{{ messages }}"
TSHA = hashlib.sha256(TEMPLATE.encode()).hexdigest()
PROBE = Path("/mnt/d/fc-train/probe/olmo3-7b-instruct-Q4_K_M.gguf")


def good():
    md = copy.deepcopy(G.EXPECT)
    md["tokenizer.chat_template"] = TEMPLATE
    return md


def check(md, size=4 * 1024**3, sha=TSHA):
    return G.check_metadata(md, size_bytes=size, template_sha256=sha)


def test_max_bytes_equals_the_runner_budget():
    assert G.MAX_BYTES == DEFAULT_TIER_S_BUDGET_BYTES


def test_expect_table_values():
    e = G.EXPECT
    assert e["general.architecture"] == "olmo2" and e["olmo2.block_count"] == 32
    assert e["olmo2.attention.sliding_window"] == 4096
    assert e["olmo2.attention.sliding_window_pattern"] == [True, True, True, False] * 8
    assert e["general.file_type"] == 15 and e["tokenizer.ggml.eos_token_id"] == 100257


def test_a_dict_built_from_expect_is_accepted():
    assert check(good()) == []


@pytest.mark.parametrize("key,value,needle", [
    ("general.architecture", "olmo3", "general.architecture"),
    ("olmo2.attention.sliding_window", 2048, "sliding_window"),
    ("olmo2.attention.sliding_window_pattern", [True, True, True, False] * 7 + [True, True, True],
     "sliding_window_pattern"),
    ("general.file_type", 7, "file_type"),
    ("olmo2.rope.scaling.factor", 4.0, "scaling.factor"),
    ("tokenizer.ggml.eos_token_id", 100265, "eos_token_id"),
])
def test_each_single_mutation_is_flagged(key, value, needle):
    md = good()
    md[key] = value
    problems = check(md)
    assert len(problems) == 1 and needle in problems[0]


def test_a_pattern_of_length_31_is_flagged():
    md = good()
    md["olmo2.attention.sliding_window_pattern"] = ([True, True, True, False] * 8)[:31]
    assert len(check(md)) == 1


def test_oversize_is_flagged():
    problems = check(good(), size=G.MAX_BYTES + 1)
    assert len(problems) == 1 and "exceeds" in problems[0]
    assert check(good(), size=G.MAX_BYTES) == []


def test_missing_chat_template_is_flagged():
    md = good()
    del md["tokenizer.chat_template"]
    problems = check(md)
    assert len(problems) == 1 and "chat_template" in problems[0]


def test_template_hash_mismatch_is_flagged():
    problems = check(good(), sha="0" * 64)
    assert len(problems) == 1 and "sha256" in problems[0]


def test_missing_key_is_flagged():
    md = good()
    del md["olmo2.block_count"]
    assert any("block_count" in p for p in check(md))


def test_module_imports_only_the_standard_library():
    src = Path(G.__file__).read_text()
    top = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
    allowed = {"__future__", "argparse", "hashlib", "json", "sys", "pathlib", "typing"}
    assert {ln.split()[1].split(".")[0] for ln in top} <= allowed


def _gguf_available():
    try:
        import gguf  # noqa: F401
        return True
    except ImportError:
        return (G.LLAMA_CPP_SRC / "gguf-py").is_dir()


@pytest.mark.skipif(not (PROBE.exists() and _gguf_available()),
                    reason="probe GGUF or gguf module not present")
def test_probe_file_matches_expect():
    md = G.read_metadata(PROBE)
    assert md["general.architecture"] == "olmo2"
    for key, want in G.EXPECT.items():
        assert md[key] == want, key
    assert md["tokenizer.chat_template"]
