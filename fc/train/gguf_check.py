"""GGUF metadata checks for the Olmo-3 entry (standard library only).

This module runs inside venv-convert, which lacks the repo's dependencies, so it imports
nothing outside the standard library at module level (``gguf`` is imported lazily by
``read_metadata``). ``EXPECT`` comes from the metadata of the probe GGUF recorded in
04.1-RESEARCH "GGUF Export"; a GGUF made by our own converter must match it, and its chat
template must hash equal to the base snapshot's ``chat_template.jinja``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

# Written as a literal: tests assert it equals fc.model_identity.DEFAULT_TIER_S_BUDGET_BYTES.
MAX_BYTES = 11 * 1024**3

LLAMA_CPP_SRC = Path("/mnt/d/fc-train/src/llama.cpp-4df29be")

EXPECT: dict[str, Any] = {
    "general.architecture": "olmo2",
    "olmo2.block_count": 32,
    "olmo2.attention.head_count_kv": 32,
    "olmo2.attention.sliding_window": 4096,
    "olmo2.context_length": 65536,
    "olmo2.rope.scaling.type": "yarn",
    "olmo2.rope.scaling.factor": 8.0,
    "olmo2.rope.scaling.original_context_length": 8192,
    "tokenizer.ggml.eos_token_id": 100257,
    "general.file_type": 15,  # MOSTLY_Q4_K_M
    "olmo2.attention.sliding_window_pattern": [True, True, True, False] * 8,
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_metadata(md: dict[str, Any], *, size_bytes: int, template_sha256: str) -> list[str]:
    """Return a list of problems (empty when the GGUF is acceptable)."""
    problems: list[str] = []
    for key, want in EXPECT.items():
        if key not in md:
            problems.append(f"{key}: missing (expected {want!r})")
            continue
        got = md[key]
        if isinstance(want, list):
            got = [bool(x) for x in got]
        if got != want:
            shown = f"list of {len(got)}" if isinstance(got, list) else repr(got)
            wanted = f"list of {len(want)}" if isinstance(want, list) else repr(want)
            problems.append(f"{key}: {shown} != expected {wanted}")
    if size_bytes > MAX_BYTES:
        problems.append(f"size {size_bytes} bytes exceeds the {MAX_BYTES} byte budget")
    tmpl = md.get("tokenizer.chat_template")
    if not tmpl:
        problems.append("tokenizer.chat_template: missing")
    elif sha256_text(tmpl) != template_sha256:
        problems.append("tokenizer.chat_template: sha256 differs from the base chat_template.jinja")
    return problems


def _field_value(field: Any) -> Any:
    """Convert one GGUFReader field to a Python value (str, scalar or list of scalars)."""
    import numpy as np
    from gguf import GGUFValueType

    types = field.types
    if types[0] == GGUFValueType.ARRAY:
        inner = types[-1]
        out = []
        for idx in field.data:
            part = field.parts[idx]
            if inner == GGUFValueType.STRING:
                out.append(bytes(part).decode("utf-8"))
            else:
                out.append(part.tolist()[0] if isinstance(part, np.ndarray) else part)
        if types[-1] == GGUFValueType.BOOL:
            out = [bool(x) for x in out]
        return out
    part = field.parts[field.data[0]]
    if types[0] == GGUFValueType.STRING:
        return bytes(part).decode("utf-8")
    val = part.tolist()[0]
    return bool(val) if types[0] == GGUFValueType.BOOL else val


def read_metadata(path: str | Path) -> dict[str, Any]:
    """All key/value metadata of a GGUF as Python values (tensor data is not read)."""
    try:
        from gguf import GGUFReader
    except ImportError:
        sys.path.insert(0, str(LLAMA_CPP_SRC / "gguf-py"))
        from gguf import GGUFReader
    reader = GGUFReader(str(path), "r")
    return {name: _field_value(f) for name, f in reader.fields.items()}


def check_file(gguf: str | Path, template: str | Path) -> tuple[list[str], dict[str, Any]]:
    gguf = Path(gguf)
    md = read_metadata(gguf)
    sha = sha256_text(Path(template).read_text(encoding="utf-8"))
    problems = check_metadata(md, size_bytes=gguf.stat().st_size, template_sha256=sha)
    return problems, md


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="check an Olmo-3 GGUF against the expected metadata")
    ap.add_argument("gguf")
    ap.add_argument("--template", required=True, help="base chat_template.jinja")
    ap.add_argument("--json", help="write {'ok', 'problems'} here")
    args = ap.parse_args(argv)
    problems, _ = check_file(args.gguf, args.template)
    if args.json:
        Path(args.json).write_text(json.dumps({"ok": not problems, "problems": problems}) + "\n")
    if problems:
        print("gguf problems:")
        for p in problems:
            print(" -", p)
        return 1
    print("gguf ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
