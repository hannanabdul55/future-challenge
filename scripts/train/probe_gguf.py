#!/usr/bin/env python3
"""Load a GGUF under llama-cpp-python and smoke-test it (tracked copy of the project skill
``gguf-runtime-load-probe``).

  python -I scripts/train/probe_gguf.py MODEL.gguf [N_CTX] [--json OUT]

Run it under the eval venv. It prints load seconds, the architecture, sliding-window and rope
keys, whether a chat template exists, a short JSON answer and the ~12k-token needle answer
(expected '7') with its timing. Verbose llama.cpp output stays on, so redirect it to a log and
grep ``offloaded N/N layers`` and ``prompt eval time`` from there. ``--json`` writes the
numbers the artifacts record needs, parsed from the verbose log through a stderr tee.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("gguf")
    ap.add_argument("n_ctx", nargs="?", type=int, default=16384)
    ap.add_argument("--json", help="write the probe numbers here")
    args = ap.parse_args(argv)

    # llama.cpp writes its verbose log to the C-level stderr; capture fd 2 in a file so the
    # timing lines can be parsed, then replay it.
    log = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115 - fd 2 points at it until the end
    saved = os.dup(2)
    sys.stderr.flush()
    os.dup2(log.fileno(), 2)

    from llama_cpp import Llama

    t0 = time.time()
    llm = Llama(model_path=args.gguf, n_gpu_layers=-1, n_ctx=args.n_ctx, seed=0, verbose=True)
    load_s = time.time() - t0
    print(f"load_s={load_s:.1f}", flush=True)

    md = llm.metadata
    print("arch", md.get("general.architecture"))
    for k, v in md.items():
        if any(s in k for s in ("context_length", "sliding", "rope")):
            print(k, v)
    has_tmpl = bool(md.get("tokenizer.chat_template"))
    print("has_chat_template", has_tmpl)

    r = llm.create_chat_completion(
        messages=[{"role": "user",
                   "content": 'Reply with JSON only: {"p": probability that a fair coin lands heads}'}],
        temperature=0.0, max_tokens=40)
    short = r["choices"][0]["message"]["content"]
    print("short:", repr(short))

    filler = " ".join(f"Line {i}: district {i % 50} shows candidate A at {40 + i % 15} percent."
                      for i in range(700))
    msg = filler + "\n\nQuestion: what number district appears in Line 7? Answer with just the number."
    n_tokens = len(llm.tokenize(msg.encode()))
    t = time.time()
    r = llm.create_chat_completion(messages=[{"role": "user", "content": msg}],
                                   temperature=0.0, max_tokens=10)
    needle_s = time.time() - t
    answer = r["choices"][0]["message"]["content"]
    print(f"long_tokens={n_tokens} seconds={needle_s:.1f} answer={answer!r}", flush=True)

    # generation speed: a short prompt so prefill is negligible, ~200 greedy tokens
    t = time.time()
    r = llm.create_chat_completion(
        messages=[{"role": "user",
                   "content": "Write the integers from 1 to 150 separated by commas, nothing else."}],
        temperature=0.0, max_tokens=200)
    gen_s = time.time() - t
    gen_n = r["usage"]["completion_tokens"]
    print(f"gen_tokens={gen_n} seconds={gen_s:.1f} tps={gen_n / gen_s:.1f}", flush=True)

    sys.stderr.flush()
    os.dup2(saved, 2)
    log.seek(0)
    text = log.read().decode("utf-8", "replace")
    sys.stderr.write(text)
    sys.stderr.flush()

    off = re.search(r"offloaded (\d+)/(\d+) layers", text)
    pe = re.findall(r"prompt eval time =\s*[\d.]+ ms /\s*(\d+) tokens .*?([\d.]+) tokens per second", text)
    out = {
        "arch": md.get("general.architecture"), "load_s": round(load_s, 1),
        "has_chat_template": has_tmpl, "short": short,
        "offloaded": f"{off.group(1)}/{off.group(2)}" if off else None,
        "needle_tokens": n_tokens, "needle_seconds": round(needle_s, 1), "needle_answer": answer,
        "needle_ok": "7" in answer.split(),
        # the long prompt is the last prompt-eval line / the longest one
        "prompt_eval_tps": float(max(pe, key=lambda x: int(x[0]))[1]) if pe else None,
        "gen_tokens": gen_n, "gen_seconds": round(gen_s, 1),
        "gen_tps": round(gen_n / gen_s, 1),
    }
    print(json.dumps(out))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(out, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
