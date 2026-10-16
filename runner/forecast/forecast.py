"""Reference forecaster — runs inside the Tier S container (RUN-01).

Contract (all paths fixed; the host mounts them):
  /model/<weights_file>      read-only GGUF, already hash-verified by the host
  /packet/packet.md          the day's packet (read-only)
  /questions/questions.json  the season question set (read-only)
  /prompt/template.md        the pinned prompt template (read-only)
  /out/                      forecast.csv, transcript.jsonl, status.json

Environment: FC_ROUND_ID, FC_DAY, FC_SEED (int), FC_WEIGHTS_FILE, FC_CTX (context length),
FC_MAX_TOKENS, FC_BATCH (questions per prompt), FC_THREADS, FC_GPU_LAYERS.

No network. Greedy decoding (temperature 0); the seed only matters if a model's chat
template injects sampling, and is recorded either way. One pass per batch, no retries:
what the model says the first time is what gets scored.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from fc import forecast_io as F

MODEL_DIR = Path("/model")
PACKET = Path("/packet/packet.md")
QUESTIONS = Path("/questions/questions.json")
TEMPLATE = Path("/prompt/template.md")
OUT = Path("/out")


def main() -> int:
    round_id = os.environ["FC_ROUND_ID"]
    day = os.environ["FC_DAY"]
    seed = int(os.environ.get("FC_SEED", "0"))
    weights = MODEL_DIR / os.environ["FC_WEIGHTS_FILE"]
    ctx = int(os.environ.get("FC_CTX", "16384"))
    max_tokens = int(os.environ.get("FC_MAX_TOKENS", "4096"))
    batch = int(os.environ.get("FC_BATCH", "20"))
    threads = int(os.environ.get("FC_THREADS", "8"))
    gpu_layers = int(os.environ.get("FC_GPU_LAYERS", "-1"))

    OUT.mkdir(parents=True, exist_ok=True)
    status = {"round_id": round_id, "day": day, "seed": seed, "weights_file": weights.name,
              "started": time.time(), "batches": []}
    try:
        from llama_cpp import Llama
    except ImportError as exc:  # pragma: no cover - only in a broken image
        status.update(error=f"llama_cpp unavailable: {exc}")
        (OUT / "status.json").write_text(json.dumps(status, indent=2))
        return 2

    qs = json.loads(QUESTIONS.read_text())["questions"]
    template = TEMPLATE.read_text()
    packet_md = PACKET.read_text()
    llm = Llama(model_path=str(weights), n_ctx=ctx, n_threads=threads, n_gpu_layers=gpu_layers,
                seed=seed, verbose=False)

    rows = []
    missing: dict[str, str] = {}
    with (OUT / "transcript.jsonl").open("w") as tr:
        for i, qb in enumerate(F.batches(qs, max_per_batch=batch)):
            prompt = F.build_prompt(template, packet_md=packet_md, questions=qb, day=day)
            t0 = time.time()
            try:
                res = llm.create_chat_completion(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0, max_tokens=max_tokens, seed=seed)
                text = res["choices"][0]["message"]["content"] or ""
                usage = res.get("usage", {})
                err = None
            except Exception as exc:  # noqa: BLE001 — recorded, batch counts as missing
                text, usage, err = "", {}, f"{type(exc).__name__}: {exc}"
            parsed = F.parse_answer(text, qb, round_id=round_id)
            rows += parsed.rows
            missing.update(parsed.missing)
            if err:
                for q in qb:
                    missing.setdefault(q["question_id"], err)
            rec = {"batch": i, "family": qb[0]["family"], "n_questions": len(qb),
                   "prompt_sha256": F.sha256_text(prompt), "prompt_chars": len(prompt),
                   "response": text, "usage": usage, "seconds": round(time.time() - t0, 1),
                   "parse_rate": parsed.parse_rate, "error": err}
            tr.write(json.dumps(rec) + "\n")
            status["batches"].append({k: rec[k] for k in ("batch", "family", "seconds",
                                                          "parse_rate", "error")})

    df = F.pd.DataFrame(rows, columns=F.OUTPUT_COLS)
    df.to_csv(OUT / "forecast.csv", index=False)
    status.update(finished=time.time(), n_rows=len(df), n_missing=len(missing),
                  missing=missing, parse_rate=1 - len(missing) / max(len(qs), 1))
    (OUT / "status.json").write_text(json.dumps(status, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
