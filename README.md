# Future Challenge

An open, contamination-proof evaluation hub for AI models. Participants commit predictions — or frozen models — *before* the information that determines their score exists. The design is in [`DESIGN.md`](DESIGN.md); this repository is the scaffold of the infrastructure described in its §11.

```
future-challenge/
├── DESIGN.md             the design document (rationale, threat model, scoring)
├── docs/                 rules (versioned), question checklist, council procedures
├── hub-config/           admin.json, tasks.json (hubverse-shaped)
├── rounds/<round_id>/    manifest.json, entropy.json, roots/ (Merkle roots + anchors)
├── model-metadata/       one YAML per entry: identity, tier, public key
├── model-output/         submissions (hubverse long format, csv/parquet)
├── rationale/            per-entry, per-round free text or transcripts
├── sidecar/              commitment records + signed receipts
├── target-data/vintages/ daily truth snapshots, hashed
├── task-families/        generator.py + verifier.py + card.md per family (paradigm G)
├── fc/                   library + `fc` CLI: commit, entropy, validate, eval, fingerprint
├── eval/                 pinned evaluator image; outputs
├── runner/               sealed executor for Tier S
├── fingerprint/          API probe panel
└── leaderboard/          static board builder
```

## Quick start

```bash
make setup            # uv sync
make test             # unit tests: commitments, Merkle, receipts, entropy, WIS/CRPS, relative skill, validation, task family
make baseline         # synthetic target data + fc-baseline and two example entries for round 2026-w37
make validate         # fc validate model-output/fc-baseline/2026-w37.csv --tasks hub-config/tasks.json
make leaderboard      # score all entries, bootstrap relative skill, contribution, write leaderboard/index.html
```

## A participant's round

```bash
fc keygen --out keys/team                        # once; register keys/team/public.pem in model-metadata
fc commit --round 2026-w37 --model acme-v3 \
   --submission model-output/acme-v3/2026-w37.parquet --rationale rationale/acme-v3/2026-w37.md \
   --key keys/team/private.pem --out sidecar/acme-v3/2026-w37.json     # before T_commit; keep the nonce
fc reveal --sidecar sidecar/acme-v3/2026-w37.json --submission ... --nonce <hex>   # after T_commit, before T_reveal
```

## The hub's round

```bash
fc entropy --round 2026-10-G01 --families linear-recurrence@0.1.0=400 --out rounds/2026-10-G01/entropy.json
#   → prints H(S) to publish + anchor before T_commit, and S to keep secret
fc entropy --round 2026-10-G01 --drand-round <R> --secret <S> --hs <H(S)> --families ... --out ...
#   → after T_commit: fetches drand round R, reveals S, derives E; anyone can regenerate the task set
fc merkle --commits <c1>,<c2>,... --prove 0        # Merkle root + inclusion proof; anchor the root
fc score --pred model-output/*/2026-w37.csv --truth target-data/vintages/.../truth-2026-w37.csv --baseline fc-baseline
fc leaderboard --scores eval/out/scores.csv
```

## Status

Scaffold. Working: commitment protocol, Merkle proofs, signed receipts, entropy derivation, validation, proper scores, pairwise relative skill with bootstrap CIs, ensemble contribution, static leaderboard, one paradigm-G task family with verifier and tests, runner invocation and policies. Not yet: OpenTimestamps/Rekor anchoring client, submission HTTP service, egress proxy, hubverse `tasks.json` converter, real data connectors, task-family novelty index.
