TODO(author): byline and date.

# Future Challenge: questions and answers

Everything this project publishes about the midterms carries one label: AI model forecasts, not statements of fact or endorsements.

TODO(author): [BLOCKER B7] Make sure this label matches legal-review §2.7 and participation.md §6 before posting.

## What is this?

A forecasting challenge for AI models that are frozen before the events they forecast. Season 1 asks 75 questions about the 2026 US midterms. From 2026-10-26 to 2026-11-03 I run each frozen model once a day on the same packet of polls and headlines and publish the forecasts. Scoring starts once results are in.

## What is it not?

There is no money involved: no prizes, entry fees or stakes. It is not a prediction market, and nothing is traded. It endorses no candidate or party. Every Senate and governor race is covered, plus both chambers, and the questions ask only which party wins. The reference baseline is a status-quo rule fixed before the season, and my daily notes are about how the models behave.

## Who runs it?

I do. I'm one independent person. No campaign or party committee sponsors or funds this.

TODO(author): one-line bio.
TODO(author): affiliations to disclose (employer, grants, advisory roles), or "none".

## How is it scored?

Nothing is scored until results arrive after 2026-11-03. Provisional scores come once race calls have been stable for 72 hours. Final scores are fixed on 2026-12-15 for Senate and governor races and on 2027-01-06 for chamber control and seat counts. The code that pulls results isn't built yet.

TODO(author): [BLOCKER B12] Once the results resolver exists, delete the last sentence above.

Binary and categorical questions get the log score (clipped at 0.001), with Brier as a secondary score. Seat counts get the weighted interval score. Ranks are relative skill against fc-baseline, a status-quo rule fixed before the season, compared over the (day, question) pairs both answered, with 95% block-bootstrap intervals by day. A model needs at least 7 scored days for a headline rank. A missing or unparseable answer scores as the worse of the baseline and the 75th percentile of submitted scores. During the season the board shows forecasts and parse rates, and no ranks.

One season can't separate close models. There are 75 outcomes, many races are safe, and most of them move with one national swing; forecasting daily adds no new outcomes. The intervals resample days, which understates this, so expect most models to overlap.

The scoring code is public; the evaluator image digest isn't pinned yet.

TODO(author): [BLOCKER B9] Once the digest is in season.json, say so here.

## What can't you guarantee?

Tier S runs will use a Docker container with networking off, read-only mounts and time quotas. That is weaker isolation than gVisor. The image isn't built yet, and the dry run ran outside it.

TODO(author): [BLOCKER B3] If the image is built and its digest pinned, replace the last two sentences above.

Greedy decoding is reproducible on a fixed hardware and software stack, but not bit-for-bit across GPUs. API models can't be proven frozen. They are fingerprinted at freeze, which catches large changes, and they stay on the Tier A board.

Anchoring to Bitcoin isn't live yet; until it is, the freeze time rests on my signature on the receipt.

TODO(author): [BLOCKER B1] If anchoring ships before posting, replace the sentence above.

## How do I check it?

docs/launch/verify.md walks through checking a day's packet hash, each freeze, and the day's commitments with `uv run fc verify` from a clone of the repository.

## How do I enter?

Tier S is one GGUF weights file of at most 11 GiB from a Hugging Face revision, which I run with greedy decoding on my GPU. Tier A is any other model, including API models, run on your own hardware against the same packet and shown on a separate board. Each team gets at most 5 entries, with exactly one primary. Freeze before 2026-10-26 00:00 UTC to be scored from the first day. The steps are in docs/participation.md.

TODO(author): [BLOCKER B11] Link docs/participation.md only once its commands read uv run fc.

## Why are there no candidate names?

Each question asks which party wins a seat and resolves from official results, so no forecast is a statement about a person. The rules also exclude questions about a candidate's health, conduct, legal exposure or withdrawal.

## How do I reach you?

TODO(author): contact address.
