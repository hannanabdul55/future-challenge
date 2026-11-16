# Scope

Operating scope for season 2026-27-rolling. Rules text is `docs/rules.md`; rationale is `DESIGN.md`.

## What this is

A rolling benchmark of frozen models. Season 2026-27-rolling runs 2026-11-24 to 2027-03-31. Templated macro questions are track `ranked` and carry the statistics; curated world questions are shown, never ranked. Season 1 (2026-us-midterms) is closed and resolves in Phase 8.

## Cadence

A weekly review every Monday (UTC), with a fixed checklist:

- Run the evidence command (section "Hub-run tier evidence gate").
- Read alerts and `docs/ops/interventions.md`.
- Confirm next month's macro questions are generated before their open date.
- Look at the board.

## Rolling season

New questions are added only from the published macro template, before their open date. A published question's statement and resolution block are never edited.

## 90-day no-change rule

From 2026-11-24 through 2027-02-21 inclusive (90 days) nothing changes in:

- the normative rules text (`docs/rules.md`);
- the scoring rules: log score clipped at 0.001, WIS, the imputation rule, relative skill vs `fc-baseline`, block bootstrap by cluster, the 7-day headline minimum;
- the admission rule;
- the prompt template `fc/prompts/forecaster-v1.md`;
- the resolution blocks of published questions.

Allowed during the freeze:

- new months generated from the published template;
- board display changes;
- bug fixes that make code match the published rules;
- security fixes.

Every exception is logged below.

### Changes during the freeze

| Date (UTC) | Change | Why | Commit |
|---|---|---|---|

## Hub-run tier evidence gate

Window: 2026-11-24 to 2026-12-03 (10 days). A day passes when all of these hold:

1. Every Tier S entry has a bundle at `runs/2026-27-rolling/<day>/<entry>/bundle.json` whose runner signature verifies against `hub-config/keys/runner.pub.pem`, with status `ok` and `parse_rate` at least 0.80. This is the threshold at which the daily job itself alerts. The 0.80 is a liveness floor for counting unattended days and is deliberately separate from the 0.95 RUN-06 parse gate in the next section; the daily count uses 0.80.
2. Both job records `runs/2026-27-rolling/<day>/job-commit.json` and `job-reveal.json` exist and report ok, meaning the job's own success criteria held: no entry, packet, round, deadline, VRAM, parse-rate, commit or push failure.
3. No manual intervention is logged for that day in `docs/ops/interventions.md`.

Pass = at least 7 of 10 days (7 of 10) pass. Decided by:

```bash
uv run python -m fc.ops_evidence --season 2026-27-rolling --start 2026-11-24 --days 10 --need 7
```

(built in plan 06-05). Status: PENDING. The verdict is decidable from 2026-12-03 18:30 UTC, 30 minutes after the last reveal; before then the command prints PENDING by design.

Pre-committed consequence if fewer than 7: the hub-run tier is declared dead. The GPU box timers are disabled, the hub stops running Tier S and house entries, published results stay up, and the project continues with self-run (Tier A) entries only. The verdict is recorded in this section.

The tier starts with a single operator entry (`house-qwen2.5-14b-q4`). ENTRY-01's three in-house entries are tracked under Phase 4 and are not a condition of this gate.

## RUN-06 parse gate and fallback

Pre-committed before the parse test in plan 06-08.

Gate: `house-qwen2.5-14b-q4` (Qwen2.5-14B-Instruct, Q4_K_M GGUF) parses at least 0.95 of the asked questions on a real 2026-27-rolling packet inside the sealed container, read from the runner-signed scratch bundle.

Shortfall triage, from `transcript.jsonl`:

- A runner or parser defect is fixed in code, the image rebuilt and the test rerun. The gate is unchanged.
- A context or `max_tokens` truncation means re-freezing before anything is committed, then rerunning.
- A model-side shortfall (well-formed prompt, invalid or out-of-band answer) gets one rerun each with the Q4_K_S and Q4_0 files of the same model. These are still 14B Q4 entries.

Still below 0.95: RUN-06's live criterion is recorded here as NOT MET with the measured rates. The threshold is never lowered. To keep the hub-run tier evidence alive, the operator entry becomes the best-parsing candidate reaching 0.80 on the same packet (the best 14B Q4 file tried, else Qwen2.5-7B-Instruct Q4_K_M as `house-qwen2.5-7b-q4`). It is committed into the freeze round only after the author confirms, a one-way step.

If no candidate reaches 0.80, no operator entry is committed and the verdict of the evidence gate is FAIL by construction.

## Anchoring status

Status: not yet stamped. Plan 06-17 stamps the 2026-27-rolling freeze root and plan 06-10 records here the Bitcoin block (or that confirmation is still pending) and the first day on which the house entry is admitted to scoring (the first day whose T_open is at least the block time + 2 h).

## Operator entries

The `house-` prefix is reserved. House entries are run and published, never ranked.

## Kill check 2027-02-12

Fewer than 3 external freezes by 2027-02-12 means the open-entry framing is dropped.

## Deferred

- Rekor anchoring.
- gVisor and custom images.
- Prizes of any kind are out of scope.
