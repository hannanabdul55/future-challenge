# Launch posts

## Before posting

Posts that show forecasts go out only after the first reveal, at 18:00 UTC on 2026-10-26. Before posting any piece, run `grep -n "TODO(author)" docs/launch/social.md` and resolve every line that belongs to that piece. The chart is a screenshot of the board until a PNG export exists. Links point to the repository until the site is live.

TODO(author): [BLOCKER B8] Attach a board screenshot to post 2/5 unless the PNG export has shipped.
TODO(author): [BLOCKER B5] Use the repository URL in every link below until the site is up.
TODO(author): [BLOCKER B7] Do not post anything until the legal-review §3 items are closed or knowingly accepted.

## X / Bluesky thread

```text
1/5 I built a forecasting challenge for frozen LLMs. Each model's revision, file hashes, prompt and decoding settings are committed before its first scored day. I then run it every day on the 2026 US midterms: 75 questions, 2026-10-26 to 11-03.

2/5 Today's forecasts from every hub-run model, all made from the same byte-identical packet with greedy decoding, so anyone can re-run a day (chart attached). AI model forecasts, not statements of fact or endorsements.

3/5 From the dry run: Qwen3.5-9B with thinking on never closed its reasoning within 4,096 tokens and parsed 0/75 questions. With thinking off it parsed 74/75 in 61 s. Thinking on/off is now part of the frozen identity.

4/5 Check a freeze from a clone: uv run fc verify --entry model-metadata/ENTRY.yml --season-dir rounds/2026-us-midterms/season --round-manifest rounds/2026-us-midterms/2026-10-26/manifest.json (anchor checks fail until anchoring ships)

5/5 Everything is in one public repository, including how to enter. Freeze a GGUF of at most 11 GiB before 2026-10-26 00:00 UTC to be scored from the first day.
```

TODO(author): link, as a reply under 5/5.
TODO(author): [BLOCKER B4] Replace ENTRY in 4/5 with a real entry id once your own entries are frozen.
TODO(author): [BLOCKER B1] If anchoring ships before posting, drop the parenthesis at the end of 4/5.

## LinkedIn

```text
I built Future Challenge, a forecasting challenge for AI models that are frozen before the events they forecast.

Season 1 is the 2026 US midterms: 75 questions on chamber control, seat counts and every Senate and governor race by party. Entrants commit to a model (Hugging Face revision, a SHA-256 for every file, prompt template, decoding settings) before its first scored day. From 2026-10-26 to 2026-11-03 I run every hub-run model once a day on the same information packet and publish the forecasts.

Scoring starts once results are in. Ranks need at least 7 scored days and carry bootstrap intervals.

One dry-run finding: a 9B model with thinking on parsed 0 of 75 questions within its token limit. With thinking off it parsed 74.

Repository and verify guide in the first comment.

AI model forecasts, not statements of fact or endorsements.
```

TODO(author): repository link and docs/launch/verify.md link for the first comment.

## Show HN

Title: Show HN: Future Challenge, frozen LLMs forecast the 2026 midterms daily

```text
FutureX, Prophet Arena and Foresight Arena already score live forecasts. I wanted a comparison anyone can re-run, so here the model and its inputs are both fixed.

An entrant commits to a model: Hugging Face revision, SHA-256 of every file, prompt template and decoding settings. Each day from 2026-10-26 to 2026-11-03, at 00:00 UTC I publish an information packet (Wikipedia polling tables, GDELT headlines) and pin its hash. I run every Tier S model with greedy decoding on that packet. There are 75 questions: chamber control, seat counts, and every Senate and governor race by party. Most of them move with one national swing, so one season won't separate close models.

In the dry run, Qwen3.5-9B with thinking on never finished reasoning within 4,096 tokens and parsed 0/75. With thinking off it parsed 74/75. Thinking on/off is now part of the frozen identity.

Not live yet: Bitcoin anchoring (until then the freeze time rests on my signature), the Docker image for runs (the dry run ran outside it), and market prices in the packet.

To check a freeze from a clone:

  uv run fc verify --entry model-metadata/ENTRY.yml --season-dir rounds/2026-us-midterms/season --round-manifest rounds/2026-us-midterms/2026-10-26/manifest.json

AI model forecasts, not statements of fact or endorsements.
```

TODO(author): post the comment right after submitting, and put the repository URL in the submission.
TODO(author): [BLOCKER B4] Replace ENTRY in the command with a real entry id.
TODO(author): [BLOCKER B1] If anchoring ships, delete it from the "Not live yet" line.
TODO(author): [BLOCKER B3] If the runner image is built, delete it from the "Not live yet" line.
TODO(author): [BLOCKER B2] If market prices are in the packet, delete them from the "Not live yet" line.

## EA Forum / LessWrong

Title: Frozen LLMs forecast the 2026 midterms daily: please check the scoring rules

```text
I run a daily forecasting challenge for frozen AI models, and I'd like forecasters to check the scoring before it counts.

Binary and categorical questions get the log score, clipped at 0.001, with Brier as a secondary score. Seat counts get the weighted interval score. Ranks are relative skill against a status-quo baseline fixed before the season, compared over shared (day, question) pairs, with block-bootstrap intervals by day. No model gets a headline rank with fewer than 7 scored days.

I know the sample is small: 75 outcomes, many of them safe seats, mostly moving with one national swing, and the intervals resample days. If you would also resample by race or by state, or do something else, tell me.

Nothing is scored before results arrive after 2026-11-03. Until then the board shows forecasts and parse rates.

The season has 75 questions on the 2026 US midterms: chamber control, seat counts and every Senate and governor race by party. Each model is frozen before its first scored day (Hugging Face revision, per-file SHA-256, prompt template, decoding settings), and I run every hub-run model daily on the same information packet. Market prices aren't in the packet yet, so the status-quo rule is the only reference baseline.

The rules are in docs/rules.md, item 6. Corrections are most useful before 2026-10-24.

AI model forecasts, not statements of fact or endorsements.
```

TODO(author): repository link.
TODO(author): [BLOCKER B2] If markets.json is filled before posting, rewrite the sentence about market prices.

## r/MachineLearning

Title: [P] Frozen LLMs forecast the 2026 US midterms daily; thinking mode broke parsing in my dry run

```text
In a dry run, Qwen3.5-9B (Q4_K_M, one RTX 4070 SUPER) with thinking on never closed its reasoning within the 4,096-token output limit and parsed 0/75 questions. With thinking off it parsed 74/75 in 61 s. The one miss was a question id the model made up. Thinking on/off is now a field in each model's frozen identity, off by default.

The point is a comparison anyone can re-run. Entrants commit to a model (HF revision, SHA-256 of every file, prompt template, decoding settings) before the first day it is scored, and the questions are about the 2026 US midterms, whose results don't exist yet. From 2026-10-26 to 2026-11-03 I run every Tier S model (one GGUF of at most 11 GiB) with greedy decoding on the same byte-identical daily packet, about 8-9k tokens of polls and headlines.

Not live yet: Bitcoin anchoring of the freeze receipts, and the Docker image for runs (the dry run ran outside it).

Code and a verify guide are in the repository.

AI model forecasts, not statements of fact or endorsements.
```

TODO(author): repository link.
TODO(author): [BLOCKER B1] If anchoring ships, drop it from the "Not live yet" line.
TODO(author): [BLOCKER B3] If the runner image is built, drop it from the "Not live yet" line.

## r/forecasting

Title: Daily forecasts from frozen AI models on 75 midterm questions, log-scored with bootstrap intervals

```text
I'm running a forecasting challenge for AI models that are frozen before the events they forecast. Here is how it is scored.

Binary and categorical questions get the log score, with Brier as a secondary score. Seat counts get the weighted interval score. Ranks are relative skill against a status-quo baseline fixed before the season, with block-bootstrap intervals by day, and a model needs at least 7 scored days for a headline rank. With 75 outcomes that mostly move together, expect most intervals to overlap. A missing or unparseable answer scores as the worse of the baseline and the 75th percentile of submitted scores.

Nothing is scored before results arrive after 2026-11-03. Provisional scores come once race calls have been stable for 72 hours.

The questions: chamber control, seat counts and every Senate and governor race by party, with no candidate names. Every hub-run model reads the same daily packet of polls and headlines. Forecasts are published daily from 2026-10-26.

AI model forecasts, not statements of fact or endorsements.
```

TODO(author): repository link.

## Hugging Face forum

Title: Enter a GGUF of 11 GiB or less in a daily forecasting challenge on the 2026 midterms

```text
If you have a GGUF of at most 11 GiB on the Hub, I can run it every day from 2026-10-26 to 2026-11-03 on 75 questions about the 2026 US midterms.

To enter, you freeze the model: the Hugging Face revision, the one weights file, a SHA-256 for every file, the prompt template and the decoding settings. Thinking mode is part of that frozen identity and defaults to off. In my dry run a 9B model with thinking on parsed 0 of 75 questions inside a 4,096-token limit.

You commit the freeze with uv run fc commit --entry and open a pull request with the entry YAML and its sidecar. CI checks the hashes, the licence allowlist and the size budget. Freeze before 2026-10-26 00:00 UTC to be scored from the first day.

I run every Tier S model with greedy decoding and a 16384-token context on one RTX 4070 SUPER, all on the same daily packet. Each team can have up to 5 entries. Anything bigger, or an API model, can enter as Tier A on a separate board.

AI model forecasts, not statements of fact or endorsements.
```

TODO(author): repository link.
TODO(author): [BLOCKER B6] The Hugging Face org doesn't exist yet; leave out org links until it does.
TODO(author): [BLOCKER B11] Link docs/participation.md only once its commands read uv run fc.
