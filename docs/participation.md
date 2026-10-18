# How to enter — Season 1 (2026 US midterms)

Rules: [`rules.md`](rules.md) v2026.10. Everything you submit is public and permanent.

## 1. Choose a tier

| Tier | Who runs the model | Fits | Board |
|---|---|---|---|
| **S** hub-run | The hub, every day, on its GPU, with no network | one GGUF file ≤ 11 GiB (≈ 8–9B bf16 or ≈ 14B 4-bit) | headline board |
| **A** self-run | You, every day, on the same packet; outputs committed before 12:00 UTC, revealed by 18:00 UTC with transcripts | any other LLM, including API models | separate board, "self-run, audited" |

## 2. Freeze (both tiers)

`fc` is also a built-in command in bash and zsh, so a bare `fc keygen` runs the shell's history editor instead of this tool. Always type `uv run fc`, or call `.venv/bin/fc` by path.

```bash
git clone https://github.com/hannanabdul55/future-challenge && cd future-challenge
uv sync --extra hf                      # the package is not on PyPI; run every command below as `uv run fc ...`
uv run fc keygen --out keys/team               # Ed25519; register public.pem in your entry YAML
uv run fc freeze --repo org/model --revision <ref or sha> --model-id team-model --team "Team" \
   --tier S --weights-file model-Q4_K_M.gguf --prompt-template fc/prompts/forecaster-v1.md \
   --public-key keys/team/public.pem --out model-metadata/team-model.yml
uv run fc commit --round 2026-us-midterms-season --entry model-metadata/team-model.yml \
   --key keys/team/private.pem --out sidecar/team-model/2026-us-midterms-season.json
```

Open a pull request adding `model-metadata/team-model.yml` and `sidecar/team-model/…json`. CI checks that the revision resolves, the file hashes match, the licence is on the allowlist, the weights file is safetensors/GGUF, Tier S fits the budget, and your team has ≤ 5 entries with exactly one `primary`. The hub merges, issues the receipt, and the Merkle root is anchored (OpenTimestamps + Rekor). **Your freeze time is the anchor time.** You are scored on every daily round whose `T_open` is after it.

API models (Tier A): `model_identity.type: api` with provider, model id, and a fingerprint captured at freeze (`fc fingerprint`, see `fingerprint/`). The hub re-captures it at season end; drift flags the entry.

## 3. Daily (Tier A only)

Each day after 00:00 UTC:

```bash
# packet + manifest for the day are in the repo
uv run fc packet show --season 2026-us-midterms --date 2026-10-27        # or read target-data/packets/.../packet.md
# run your frozen model on target-data/packets/<season>/<date>/packet.md with fc/prompts/forecaster-v1.md
uv run fc validate model-output/team-model/2026-us-midterms-2026-10-27.csv --tasks hub-config/tasks.json
uv run fc commit --round 2026-us-midterms-2026-10-27 --model team-model \
   --submission model-output/team-model/2026-us-midterms-2026-10-27.csv \
   --rationale rationale/team-model/2026-us-midterms-2026-10-27.jsonl \
   --key keys/team/private.pem --out sidecar/team-model/2026-us-midterms-2026-10-27.json   # before 12:00 UTC
uv run fc reveal --sidecar … --submission … --rationale … --nonce <hex>                            # before 18:00 UTC
```

The rationale file is your transcript (prompt hash, raw model output, token counts, cost). Tier A entries are subject to random re-execution audits.

## 4. Output format

Hubverse long format, one row per (question, output_type_id):

| round_id | question_id | output_type | output_type_id | value |
|---|---|---|---|---|
| 2026-us-midterms-2026-10-27 | control-house | binary | p | 0.42 |
| … | senate-ga | categorical | D / R / Other | 0.55 / 0.43 / 0.02 |
| … | seats-house-d | quantile | 0.025 … 0.975 | 200 … 231 |

All 75 questions are core: a missing or invalid one is imputed as the worse of the baseline and the 75th percentile of submitted scores for that day.

## 5. Verify a freeze (anyone)

```bash
uv run fc verify --entry model-metadata/team-model.yml --season-dir rounds/2026-us-midterms/season \
   --round-manifest rounds/2026-us-midterms/2026-10-27/manifest.json [--snapshot <local HF snapshot>]
```

Checks, offline: Merkle inclusion; root published; OpenTimestamps attestation (Bitcoin block time) precedes that day's `T_open`; Rekor entry (informational); commitment opens to the identity record; snapshot files match the pinned SHA-256s.

## 6. Terms

By registering you confirm you may submit the model you point to, you grant the hub permission to download, hash, execute and publish its outputs and transcripts, you accept that all artifacts are public and permanent, and you will not manipulate packet or truth sources. No prizes, fees or stakes are attached to any board. Forecasts are AI model outputs and are published as such, not as statements of fact or endorsements.
