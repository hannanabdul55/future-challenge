TODO(author): byline, date and the repository URL wherever this post says "the repository".

# Frozen models forecast the 2026 midterms, every day

I built a forecasting challenge in which each model is frozen before the events it forecasts. Season 1 is the 2026 US midterms: 75 questions on chamber control, seat counts and the winning party in all 35 Senate and 36 governor races. No question names a candidate. From 2026-10-26 to election day, 2026-11-03, I run every frozen model once a day on the same information packet and publish what it says.

## Why

Most forecasting comparisons can't be re-run. An API model can change under the same name, and an agent that searches the web sees a different web each time. Here the model is pinned before its first scored day and each day's packet is published, so anyone can re-run a day with greedy decoding. On other hardware, floating-point differences can still change a few digits.

## The chain

weights hash → anchor (not live yet) → daily packet hash → forecast → outcome

Each link is a file in the repository. An entry's YAML in model-metadata/ pins the Hugging Face commit, a SHA-256 for every file, the inference settings and the prompt-template hash. `uv run fc commit --entry` commits it, and I receipt it into rounds/2026-us-midterms/season/. The Merkle root of those receipts is meant to go to OpenTimestamps and Rekor. Each day's manifest pins the packet's SHA-256, and each run writes a signed bundle.json carrying the same hash. The outcome is the certified result.

Anchoring to Bitcoin isn't live yet; until it is, the freeze time rests on my signature on the receipt.

TODO(author): [BLOCKER B1] If anchoring ships before posting, replace the paragraph above and the "(not live yet)" in the chain line.

## A season day

At 00:00 UTC I publish the day's packet and pin its hash in the manifest. The models run in the morning, commitments close at 12:00 UTC, and forecasts are revealed at 18:00 UTC. The packet is about 20k characters of Markdown, built from Wikipedia's per-race polling tables and GDELT headlines (titles and links only). In the last live build, 61 of 71 races had general-election polls. Market prices aren't in the packet yet.

TODO(author): [BLOCKER B2] If markets.json is filled before posting, say which markets the packet carries.

Runs will use a Docker container with networking off; the image isn't built yet, and the dry run ran outside it.

TODO(author): [BLOCKER B3] If the image is built and its digest pinned, say so here instead.

## What the dry run showed

I ran Qwen3.5-9B (Q4_K_M) on one RTX 4070 SUPER against a live packet, with prompts of about 8-9k tokens. With thinking on, it never closed its reasoning within the 4,096-token output limit and parsed 0/75 questions. With thinking off it parsed 74/75 in 61 seconds. The one miss was a question id it made up.

Thinking on or off is now a field in the frozen identity, and it defaults to off. An entrant can turn it on at freeze time; the 4,096-token limit still applies.

## Scoring

Nothing is scored until results arrive after 2026-11-03, and the code that pulls them isn't built yet.

TODO(author): [BLOCKER B12] Once the results resolver exists, delete the second half of the sentence above.

Binary and categorical questions get the log score, with Brier as a secondary score. Seat counts get the weighted interval score. Ranks are relative skill against fc-baseline, a status-quo rule I fixed before the season (model-metadata/fc-baseline.yml), with bootstrap intervals by day. A model needs at least 7 scored days for a headline rank. During the season the board shows forecasts and parse rates, and no ranks.

One season can't separate close models. There are 75 outcomes, many races are safe, and most of them move with one national swing; forecasting daily adds no new outcomes. The intervals resample days, which understates this, so expect most models to overlap.

AI model forecasts, not statements of fact or endorsements.

## Related work

ForecastBench scores API models against crowd "freeze values". FutureX (ICLR 2026) adds questions from 128 websites daily and lets agents search. Prophet Arena (ICLR 2026) gives every model, open-weight ones included, the same context for each Kalshi event (search results and market data). The Metaculus AI benchmark runs quarterly bot tournaments against Pro forecasters. Foresight Arena (arXiv 2605.00420) commits forecasts on Polygon. LLM-SoccerArena (arXiv 2607.24573) logged timestamped forecasts, prompts and model versions for the 2026 World Cup. Bench to the Future (arXiv 2506.21558) and Hindcast (arXiv 2607.14051) replay resolved questions against a frozen web corpus or Reddit snapshot. ChronoGPT (arXiv 2502.21206) and DatedGPT release open weights with dated knowledge cutoffs for back-testing. Kaggle's Optiver and Jane Street competitions score submitted code on market data that arrives after submissions close.

So live questions, timestamped commitments, shared inputs and models frozen before their data exists aren't new. As far as I know, what's different here is a live season in which each model's exact identity is pinned in public before scoring: Hugging Face revision, every file's SHA-256, the prompt template, and decoding including thinking on or off (Prophet Arena lists models by family, such as "GPT-5", and doesn't state decoding settings). The daily inputs are also published, so anyone can replay any day. If I have any of this wrong, tell me; the FAQ has a contact address.

## Enter or check it

Entries must be LLMs. Tier S takes one GGUF weights file of at most 11 GiB, which I run on my GPU. Any other LLM, including API models fingerprinted at freeze, runs on your own hardware as Tier A on a separate board. Freeze before 2026-10-26 00:00 UTC to be scored from the first day. The guide is docs/participation.md, and docs/launch/verify.md has the checks.

TODO(author): [BLOCKER B11] Link docs/participation.md only once its commands read uv run fc.

My own entries aren't frozen yet.

TODO(author): [BLOCKER B4] Once they are, name them here and drop the sentence above.
