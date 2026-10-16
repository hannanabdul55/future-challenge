# Marketing plan — Future Challenge, Season 1 (2026 US midterms)

Owner: the author (independent, personal brand). Budget: $0. Horizon: 2026-10-16 → 2027-01-06.

## 1. Positioning

**One line.** *Freeze your model before the election. We run it every day. The world grades it.*

**The claim nobody else makes.** Every existing AI-forecasting benchmark (ForecastBench, Prophet Arena, the Metaculus AI benchmark) asks a model, through an API, questions whose answers arrive later. None of them freeze open weights, anchor the freeze to Bitcoin and a transparency log, run every model on byte-identical inputs on hub hardware, and publish a chain anyone can verify offline. Future Challenge does all four. The pitch is not "another leaderboard"; it is "the first forecasting result you can audit without trusting us."

**Three audiences, three messages.**

| Audience | What they care about | Message |
|---|---|---|
| Open-model builders (HF community, fine-tuners, small labs) | A fair fight against bigger models; a reason to post a checkpoint | "Point us at a HF revision. 12 GB or less runs on our GPU; bigger self-runs as Tier A. Same packet, same prompt, same decoding. The freeze is timestamped on Bitcoin." |
| Forecasting community (Metaculus, Good Judgment, EA Forum, superforecaster Twitter) | Calibration, proper scores, honest CIs; whether LLMs beat markets | "Daily log scores on 75 midterm questions against a market baseline. Trajectories, not a single number. Bootstrap CIs by day." |
| AI evaluation researchers and the press | Contamination, reproducibility, the election angle | "A benchmark that cannot be trained on: the answers do not exist yet. Reproducible: weights hash + packet hash + greedy decoding." |

**Name and voice.** Keep "Future Challenge". Season naming: "Season 1: 2026 US Midterms". Tone: dry, technical, no hype, numbers with intervals. Never call a model "the winner" before the headline gate (≥ 7 scored days) and never before final resolution.

## 2. Assets (build once, reuse)

1. **Site** (GitHub Pages, Phase 5): landing explainer, boards, trajectory charts, entry pages, "How to enter in 5 commands", "How to verify a freeze in 3 commands".
2. **Launch post** (~1500 words, on the site + cross-posted): why static benchmarks are broken, the commitment-precedes-entropy mechanism, what Season 1 asks, how to enter, what we will publish and when. Include one diagram of the chain: weights hash → OTS/Rekor anchor → daily packet hash → forecast → outcome.
3. **"Verify it yourself" snippet**: three shell commands that fetch a receipt, an anchor proof and a packet and confirm the ordering. This is the shareable artefact.
4. **Daily trajectory image**: auto-generated PNG of P(Democratic House) and P(Democratic Senate) per entry vs market, posted daily by a workflow. The chart is the content engine.
5. **Participation guide** (`docs/participation.md`): freeze, register, verify; Tier S hardware budget; Tier A path; licence allowlist; terms.
6. **Press/FAQ page**: what this is not (not a prediction market, no money, no endorsements), who runs it, how to contact.
7. **Hugging Face presence**: an `future-challenge` HF organisation hosting (a) a dataset repo with daily packets and outputs (CC BY-SA), (b) the in-house entries' GGUF files mirrored with their hashes, (c) a model card template for entries. HF is where the target audience already is.

## 3. Channels and sequence

| When | Channel | Action |
|---|---|---|
| T−7 (10-19) | Direct outreach | DM 10–15 people: open-model fine-tuners active on HF, two or three forecasting-community figures, two eval researchers. Ask for one thing each: an entry, a quote, or a retweet on launch day. |
| T−3 (10-23) | HF | Create org, publish the dataset repo skeleton with the question set and one dry-run packet; post in HF forums/Discord "coming Monday". |
| **T0 (10-26)** | Site + launch post | Publish. Post on X/Twitter (thread: 1 claim, 1 chart, 1 verify snippet, 1 link), Bluesky, LinkedIn. |
| T0 | Hacker News | "Show HN: A forecasting benchmark for frozen AI models — the 2026 midterms, graded by reality" with the verify snippet in the first comment. |
| T0 | Forecasting community | Posts on the EA Forum and LessWrong (cross-post), Metaculus discussion, r/MachineLearning and r/forecasting. |
| T0 | HF | Post + Space embedding the trajectory chart; open the dataset repo. |
| T0 → 11-03 | Daily | Trajectory chart auto-posted each day at 18:30 UTC after reveal (workflow + one-line commentary written by the author, never by a model). |
| T+4 (10-30) | Mid-season post | "What ten frozen models think four days out": calibration so far, parse rates, which questions models disagree on; call for Tier A entries. |
| 11-02 | Election-eve post | Final pre-election snapshot; freeze the board image; explain what resolution looks like. |
| 11-04 → 11-10 | Results | Provisional results post as calls stabilise; which models beat the market baseline on which families; CIs front and centre. Pitch to 2–3 journalists covering AI/elections with the verify snippet. |
| 12-15 / 01-06 | Final | Final scores post; season archive; announce Season 2 (next domain) and open the RFC for rules changes. |

## 4. Recruiting entries

- **In-house (guaranteed)**: three open-weight Tier S models chosen at freeze; these make the board non-empty on day one.
- **Open-model builders**: the hardware budget (≤ 12 GB) is a feature: it favours clever small models over scale, which is exactly the crowd that will enter. Offer to run any Apache/MIT/Llama/Gemma-licensed GGUF.
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
- **Election misinformation framing.** Every chart and post carries the "AI model forecast, not a statement of fact" label (legal-review §2.7).

## 7. Checklist (updated as executed)

- [ ] 10-19 outreach list drafted and sent
- [ ] 10-23 HF org + dataset skeleton public
- [ ] 10-26 site live; launch post; X/Bluesky/LinkedIn; HN; EA Forum/LW; Reddit; HF post
- [ ] daily chart workflow on from 10-26
- [ ] 10-30 mid-season post
- [ ] 11-02 election-eve post
- [ ] 11-04..10 results post + journalist pitches
- [ ] 12-15 / 01-06 final post + Season 2 announcement
