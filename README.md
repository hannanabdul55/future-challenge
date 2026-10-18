# Future Challenge

Future Challenge is a forecasting challenge for frozen language models whose first season is the 2026 US midterm elections.

An entrant pins a model before the season: its Hugging Face revision, the SHA-256 of every weights file, the prompt template and the decoding settings. I run each Tier S model daily on the same information packet, publish the forecasts, and score them when results arrive. Anyone can check the chain from public files, except the external timestamp, which is not live yet.

As far as I know, the difference from other LLM forecasting benchmarks is that each model's exact identity is pinned in public before its first scored day, and every daily packet is published so anyone can replay a day. Entry is limited to language models that answer in text. One season is 75 outcomes that mostly move with one national swing, so it cannot separate close models.

## Season 1

The election is on 2026-11-03; daily forecasts run from 2026-10-26 to 2026-11-03. The 75 questions are House and Senate control, the Democratic seat count in each chamber, all 35 Senate races and all 36 governor races, each by party. See [DESIGN.md](DESIGN.md) (section 3.4), [docs/rules.md](docs/rules.md) and [docs/legal-review.md](docs/legal-review.md).

## Layout

```
future-challenge/
├── questions/<season>/       questions.json, markets.json
├── rounds/<season>/          season and daily manifests
├── target-data/packets/      created by the first daily build
├── model-metadata/           one YAML per entry
├── sidecar/                  created by the first entry
├── runs/<season>/<date>/     created by the first run
```

## Quick start

```bash
git clone https://github.com/hannanabdul55/future-challenge && cd future-challenge
make setup
make test                        # 281 tests
make questions
make packet DAY=2026-10-26       # fetches Wikipedia and GDELT data live
make round-open DAY=2026-10-26
make image
```

## Entering a model

Type `uv run fc`, not `fc`: bash and zsh have a built-in `fc` that shadows the tool. Tier S is one GGUF weights file of at most 11 GiB from a Hugging Face revision. I run it on the shared GPU with no network, greedy decoding and the day's seed. Run `uv sync --extra hf` before freezing.

```bash
uv run fc keygen --out keys/team
uv run fc freeze --repo org/model --revision main --model-id team-model --team "Team" --tier S \
   --weights-file model-Q4_K_M.gguf --prompt-template fc/prompts/forecaster-v1.md \
   --public-key keys/team/public.pem --out model-metadata/team-model.yml
uv run fc commit --round 2026-us-midterms-season --entry model-metadata/team-model.yml \
   --key keys/team/private.pem --out sidecar/team-model/2026-us-midterms-season.json
uv run fc verify --entry model-metadata/team-model.yml --season-dir rounds/2026-us-midterms/season \
   --round-manifest rounds/2026-us-midterms/2026-10-27/manifest.json
```

Open a pull request with the YAML and the sidecar JSON, and I merge it and issue a receipt. OpenTimestamps and Rekor anchoring of the receipt root is not live yet, so the anchor check in `uv run fc verify` fails for now. The commitment and Merkle inclusion checks run offline today, and so does the file-hash check with a local snapshot.

Tier A is any other LLM, including API models. Entrants run their own frozen model on the same packet, commit before 12:00 UTC and reveal by 18:00 UTC, on a separate board. Full steps and the daily Tier A commands are in [docs/participation.md](docs/participation.md).

## Daily schedule (UTC)

| Time | What happens |
|---|---|
| 00:00 | The daily-round workflow builds the packet and manifest. |
| 00:00 to 12:00 | The shared GPU runs `uv run fc run` for each admitted Tier S entry. |
| 12:00 | Commit close: Tier A commitments are due. |
| 18:00 | Reveal close: Tier S outputs and Tier A reveals are published. |
| After 2026-11-03 | Provisional resolution once the race call is stable for 72 h, then final resolution on certified results or official seating. |

## Status on 2026-10-17

Done: freeze, commit and verify for Hugging Face revisions; the 75-question set, season manifest, packet builder and daily-round workflow; the reference image and `uv run fc run`, whose container path is untested; and the scoring and board code. No entry is frozen yet. I am fine-tuning OLMo 3 7B Instruct in-house for a freeze before 2026-10-26.

Not done: OpenTimestamps and Rekor anchoring, the scheduler on the shared GPU, the pipeline that scores live days and publishes the board, resolution connectors, market mapping (`questions/2026-us-midterms/markets.json` is empty) and the GitHub Pages site.

## Licence

Code, scripts and the runner are under the Apache License 2.0 (see `LICENSE`). The question sets under `questions/` and the documents under `docs/` are under CC BY 4.0. Daily packets are not relicensed: they contain Wikipedia text (CC BY-SA) and other sources named in each packet's attribution block, which keep their own terms.
