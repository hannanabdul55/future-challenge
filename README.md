# Future Challenge

**Freeze your model before the election. We run it every day. The world grades it.**

An open, contamination-proof forecasting challenge for frozen AI models. A participant freezes a model (a Hugging Face revision whose files are hashed and externally timestamped) *before* the events it will forecast have happened. The hub runs every frozen model every day on the same curated information packet, publishes the forecasts, and scores them when reality arrives. Anyone can verify the chain — weights hash → anchored timestamp → daily packet hash → forecast → outcome — from public artifacts, offline.

**Season 1: the 2026 US midterm elections** (November 3, 2026). Daily forecasts from 2026-10-26 of House and Senate control, seat counts, and all 35 Senate and 36 gubernatorial races by party. Design: [`DESIGN.md`](DESIGN.md) (§3.4 for the daily season). Rules: [`docs/rules.md`](docs/rules.md). Legal notes: [`docs/legal-review.md`](docs/legal-review.md).

```
future-challenge/
├── DESIGN.md                 rationale, threat model, scoring; §3.4 the frozen-model daily season
├── docs/                     rules (versioned), question checklist, legal review, marketing plan
├── questions/<season>/       races.csv → questions.json (75 machine-readable specs), markets.json
├── rounds/<season>/          season.json · season/manifest.json (freeze round) · <date>/manifest.json
├── target-data/packets/      daily packets: packet.md, packet.json, raw/, attribution.json, index.json
├── model-metadata/           one YAML per entry: tier, model_identity (HF revision + file hashes), key
├── sidecar/                  freeze commitments + hub receipts
├── model-output/<entry>/     daily forecasts (hubverse long format)
├── runs/<season>/<date>/     Tier S run bundles: transcript, status, signed bundle.json
├── fc/                       library + `fc` CLI
├── runner/forecast/          reference Tier S image (llama.cpp) + forecast.py
├── eval/, leaderboard/       evaluator image, static board
```

## Quick start

```bash
make setup                 # uv sync
make test                  # 70 tests: commitments, Merkle, receipts, freeze, questions, packet, runner, scores
make questions             # regenerate + validate questions/2026-us-midterms/questions.json
make packet DAY=2026-10-26 # build the day's packet from Wikipedia / markets / GDELT
make round-open DAY=...    # packet + daily manifest
make image                 # build the reference forecaster image
```

## Enter a model (Tier S, hub-run)

```bash
fc keygen --out keys/team
fc freeze --repo org/model --revision main --model-id team-model --team "Team" --tier S \
   --weights-file model-Q4_K_M.gguf --prompt-template fc/prompts/forecaster-v1.md \
   --public-key keys/team/public.pem --out model-metadata/team-model.yml
#   → prints the identity hash; the YAML pins commit sha + per-file sha256 + inference config
fc commit --round 2026-us-midterms-season --entry model-metadata/team-model.yml \
   --key keys/team/private.pem --out sidecar/team-model/2026-us-midterms-season.json
#   → open a PR with model-metadata/ + sidecar/; the hub receipts it and anchors the Merkle root
fc verify --entry model-metadata/team-model.yml --season-dir rounds/2026-us-midterms/season \
   --round-manifest rounds/2026-us-midterms/2026-10-27/manifest.json
#   → anyone: anchor precedes that day's T_open; commitment opens to the identity record
```

Hardware budget for Tier S: one GGUF ≤ 11 GiB (12 GB consumer GPU). Larger or API models enter as Tier A: run your frozen model on the same packet and `fc commit` / `fc reveal` the forecast daily (see [`docs/participation.md`](docs/participation.md)).

## The hub's day (all UTC)

| 00:00 | `daily-round` workflow builds the packet, opens `rounds/<season>/<date>/manifest.json` (packet hash, drand round) |
|---|---|
| 00:00–12:00 | GPU box: `fc run --entry … --date …` for every admitted Tier S entry; outputs rooted before 12:00 |
| 12:00 | commit close (Tier A commitments due) |
| 18:00 | reveal close; Tier S outputs + transcripts and Tier A reveals published together |
| after 11-03 | resolution from public record; provisional (72 h stable calls) then final (certified / seating) |

## Status

Built 2026-10-16 (pivot night): freeze/commit/verify for Hugging Face identities; Season 1 question set (75) and season manifest; daily packet builder (per-source degradation, attribution, hashes); daily-round workflow with backstop; reference forecaster image and host runner with weights re-hash, sealed docker command and signed bundles; mixed-type scoring and season baselines. Not yet: real OTS/Rekor anchoring (seam exists), scheduler on the GPU box, scoring/board pipeline for the season, resolution connectors, market mapping (`questions/2026-us-midterms/markets.json` is empty), site.

## Licence

Code, scripts and the runner are under the Apache License 2.0 (see `LICENSE`). The question sets under `questions/` and the documents under `docs/` are under CC BY 4.0. Daily packets are not relicensed: they contain text from Wikipedia (CC BY-SA) and other sources named in each packet's attribution block, which keep their own terms.
