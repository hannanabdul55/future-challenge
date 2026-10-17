# Outreach

These go out on 2026-10-19, 10-15 messages in total (marketing-plan §3). Send at most one follow-up, after four days, and none after a no.

## Who to contact

- Open-model builders: published a GGUF or fine-tune on Hugging Face in the last 90 days, under a licence on the allowlist in legal-review §2.5, with a weights file that fits in 11 GiB.
- Forecasting community: writes publicly about calibration or proper scoring.
- Eval researchers: published on contamination, evaluation methodology or forecasting benchmarks in 2025-26.
- Press: covers AI evaluation or election data.

Don't contact campaign staff, candidates, party committees or PACs (legal-review §2.8), or anyone who has asked not to be contacted.

### Template A: open-model builders

Subject: Running your GGUF daily on the 2026 midterms

```text
Hi,

I run Future Challenge: I freeze open-weight models and run them every day on 75 questions about the 2026 US midterms.

TODO(author): one line on why this person

Would you freeze and enter one of your GGUFs (11 GiB or less) before 2026-10-26 00:00 UTC, so it is scored from the first day? It takes one YAML file and a pull request, and I run it on my GPU.
```

One ask: freeze and enter one model before 2026-10-26 00:00 UTC so it is scored from day one.

### Template B: forecasting community

Subject: Scoring rules for a frozen-model forecasting season

```text
Hi,

I run Future Challenge, a daily forecasting challenge for frozen AI models on the 2026 US midterms.

TODO(author): one line on why this person

The scoring is log score and weighted interval score, ranked as relative skill against a status-quo baseline with block-bootstrap intervals by day. Could you read the rules (docs/rules.md, item 6) and tell me what is wrong with them by 2026-10-24?
```

One ask: read the scoring rules (docs/rules.md item 6) and tell me what is wrong, by 2026-10-24.

### Template C: eval researchers

Subject: Weakest link in a frozen-model forecasting chain

```text
Hi,

I run Future Challenge: entrants commit to a model's file hashes, prompt template and decoding settings before it is scored, and I run it daily on the 2026 US midterms.

TODO(author): one line on why this person

Would you read the threat model (DESIGN.md §9) and the verify guide (docs/launch/verify.md) and tell me which link in the chain is weakest?
```

One ask: read the threat model and the verify guide and tell me the weakest link.

### Template D: press

Subject: Results from frozen AI models on the 2026 midterms

```text
Hi,

I run Future Challenge, where AI models frozen before the 2026 US midterms forecast both chambers and every Senate and governor race, once a day, from the same packet of polls and headlines.

TODO(author): one line on why this person

Race calls should settle between 2026-11-04 and 11-10. May I send you the results post then?
```

One ask: may I send you the results post when race calls settle (2026-11-04 to 11-10).

## Tracking table

Keep the filled copy outside the public repository, because names and handles are personal data.

| name | handle | audience | ask | sent date | reply |
|---|---|---|---|---|---|
