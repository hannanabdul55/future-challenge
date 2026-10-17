# Marketing plan — Future Challenge, Season 1 (2026 US midterms)

Owner: the author (independent, personal brand). Budget: $0. Horizon: 2026-10-16 → 2027-01-06.

## 1. Positioning

**One line.** *Freeze your model before the election. We run it every day. The world grades it.*

### What is distinctive, stated narrowly

ForecastBench scores API models with Brier and log scores against crowd "freeze values". FutureX (ICLR 2026) adds questions from 128 websites daily and lets agents search. Prophet Arena (ICLR 2026) scores live Kalshi events on calibration and market returns. The Metaculus AI benchmark runs quarterly bot tournaments with peer scores against Pro forecasters. Foresight Arena (arXiv 2605.00420) commits forecasts through smart contracts on Polygon. LLM-SoccerArena (arXiv 2607.24573) logged timestamped forecasts, prompts and model versions for the 2026 World Cup. So live questions, leakage-free scoring and timestamped commitments are not new, and "contamination-proof" is not the pitch. As far as I know, the difference is that entrants commit to the model itself (HF revision, SHA-256 of every file, prompt template, decoding including thinking on/off) before its first scored day, and I run every Tier S model greedily on the same byte-identical daily packet. Anyone can check the chain from public files, except the external anchor, which isn't live yet (B1). The pitch is that the comparison is controlled and replayable: same weights, same inputs, anyone can re-run a day. The launch post, the Show HN comment and the forecasting-community posts also say that one season (75 outcomes, mostly moving with one national swing) can't separate close models.

**Three audiences, three messages.**

| Audience | What they care about | Message |
|---|---|---|
| Open-model builders (HF community, fine-tuners, small labs) | A fair fight against bigger models; a reason to post a checkpoint | "Point me at a HF revision. One GGUF weights file of at most 11 GiB runs on my GPU; anything bigger self-runs as Tier A. Every Tier S model gets the same packet and decoding. I receipt the freeze; Bitcoin anchoring isn't live yet (B1)." |
| Forecasting community (Metaculus, Good Judgment, EA Forum, superforecaster Twitter) | Calibration, proper scores, honest CIs; whether LLMs beat markets | "Daily forecasts on 75 midterm questions, log-scored against a status-quo reference baseline fixed before the season (a market baseline only if B2 ships). Bootstrap CIs by day." |
| AI evaluation researchers and the press | Contamination, reproducibility, the election angle | "A forecasting comparison anyone can re-run: frozen weights, the same daily packet for every model, greedy decoding. One season is too small to separate close models." |

**Name and voice.** Keep "Future Challenge". Season naming: "Season 1: 2026 US Midterms". Tone: dry, technical, no hype, numbers with intervals. Never call a model "the winner" before the headline gate (≥ 7 scored days) and never before final resolution.

## 2. Assets (build once, reuse)

1. **Site** (GitHub Pages, Phase 5): landing explainer, boards, trajectory charts, entry pages, "How to enter in 5 commands", "How to verify a freeze in 3 commands".
2. **Launch post** (~1500 words, on the site + cross-posted): why a forecasting comparison should be replayable, the commitment-precedes-entropy mechanism, what Season 1 asks, how to enter, what we will publish and when. Include one diagram of the chain: weights hash → OTS/Rekor anchor → daily packet hash → forecast → outcome.
3. **"Verify it yourself" snippet**: three shell commands that fetch a receipt, an anchor proof and a packet and confirm the ordering. This is the shareable artefact.
4. **Daily trajectory image**: auto-generated PNG of P(Democratic House) and P(Democratic Senate) per entry vs market, posted daily by a workflow. The chart is the content engine.
5. **Participation guide** (`docs/participation.md`): freeze, register, verify; Tier S hardware budget; Tier A path; licence allowlist; terms.
6. **Press/FAQ page**: what this is not (not a prediction market, no money, no endorsements), who runs it, how to contact.
7. **Hugging Face presence**: an `future-challenge` HF organisation hosting (a) a dataset repo with daily packets and outputs (CC BY-SA), (b) the in-house entries' GGUF files mirrored with their hashes, (c) a model card template for entries. HF is where the target audience already is.

Drafts of items 2, 3 and 6 and of the §3 posts are in docs/launch/ (2026-10-16).

## 3. Channels and sequence

| When | Channel | Action |
|---|---|---|
| T−7 (10-19) | Direct outreach | DM 10–15 people: open-model fine-tuners active on HF, two or three forecasting-community figures, two eval researchers. Ask for one thing each: an entry, a quote, or a retweet on launch day. |
| T−3 (10-23) | HF | Create org, publish the dataset repo skeleton with the question set and one dry-run packet; post in HF forums/Discord "coming Monday". |
| **T0 (10-26)** | Site + launch post | Publish. Post on X/Twitter (thread: 1 claim, 1 chart, 1 verify snippet, 1 link), Bluesky, LinkedIn. |
| T0 | Hacker News | "Show HN: Future Challenge, frozen LLMs forecast the 2026 midterms daily" with the verify snippet in the first comment. |
| T0 | Forecasting community | Posts on the EA Forum and LessWrong (cross-post), Metaculus discussion, r/MachineLearning and r/forecasting. |
| T0 | HF | Post + Space embedding the trajectory chart; open the dataset repo. |
| T0 → 11-03 | Daily | Trajectory chart auto-posted each day at 18:30 UTC after reveal (workflow + one-line commentary written by the author, never by a model). |
| T+4 (10-30) | Mid-season post | "What ten frozen models think four days out": forecast spread, parse rates and disagreement so far (nothing resolves before 11-03, so no calibration yet); call for Tier A entries. |
| 11-02 | Election-eve post | Final pre-election snapshot; freeze the board image; explain what resolution looks like. |
| 11-04 → 11-10 | Results | Provisional results post as calls stabilise; which models beat the market baseline on which families; CIs front and centre. Pitch to 2–3 journalists covering AI/elections with the verify snippet. |
| 12-15 / 01-06 | Final | Final scores post; season archive; announce Season 2 (next domain) and open the RFC for rules changes. |

## 4. Recruiting entries

- **In-house (guaranteed)**: three open-weight Tier S models chosen at freeze; these make the board non-empty on day one.
- **Open-model builders**: the hardware budget (one GGUF weights file of at most 11 GiB) is a feature: it favours clever small models over scale, which is exactly the crowd that will enter. Offer to run any Apache/MIT/Llama/Gemma-licensed GGUF.
- **Labs with APIs**: Tier A, self-run. Message: "Your model's forecasts on identical inputs, timestamped, next to open models. Transcripts published." Expect few before election day; the ask is for Season 2.
- **Forecasting teams**: Tier H human baselines are welcome as separate-board entries; they make the LLM comparison interesting.
- **Friction removal**: entry = one YAML + one PR; CI tells you what is wrong; the freeze command prints the receipt. Target: under 15 minutes from reading the guide to a registered entry.

## 5. What we measure

| Metric | Launch week target | Season target |
|---|---|---|
| Registered external entries | 2 | 8 |
| Site visitors (Pages analytics) | 2,000 | 10,000 |
| HN front page / X thread impressions | once / 50k | — |
| Verify-snippet runs (download count of a receipt bundle) | 50 | 300 |
| Press mentions | 0–1 | 2–3 |
| Teams who say they will enter Season 2 | — | 5 |

## 6. Risks and lines we will not cross

- **Being read as partisan.** Publish forecasts for both chambers, all races, with baselines; never editorialise about outcomes; the daily commentary describes model behaviour, not politics.
- **"Markets already do this."** Answer: yes, and the market is our baseline; the question is whether a frozen model can match it from a fixed packet, and whether that transfers to domains without markets.
- **A model embarrasses itself.** Expected and fine; the parse-rate and calibration pages exist so that failure is legible, not hidden.
- **Overclaiming.** No "AI beats markets" headlines from n = 10 days. Every public number carries its interval.
- **Election misinformation framing.** Every chart and post carries the label "AI model forecasts, not statements of fact or endorsements". Its wording still has to be reconciled with legal-review §2.7 (B7).

## 7. Checklist (updated as executed)

- [ ] 10-19 outreach list drafted and sent
- [ ] 10-23 HF org + dataset skeleton public
- [ ] 10-26 site live; launch post; X/Bluesky/LinkedIn; HN; EA Forum/LW; Reddit; HF post
- [ ] daily chart workflow on from 10-26
- [ ] 10-30 mid-season post
- [ ] 11-02 election-eve post
- [ ] 11-04..10 results post + journalist pitches
- [ ] 12-15 / 01-06 final post + Season 2 announcement

## 8. Launch blockers for marketing claims

Every `TODO(author): [BLOCKER Bn]` line in docs/launch/ points to a row here; before a piece is posted, either Bn has shipped and I update the wording, or the piece keeps its plain not-live wording.

| ID | Not live yet | Evidence | Claims it blocks | To unblock | Wording until then |
|---|---|---|---|---|---|
| B1 | External anchoring (OpenTimestamps + Rekor) | fc/anchor.py null_anchor records "not-anchored"; STATE.md blockers | "timestamped on Bitcoin", "anchored", freeze time = anchor time, the verify.md anchor step, §1 builders row, §2 item 3 | plan 02-02 privileged workflow; one real .ots checked with `uv run fc verify` | "Anchoring to Bitcoin isn't live yet; until it is, the freeze time rests on my signature on the receipt." |
| B2 | Market baseline | questions/2026-us-midterms/markets.json is `{}`; the packet's prediction_markets section is "unavailable"; legal-review §2.1 and §2.4 open | "against a market baseline", "vs market" on the chart, results that "beat the market baseline", §1 forecasting row, §2 item 4, §3 11-04 row | fill markets.json; record the Polymarket/Kalshi terms decision | "The reference baseline is a status-quo rule fixed before the season; market prices aren't in the packet yet." |
| B3 | Tier S runner image | no Docker or NVIDIA container toolkit on the GPU box; image never built; season.json runner_image_digest is null; the dry run used `--local` | "networking off", "container", "pinned runner image", run reproducibility | install Docker and the toolkit, run `make image`, record the digest | "Runs will use a Docker container with networking off; the image isn't built yet, and the dry run ran outside it." |
| B4 | In-house entries | none frozen, committed or receipted (STATE.md todo 2) | "the board is non-empty on day one", any in-house model names | `uv run fc freeze` (thinking off), `uv run fc commit`, `uv run fc service submit` for each | "My own entries aren't frozen yet." |
| B5 | GitHub Pages and Actions | pages.yml and daily-round.yml have never run live; no site URL; no daily manifests or packets yet | site and board links, daily rounds, the "How to enter in 5 commands" page | enable Actions and Pages; workflow_dispatch one dry day | link the repository instead of the site |
| B6 | Hugging Face org | org not created; no dataset repo | §2 item 7, §3 10-23 and T0 HF rows, HF forum post links | create the org and dataset skeleton by 10-23 | leave out HF org links |
| B7 | Legal-review open items | docs/legal-review.md §3 checklist unchecked; §2.7 statute check open; the label wording differs between legal-review §2.7, participation.md §6 and this kit | every public post; the label text | counsel skim; terms decisions; one label string everywhere | do not post until the §3 items are closed or knowingly accepted |
| B8 | Daily chart image | fc/season_board.py renders inline SVG; no PNG export and no posting workflow | X chart post, §2 item 4, §3 daily row | add a PNG export and workflow, or screenshot by hand | attach a screenshot of the board |
| B9 | Evaluator image digest | season.json evaluator_image_digest is null | "anyone can re-score a round from public artifacts" | build and pin the evaluator image before the first T_open | "The scoring code is public; the evaluator image digest isn't pinned yet." |
| B10 | Hub public key | no hub public key is published in the repo; `uv run fc verify --hub-key` needs it to check receipt signatures | verify.md check 3, any "check the receipt signature" claim | publish the hub public key at a fixed public path | leave out the receipt-signature step |
| B11 | Participation-guide commands | docs/participation.md and README.md use bare `fc` (a shell builtin); the guide's packet-viewing subcommand does not exist in fc/cli.py; `pip install future-challenge[hf]` assumes a PyPI release nobody has checked | "How to enter in 5 commands", any copy-paste command that points to the guide | switch the guide and README to `uv run fc` after git clone and `uv sync --extra hf` | the launch kit uses `uv run fc` from a clone |
| B12 | Truth resolver | target-data/truth not built (STATE.md todo 8, Phase 7) | results posts from 11-04, any statement that scores exist | build the Wikipedia results snapshotter | "Scoring starts when results are certified." |

Pre-publish check: `grep -n "TODO(author)" docs/launch/<file>.md` must print nothing for the piece being posted.
