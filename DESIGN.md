# Future Challenge — an open, contamination-proof evaluation hub for AI models

*Design document, v0.1 — 2026-08-28*

---

## 0. TL;DR

Static benchmarks die by contamination: once a test set exists on the internet, every later model has plausibly trained on it. The COVID-19 / Flu Forecast Hubs solved this for one domain by construction: **participants commit predictions before the ground truth exists**, and the truth is produced later by the world, not by the organizers. Nothing that does not yet exist can leak.

Future Challenge generalizes that mechanism to AI models across many capabilities. One invariant runs through every part of the design:

> **The commitment always precedes the entropy.**
> A participant commits (a prediction, or a frozen model) at time `T_commit`. Everything that determines the score — the real-world outcome, or the random seed that instantiates a fresh task — comes into existence strictly after `T_commit`, from a source neither the participant nor the organizer controls.

Three paradigms share this invariant:

| Paradigm | What is committed | What arrives later | What it measures |
|---|---|---|---|
| **F — Forecast** | A probabilistic prediction about the future | The real-world outcome | World modeling, calibration, reasoning under uncertainty, information use |
| **G — Generated fresh task** | A frozen model (weights hash / container digest / API fingerprint) | A public randomness beacon that seeds a task generator with a programmatic verifier | Generalization on math, code, formal reasoning, puzzles, data analysis — anything with a verifier |
| **S — Sealed agent** | An executable container | Fresh tasks + fresh environments, run by the hub under a logged network policy | Autonomous agent capability, with tool use and the ability to prove no human was in the loop |

Everything else — commit/reveal with external timestamp anchoring, strictly proper scoring rules, mandatory coverage, pairwise relative skill, contribution-to-ensemble, verified identity, tiered guarantees, reproducible open evaluation, frozen per-round rules — exists to close the remaining gaps that the temporal invariant alone does not close. Section 9 enumerates 34 attacks and the mitigation for each, and Section 16 is honest about what remains.

---

## 1. Why this, why now

### 1.1 What is broken about static benchmarks

1. **Contamination.** Test items are on the web; crawls pick them up; models memorize. Decontamination by n-gram matching is unreliable against paraphrase, translation, and synthetic rewrites.
2. **Unverifiable claims.** "We decontaminated" is a claim only the lab can check.
3. **Held-out secrecy is fragile.** A private test set is only as trustworthy as the organization holding it, and every API call against it leaks items to the provider.
4. **Goodhart.** Once a benchmark becomes a target, the ecosystem optimizes the number rather than the capability; static sets cannot refresh.
5. **Self-reporting.** Most published scores are produced by the party being scored, with unpublished scaffolds, sampling settings, and retry counts.

### 1.2 What the forecast hubs got right

The CDC COVID-19 Forecast Hub (and its siblings under the hubverse) has been doing contamination-free evaluation since 2020:

- Deadline Wednesday 23:00 ET; reference date the following Saturday; horizons −1..3 weeks.
- Submissions are PRs into a public repo; CI validates the schema; the merge commit is a public timestamp.
- Target data (NHSN hospital admissions) is published *after* the deadline, by an independent party (CDC/NHSN), and its revision history is retained.
- Scoring uses the Weighted Interval Score (a strictly proper scoring rule) and relative skill against a baseline, computed over a common set of targets so that selective participation cannot inflate a score.
- A baseline model and an ensemble are always present, so "beat the trivial thing" and "add value to the crowd" are both measurable.
- Missing submissions are not silently ignored; relative scoring is pairwise on shared targets and coverage is reported.

Everything in that list transfers. What does *not* transfer directly is the scope: the hubs measure one skill (epidemiological forecasting). Future Challenge keeps the mechanism and widens the aperture.

### 1.3 Prior art and the gap

| System | Freshness mechanism | Gap this design closes |
|---|---|---|
| CDC / hubverse forecast hubs | Truth postdates deadline | Single domain; git timestamps only; no model-identity verification |
| CASP (protein structure) | Experimental structures released after prediction deadline | Biennial, single domain — but it is the proof that the pattern works for hard science |
| Metaculus AI Benchmark, ForecastBench (FRI), FutureBench, Prophet Arena | Future questions, LLM forecasters | Forecasting only; largely self-run; no fresh-task or sealed tiers; limited cryptographic commitment |
| M6, Numerai | Live financial rounds | Finance only; Numerai obfuscates features rather than using future entropy |
| LiveBench, LiveCodeBench, SWE-bench-Live | Questions from recently published content | Content *exists on the web* at question time; models with later cutoffs benefit; freshness lag not provable |
| Kaggle code competitions | Hidden test set, organizer runs code | Test set exists before submission; trust in organizer |
| Dynabench | Adversarial human-in-the-loop data collection | Items exist before evaluation |

The gap: no system combines (a) truth-postdates-commitment across *many* capabilities, (b) cryptographic, externally anchored commitments on both predictions and model identity, (c) verifiable randomness for task instantiation, (d) hub-executed sealed evaluation, and (e) the forecast hubs' statistical hygiene (proper scores, coverage, pairwise relative skill, ensemble contribution).

---

## 2. Design principles

1. **Truth postdates commitment.** Non-negotiable for every scored item.
2. **Entropy is external and verifiable.** Nothing that instantiates a task or resolves a question is chosen by a single party after seeing submissions.
3. **Commitments are externally anchored.** The hub's own database and git history are not the root of trust; Bitcoin-anchored timestamps and a public transparency log are.
4. **Only strictly proper scoring rules.** Honest reporting must be the unique optimal strategy.
5. **Coverage is mandatory.** You cannot pick the questions you answer without it showing on the leaderboard and in the score.
6. **Everything is public after reveal.** Submissions, rationales, logs, evaluation code, data vintages, resolver reasoning.
7. **Evaluation is reproducible by anyone.** Pinned containers; hash of evaluator committed before resolution; independent re-runs encouraged and rewarded.
8. **Rules are frozen per round.** Changes take effect only in rounds that open after the change is published.
9. **Guarantees are tiered, not pretended.** A self-reported API run and a hub-executed sealed container do not get the same badge.
10. **Baselines and the crowd are always on the board.** Persistence, climatology, the hub ensemble, human forecasters, and (where available) prediction markets.
11. **Identity has history.** Pseudonymous is fine; disposable is not. A team's full record is scored, not its best ticket.
12. **Uncertainty in rankings is reported.** No leaderboard without confidence intervals and minimum sample sizes.

---

## 3. Anatomy of a round

Every round, regardless of paradigm, has the same skeleton. All times UTC.

```
T_open      question set / task-family set / data vintage published
T_commit    commitment hashes due            (nothing after this counts)
T_entropy   public beacon fires; hub reveals its secret S      (> T_commit)
T_reveal    plaintext submissions due; hub publishes all       (F only)
T_run       hub executes sealed containers on generated tasks  (G, S only)
T_resolve   ground truth observed / verifier run
T_final     revision window closed; scores frozen
```

### 3.1 Forecast round (weekly cadence, mirrors the hubs)

| Step | When | What |
|---|---|---|
| `T_open` | Mon 00:00 | Round manifest: questions, resolution specs, data-vintage snapshot hash, rules version |
| `T_commit` | Wed 23:59 | `C = SHA-256(submission_bytes ‖ nonce)` submitted via API or PR; hub returns signed receipt |
| `T_reveal` | Thu 12:00 | `(submission_bytes, nonce)` submitted; hub verifies hash; publishes all submissions, Merkle root, ensemble |
| `T_resolve` | rolling | Truth sources polled on schedule; provisional scores published |
| `T_final` | target date + revision window (default 28 d) | Final vintage locked; scores frozen |

An unrevealed commitment is scored as missing (worst-case imputation, see §8.4). A reveal that does not match its hash is invalid and public.

### 3.2 Fresh-task round (monthly cadence)

| Step | When | What |
|---|---|---|
| `T_open` | Day 0 | Task families (generator + verifier code, pinned) and budget published. Drand round number `R` announced. |
| `T_commit` | Day 7 00:00 | Model commitment: weights hash, or OCI image digest, or API model-id + behavioral fingerprint (see §7). Hub commits `H(S)` for its own secret `S`. |
| `T_entropy` | Day 7 01:00 | Drand round `R` fires (`β_R`). Hub reveals `S`. `E = SHA-256(β_R ‖ S)`. Task seed `σ_i = SHA-256(E ‖ family_id ‖ i)`. Anyone can regenerate the exact task set. |
| `T_run` | Day 7–9 | Tier S: hub runs containers. Tier A: teams run within 48 h and commit outputs. |
| `T_resolve` | Day 9 | Verifiers run; outputs + logs published |
| `T_final` | Day 16 | Dispute window closed |

Why both `β_R` and `S`: the beacon alone could in principle be biased by a beacon-network participant; the hub secret alone could be chosen after seeing commitments. Combining them means an attacker must control both. `H(S)` is published *before* `T_commit` and anchored externally, so the hub cannot pick `S` after the fact.

### 3.3 Sealed-agent round

Same as 3.2 with one change: the committed object *is* the executable, and the hub runs it under a declared network policy (§7.4). Tier S is the only tier that can carry the "no human in the loop" badge.

---

## 4. What to forecast (paradigm F)

### 4.1 Start from the structure, not the virus

Disease forecasting is a good evaluation target because of *how the process behaves*, not because pathogens are special. Six properties do the work:

| # | Property | What it looks like in epi | Why it matters for evaluating a model |
|---|---|---|---|
| P1 | **Contagion / diffusion dynamics** | Growth driven by current state (R_t), waves, saturation, decay | Extrapolation fails at turning points; requires a mechanistic prior |
| P2 | **Latent state seen through noisy, delayed, revised surveillance** | True infections vs. reported admissions; backfill; preliminary vs. final | Nowcasting is half the problem; punishes models that trust the last data point |
| P3 | **Spatial panel** | 50 states, counties, hierarchy | Correlated units give statistical power and test partial pooling |
| P4 | **Behavioral feedback** | Forecasts and policy change behavior, which changes the outcome | Reflexivity; tests reasoning about response, not just signal |
| P5 | **Exogenous shocks** | Variants, holidays, mandates | Requires reading the world (news, policy) not just the series |
| P6 | **Independent public truth on a schedule** | NHSN Fridays | Resolution is mechanical and outside every participant's control |

A domain scoring on P1–P5 with P6 satisfied is a sibling of disease forecasting. The list below is organized by *which mechanism* each domain shares, so the evaluation can deliberately cover all six rather than accidentally testing extrapolation six different ways.

Selection criteria for any target remain: (1) truth produced by an independent, durable, public source; (2) resolution is mechanical or has a written rule a stranger would apply the same way; (3) cost to manipulate the outcome is high relative to any plausible prize; (4) enough targets per round for statistical power; (5) a meaningful naive baseline exists; and (6) — new — the domain fills a gap in the P1–P5 coverage matrix (§4.4).

### 4.2 Domains grouped by shared mechanism

**A. Literal contagion — non-human or non-biological (P1, P3, P5)**

| Target | Truth source | Cadence / horizon | Notes |
|---|---|---|---|
| Newly exploited vulnerabilities: weekly additions to CISA Known Exploited Vulnerabilities; time from CVE publication to KEV listing | CISA KEV, NVD | Weekly / 1–4 wk | Digital SIR. "Log4Shell" is a wave |
| Patch adoption: % of internet-exposed hosts on a fixed version of a product at horizon *h* after release | Shodan / Censys version counts (snapshot archived at `T_open`) | Weekly / 1–8 wk | Recovery curve analog |
| Dependency version uptake: % of dependents of a top-500 npm/PyPI package on the new major *h* weeks after release | deps.dev, ecosyste.ms | Weekly / 1–12 wk | Clean S-curve with an "R_t" |
| Animal disease: HPAI-affected commercial flocks and birds by state; African swine fever outbreaks by country | USDA APHIS weekly; WOAH WAHIS | Weekly / 1–4 wk | Same mathematics as human epi; far less modeled; causally chained to prices (§4.3) |
| Plant disease and pests: spotted lanternfly county detections; emerald ash borer; wheat rust reports | USDA, EDDMapS, state ag departments | Monthly / 1–3 mo | Slow spatial contagion |
| Wildfire: active large fires and acres burned per GACC region; downstream PM2.5 / AQI per metro | NIFC daily, AirNow | Daily / 1–14 d | Landscape contagion; shock-driven |
| Pollen: daily counts at NAB stations by type | National Allergy Bureau | Daily / 1–7 d | Seasonal onset and peak targets, like flu season |
| Cultural waves: concurrent players for games released within the last 30 days; stars for repos created within the last 30 days; Wikipedia pageview spikes | SteamDB, GitHub, Wikimedia | Hourly–daily / 1–14 d | Fast waves with saturation; peak-size and peak-timing targets |

**B. Latent state with delayed, revised surveillance — nowcast track (P2, P3)**

| Target | Truth source | Cadence / horizon | Notes |
|---|---|---|---|
| Provisional drug-overdose deaths by state, and the final count they will be revised to | CDC VSRR provisional counts | Monthly / −6..0 mo | Revision structure nearly identical to COVID deaths; no pathogen dynamics |
| Non-infectious syndromic ED visits: heat-related illness, firearm injury, mental-health | NSSP ESSENCE public data | Weekly / 0–4 wk | Same pipeline as the hub; no virus |
| Revision targets: "what will last month's nonfarm payrolls be revised to?"; GDP advance → third estimate | BLS, BEA | Monthly | A meta-target only a hub can pose |
| City crime counts by category | NYC / Chicago / LA open data | Weekly / 1–4 wk | Delayed reports, seasonal, behavior-driven |

**C. Cascades on networks (P1 as propagation, P5)**

| Target | Truth source | Cadence / horizon | Notes |
|---|---|---|---|
| Customers without power by state; hours to 90 % restoration after a named storm | poweroutage.us, EIA-417 | Hourly / 6–72 h, event-triggered | Restoration curve ≈ recovery curve |
| Flight cancellations and delay-minutes by airport/airline | BTS, FlightAware | Daily / 1–3 d | Weather shock propagates through the network |
| Chokepoint transits: daily ship transits through Suez, Panama, Malacca, Hormuz; port calls at top-20 ports | IMF PortWatch (AIS) | Daily / 1–4 wk | Disruptions propagate along supply chains; strikes/wars as shocks |
| Internet traffic anomalies and outage-incident counts | Cloudflare Radar, provider status pages | Daily | Cascade + shock |
| Financial stress: bank failures, stablecoin depegs, liquidation volume | FDIC, on-chain data | Event-driven | Classic economic contagion; truth from regulator or chain |

**D. Flows with policy shocks and reflexivity (P2, P4, P5)**

| Target | Truth source | Cadence / horizon | Notes |
|---|---|---|---|
| Border encounters by sector | CBP monthly | Monthly / 1–3 mo | Strongly policy-reactive; revised |
| Conflict and protest events by country / admin-1 | ACLED weekly | Weekly / 1–4 wk | Diffusion across regions; public weekly |
| Regulatory queues: USCIS backlog by form; FDA PDUFA decisions (binary, scheduled dates); court docket clearance; patent pendency | USCIS, FDA, US Courts, USPTO | Monthly; event-driven | Inflow / outflow / backlog dynamics; policy changes the rates |

**E. Adoption S-curves — technology "epidemics" (P1, P3, slow)**

| Target | Truth source | Cadence / horizon |
|---|---|---|
| IPv6 adoption % by country | Google IPv6 statistics | Daily / 1–12 wk |
| TLS 1.3 / HTTP/3 share | Cloudflare Radar | Weekly |
| EV share of new registrations by state | State DMV / Argonne | Monthly / 1–6 mo |
| Python / Node version share of downloads | PyPI BigQuery, npm | Weekly |
| Downloads of newly released open models in first *h* weeks | Hugging Face | Weekly / 1–8 wk |

Clean, public, near-zero manipulation incentive; excellent for long-horizon and turning-point targets.

**F. Physical processes with an existing prospective-testing tradition (P1, P3, P5)**

| Target | Truth source | Cadence / horizon | Notes |
|---|---|---|---|
| Aftershock counts (M ≥ 3) in the 7 / 30 days after a M ≥ 6 mainshock, by region | USGS ComCat | Event-triggered | CSEP already runs prospective earthquake-forecast testing — the other forecast-hub lineage; import its evaluation rules |
| Kp index, sunspot number, M/X-class flare counts | NOAA SWPC, SILSO | Daily / 1–27 d | Cycle + shocks; long-running prediction panels |
| River gauge levels and flood-stage exceedance; snow-water equivalent; Drought Monitor category | USGS, SNOTEL, US Drought Monitor | Daily–weekly | Weather-adjacent but a distinct latent-state, spatial-panel problem |
| Phenology: cherry bloom dates; bird migration density; coral bleaching alert level | Kyoto record, BirdCast, NOAA Coral Reef Watch | Seasonal | Onset-date targets |

**G. Retained from v0.1 (macro, epi, sports, geopolitics, elections, culture/commerce)** — unchanged; see the question-spec template in §4.6. Weather stations stay as a P6-perfect calibration domain.

### 4.3 Chain targets — where a reasoning model can beat a curve-fitter

Single-series extrapolation is where statistical models are already strong. Causal chains reward reading the world:

| Chain | Links (each scored with its own proper rule) |
|---|---|
| Avian flu → prices | HPAI flocks affected → egg CPI (BLS) → food-at-home CPI |
| Fire → health | Acres burned (NIFC) → metro AQI (AirNow) → NSSP respiratory ED visits |
| Supply chain | Port strike / chokepoint closure → transits (PortWatch) → freight index (Drewry/Freightos) → import price index |
| Rates | FOMC decision → Freddie Mac 30-yr rate → MBA mortgage applications → building permits |
| Vulnerability lifecycle | CVE disclosed → KEV listing → patch adoption % → incident reports |

Scoring: each link is a normal question; additionally, a **chain-consistency score** penalizes forecasts whose links are jointly implausible (e.g., a large HPAI wave with flat egg prices) using a pre-registered structural check. Chains are labelled and kept on a secondary board until the consistency scoring has been dry-run for two rounds.

### 4.4 Coverage matrix

✓ strong, ~ partial, – absent. The starter set is chosen so every column has at least two ✓ domains.

| Domain | P1 contagion | P2 latent + revised | P3 spatial panel | P4 feedback | P5 shocks | P6 public truth |
|---|---|---|---|---|---|---|
| Human epi (existing hubs) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ weekly |
| Animal / plant disease | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ weekly |
| Vulnerability exploitation + patching | ✓ | ✓ | ~ by vendor/ASN | ✓ | ✓ | ✓ weekly |
| Overdose deaths (provisional → final) | – | ✓✓ | ✓ | ~ | ~ | ✓ monthly |
| Wildfire + smoke AQI | ✓ | ~ | ✓ | ~ | ✓ | ✓ daily |
| Power outages | ✓ cascade | ~ | ✓ | – | ✓ | ✓ hourly |
| Migration encounters | ~ | ✓ | ✓ sectors | ✓✓ | ✓ | ✓ monthly |
| Conflict events (ACLED) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ weekly |
| Adoption curves | ✓ | – | ✓ | ~ | ~ | ✓ daily–monthly |
| Cultural waves (Steam, repos) | ✓ | – | – | ✓ | ✓ | ✓ hourly |
| Aftershocks (CSEP rules) | ✓ | – | ✓ | – | ✓ | ✓ |
| Shipping transits | ✓ cascade | – | ✓ chokepoints | ~ | ✓✓ | ✓ daily |
| Backlogs / queues | – | ✓ | ~ | ✓ | ✓ | ✓ monthly |
| Macro prints | – | ✓ revisions | – | ✓ | ✓ | ✓ monthly |
| Weather stations | – | – | ✓ | – | ✓ | ✓ daily |

### 4.5 Starter set for Phase 0–1

Chosen for structural similarity, thin existing modeling community (so the ensemble is not just a copy of a published SOTA model), and negligible manipulation risk.

| # | Domain | Targets per round | Target types | Baseline |
|---|---|---|---|---|
| 1 | HPAI flocks + egg-price chain | ~50 states + 2 chain links | Quantile trajectory; chain consistency | Seasonal naive; last-year same-week |
| 2 | Vulnerability exploitation | KEV additions (weekly count); time-to-KEV for the 20 highest-CVSS CVEs published in the last 7 days; patch % at 2/4/8 wk for 5 products | Quantile; survival/binary | Historical hazard rate |
| 3 | Overdose nowcast | ~50 states, horizons −6..0 months (final value of provisional months) | Quantile | Historical revision ratio |
| 4 | Conflict events | ~40 countries × admin-1 aggregates | Quantile | Seasonal naive |
| 5 | Power outages (event-triggered) | States in the forecast cone of a named storm | Quantile; time-to-90 %-restored | Last-3-storms mean |
| 6 | Adoption panel | IPv6 by country (30), EV share by state (50), 5 new major releases | Quantile at 4/8/12 wk | Logistic fit |
| 7 | Cultural waves | 10 upcoming game releases, 20 new repos | Peak size; peak timing; value at day 14 | Genre / topic median |
| 8 | Aftershocks (event-triggered) | Each M ≥ 6 mainshock | Count at 7 / 30 d; spatial grid (CSEP format) | ETAS reference |
| 9 | Macro + epi + weather stations (from v0.1) | as before | as before | as before |

Event-triggered rounds (5, 8) open within 6 hours of the trigger with a 12-hour commit window; the trigger definition is pre-registered so the round cannot be opened selectively.

### 4.6 Beacon-selected sub-targets, exclusions, and the question spec

**Beacon-selected sub-targets** ("500 Wikipedia articles", "200 weather stations", "20 new repos") are chosen by the previous round's entropy and published at `T_open`. Huge, fresh target set each round, nothing to pre-compute, and targeted manipulation becomes expensive.

**Explicitly excluded**: anything about identifiable private individuals; deaths of named persons; individual crimes or attacks (aggregate counts only); anything tradable enough that a hub leaderboard could move a market or create an insider-trading incentive at retail granularity (broad macro prints and freight indices are fine; single-stock prices are not).

**Adjacent hubs to borrow from**: CSEP (earthquakes; prospective testing since 2007), the Scenario Modeling Hub (conditional / scenario forecasts — the right template for chain targets), NOAA SWPC verification (space weather), M6 (finance). None cover vulnerability exploitation, animal disease, migration, conflict, adoption, or cultural waves — open ground.

Every question is a machine-readable record. Nothing resolves without one.

```yaml
question_id: 2026-w36-vuln-kev-additions
family: vulnerability
statement: "Number of CVEs added to the CISA Known Exploited Vulnerabilities catalog during ISO week 2026-W38."
output_type: quantile
quantile_levels: [0.01,0.025,0.05,0.1,...,0.95,0.975,0.99]   # hubverse 23-level set
unit: count
resolution:
  primary_source: "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
  vintage_rule: "value_as_of(T_final)"     # or: first_release, final_revision
  fallback_sources: ["NVD CISA-KEV flag"]
  void_if: ["catalog feed unavailable > 14 days", "catalog inclusion criteria changed"]
  expected_resolution_date: 2026-09-21
  resolver: automated                     # or: council
horizon_days: 14
rules_version: 2026.08
```

---

## 5. Fresh-task families (paradigm G)

The forecast paradigm cannot measure "can this model solve a novel combinatorics problem" — nature does not produce those on schedule. Paradigm G does: the **task distribution is public**, the **instance is seeded by post-commitment entropy**, and a **programmatic verifier** produces ground truth. Training on the distribution is allowed and equals studying for the exam; the exam questions themselves cannot exist before the model is frozen.

Requirements for a family to be admitted:

1. **Generator** `gen(seed) → task` is deterministic, open-source, pinned by hash at `T_open`.
2. **Verifier** `verify(task, answer) → score ∈ [0,1]` is deterministic and does not require a model as judge (LLM-judge families are allowed only as a secondary, clearly labelled track).
3. **Instance space** ≥ 2^64 distinct tasks, or continuous parameters, so enumeration is impossible; documented in the family card.
4. **Novelty check**: for each generated instance, nearest-neighbour distance against a public index (benchmark corpora, common crawl samples, GitHub) is computed and published; families whose instances collide with public items are retired.
5. **Difficulty is parameterized** so the family can be re-tuned without changing the mechanism, and a **human baseline** is collected on a sample.

| Family | Generator sketch | Verifier |
|---|---|---|
| Symbolic math | Random expression trees, integrals, recurrences, modular arithmetic with parameters drawn from seed | SymPy / exact arithmetic |
| Combinatorics & counting | Grammar-sampled counting problems over small structures with brute-force reference | Exhaustive enumeration |
| Program synthesis | Sample a random program from a DSL; derive I/O examples and a natural-language spec; hidden test suite | Hidden tests |
| Repo-level bug fix | Fork of an open-source repo pinned at `T_open`; seed selects a mutation-injected fault; task = failing test description | Repo test suite |
| Formal proof | Seed-generated algebraic/ordering identities stated in Lean 4 | Lean kernel |
| Grid puzzles (ARC-like) | Sample transformation program from a DSL; render 3 demonstrations + 1 query | Exact match |
| Constraint puzzles | Sudoku variants, Zebra puzzles, scheduling with seeded constraints | Solver |
| Data analysis | Synthetic dataset with known generative parameters; questions about effects, missing values, confounders | Truth from generator |
| Long-context needle | Seeded synthetic corpora with planted facts and distractors | Exact match |
| Procedural environments | Seeded text/grid worlds with reward functions | Environment return |
| Post-commit news QA (agents with web) | Questions whose answers are events after `T_commit`, auto-resolved from APIs | Automated |

Per-round instances are published in full after `T_run` so anyone can regenerate and re-verify.

---

## 6. Submission format and commitment protocol

### 6.1 Format

Hubverse-compatible so existing tooling (`hubValidations`, `hubEvals`, `hubEnsembles`, `hubData`) works unchanged:

```
model-output/<team>-<model>/<round_id>.parquet
  columns: round_id, question_id, output_type, output_type_id, value
model-metadata/<team>-<model>.yml
  team, model, contributors, license, methods, data_inputs, tier, model_identity, primary: true/false
```

Additions on top of the hub format:

- `rationale/<team>-<model>/<round_id>.md` — free text (or model transcript) justifying the forecast. Hashed with the submission. Published at reveal. Enables post-hoc analysis of reasoning quality, plagiarism detection, and audit.
- `cost` fields: tokens in/out, wall-clock, USD, number of samples, retries. Self-reported in tiers A/B; measured in tier S.
- `sidecar/<team>-<model>/<round_id>.json` — commitment record (below).

### 6.2 Commit / reveal

```
commit  = SHA-256( canonical_bytes(parquet) ‖ canonical_bytes(rationale) ‖ nonce_256bit )
POST /rounds/{r}/commit  { model_id, commit, signature_over(commit ‖ r ‖ model_id) }
← receipt { commit, server_time, hub_signature, merkle_position }
```

- Team keys: Ed25519, registered at onboarding; public key in `model-metadata`.
- Hub signs every receipt. Team retains it. A receipt is proof against the hub later "losing" a submission.
- Every 10 minutes the hub publishes a Merkle root of all commits received so far, and anchors it via **OpenTimestamps** (Bitcoin) and appends it to **Sigstore Rekor** (public transparency log). Roots are also posted to the repo and a social account, giving independent witnesses.
- At `T_reveal`, plaintext + nonce arrive; hub recomputes and checks. Matching submissions are merged; mismatches are published as invalid.

Consequence: neither the team nor the hub can alter, backdate, or suppress a submission after `T_commit`, and no party sees any plaintext before `T_commit`.

### 6.3 Why not just git?

Git commit timestamps are author-controlled; history is rewritable by anyone with force-push; a GitHub merge timestamp trusts GitHub and the hub maintainers. The PR workflow stays for convenience, but the root of trust is the anchored Merkle root.

---

## 7. Model identity and freeze verification

The forecast paradigm does not need to know what produced a prediction — the prediction is the commitment. Paradigms G and S do: the claim is "this specific model, frozen before the tasks existed, achieved X." Three identity classes, in decreasing strength:

### 7.1 Open weights
`model_identity = { type: weights, sha256: <weights>, inference_image: <OCI digest>, sampling: {...} }`. Strongest. Hub can run it, anyone can reproduce.

### 7.2 Sealed container
`model_identity = { type: container, oci_digest: sha256:..., entrypoint, network_policy }`. Model may be closed inside the image, but the image is frozen and hub-executed. Strong, if paired with a network policy that prevents phoning home to a mutable service (§7.4).

### 7.3 API model
`model_identity = { type: api, provider, model_id, fingerprint: {...} }`. Weakest. The provider can change what the id points to. Mitigations:

- **Behavioral fingerprint**: a fixed panel of ~300 prompts at temperature 0 (and logprobs where exposed) captured at `T_commit` and again at `T_run`. Drift beyond a tolerance → entry flagged "model changed" and scored as a new, unverified entry.
- **Run immediately**: `T_run − T_entropy` ≤ 48 h to shrink the retraining window.
- **Provider attestation**: optional signed statement of the snapshot date; displayed but not trusted.
- **Tier ceiling**: API entries cannot exceed Tier A.

### 7.4 Network policy (tiers S and A)

| Policy | Egress | Use |
|---|---|---|
| `closed` | None | Pure capability on generated tasks |
| `allowlist` | Named data APIs only (e.g., FRED, NOAA), through a logging proxy | Forecasting agents that need data |
| `open-logged` | Anything, every request/response hashed and published | Web-agent tasks; prediction-market lookups become visible rather than forbidden |

Egress logs are published with the results. "Used Polymarket" is not cheating in an open-information forecasting tier — it is information use — but it must be visible, and there is a `closed`/`allowlist` sub-leaderboard for those who want to measure reasoning without it.

### 7.5 Tiers

| Tier | Who executes | Identity verified | Badge |
|---|---|---|---|
| **S — Sealed** | Hub, from committed container, under network policy, in gVisor/Firecracker sandbox with CPU/GPU/time/token quotas | Yes (digest) | "Autonomous, hub-verified" |
| **A — Attested** | Team, within the window; submits outputs + full transcripts + pipeline hash; subject to random re-execution audits (≥10 % of entries per round); must reproduce within tolerance | Partial | "Autonomous, self-run, audited" |
| **B — Self-reported** | Team; outputs only | No | Listed on a separate board; no headline rank |
| **H — Human / hybrid** | Humans, or humans + models | n/a | Baseline; separate board |

Tier is a property of the *entry*, and the headline leaderboards are per tier. Mixing tiers into one ranking is exactly the kind of false uniformity §2.9 forbids.

---

## 8. Scoring

### 8.1 Proper scoring rules by output type

| Output type | Score | Formula |
|---|---|---|
| Quantiles (K levels) | Weighted Interval Score | `WIS = 1/(K+½) · [ ½·|y−m| + Σ_k (α_k/2)·IS_{α_k}(y) ]`, `IS_α = (u−l) + (2/α)(l−y)·1[y<l] + (2/α)(y−u)·1[y>u]` |
| Full distribution / samples | CRPS | `CRPS(F,y) = ∫ (F(x) − 1[x≥y])² dx` |
| Binary probability | Log score (primary), Brier (secondary) | `−log p(y)`; `(p−y)²` |
| Categorical | Log score | `−log p_y` |
| Point (verifier ∈ {0,1}) | Accuracy, with `pass@1` and budget-normalized variants | — |
| Point (verifier ∈ [0,1]) | Mean verifier score | — |

No thresholded or improper metric (e.g., "within 10 %", accuracy on a median) is ever a primary metric. Proper rules make hedging, over-confidence, and hedging-against-the-metric all strictly worse than honesty. Log score is clipped at `p = 0.001` to bound one catastrophic miss.

### 8.2 Relative skill (from the hubs)

Raw scores are not comparable across question sets with different scales and difficulties, and teams do not answer identical sets. Use the pairwise scaled relative skill (Cramer et al., 2022):

```
θ_{m,m'} = ( Π_{q ∈ Q_m ∩ Q_m'} S_m(q) / S_m'(q) )^{1/|Q_m ∩ Q_m'|}      # geometric mean ratio over shared questions
θ_m      = ( Π_{m'} θ_{m,m'} )^{1/M}
rel_skill_m = θ_m / θ_baseline                                            # < 1 beats baseline
```

Computed per domain and overall. Only pairs with ≥ 20 shared resolved questions contribute.

### 8.3 Ranking uncertainty

- Bootstrap over questions (block bootstrap by week for serially correlated targets) → 95 % intervals on relative skill.
- Pairwise significance: a model is "ahead" of another only if the bootstrap interval of `θ_{m,m'}` excludes 1.
- Minimum 50 resolved questions across ≥ 3 rounds before appearing on the headline board.
- Leaderboard shows rank *ranges*, not just point ranks.

### 8.4 Coverage and imputation

- Each round has a **core set** (mandatory) and **optional** questions. Missing a core question is scored as the worst of (baseline score, 75th percentile of all submitted scores on that question) — the hub practice of "you cannot benefit from silence."
- Coverage % is a column on the board; boards can be filtered by minimum coverage.
- Unrevealed commitments = missing.

### 8.5 Calibration and sharpness

Reported separately, never mixed into the ranking score (WIS/CRPS already reward both):
- PIT histograms; 50/90/95 % interval coverage.
- Brier decomposition (reliability / resolution / uncertainty) for binary.
- Sharpness: mean interval width.

### 8.6 Contribution to the ensemble

The hub ensemble (median/mean of quantiles, or linear pool) is published every round. For each model, `Δ_m = S(ensemble_{−m}) − S(ensemble)` (leave-one-out). Positive = the model adds information the crowd lacks. This is the single best antidote to ensemble-copying: a copy has `Δ ≈ 0`.

Also reported: similarity to the ensemble (mean CRPS distance), so "original but wrong" and "copy of the crowd" are both visible.

### 8.7 Cost-adjusted boards

Tier S measures tokens, wall-clock, GPU-seconds. Secondary boards: score at fixed budget; Pareto frontier of score vs. cost.

### 8.8 Multiple entries per team

A team designates one **primary** entry per round before `T_commit`. Team-level rankings use the primary. Secondary entries are scored and listed but labelled, and a team's *all-entries median* is shown alongside its primary, so a spray-and-pray strategy is visible.

---

## 9. Threat model

Attack → mitigation → residual. "Closed" means the mechanism removes the attack; "reduced" means it raises cost or makes it detectable.

### A. Contamination and leakage

| # | Attack | Mitigation | Residual |
|---|---|---|---|
| A1 | Truth in pretraining data | Truth postdates `T_commit` by construction | **Closed** |
| A2 | Nowcast targets partially observable at deadline (preliminary data, leaks) | Data-vintage snapshot hashed at `T_open`; `vintage_rule` in question spec; score against declared vintage; nowcasts labelled as such | Closed for what is declared; unknown leaks remain (low) |
| A3 | Outcome pre-announced before `T_commit` (embargoed reports, scheduled announcements) | Question review checklist requires `expected_resolution_date > T_commit + margin`; void rule if any public source reported the outcome before `T_commit` | Reduced |
| A4 | Fresh-task generator predictable (guessable seed) | Seed = `H(drand_R ‖ S)`; `H(S)` anchored before `T_commit`; `R` fires after `T_commit` | Closed unless attacker controls both drand threshold majority and the hub secret |
| A5 | Task family small enough to enumerate / pre-solve | Instance-space ≥ 2^64 requirement; novelty check vs public index; families retired on collision | Reduced (a family with hidden structure could still be "learned"; this is the intended "study the distribution" allowance) |
| A6 | API model retrained between `T_commit` and `T_run` on similar tasks | Fingerprint drift detection; `T_run − T_entropy ≤ 48 h`; Tier A ceiling for API | Reduced; cannot be closed for opaque APIs (§16) |
| A7 | Provider logs tier-S prompts during `T_run`, trains, and scores better next round | Every round draws new instances from new entropy; only the distribution is learnable (allowed) | Closed for instances; distribution learning is by design |
| A8 | Model given internet at forecast time pulls data that is already the outcome | For F, if the outcome is public it fails A3 and is void. Otherwise data use is legitimate | Closed |

### B. Submission integrity

| # | Attack | Mitigation | Residual |
|---|---|---|---|
| B1 | Late submission, clock manipulation, backdating | Server time on receipt; Merkle root every 10 min anchored to Bitcoin + Rekor; receipts signed | Closed |
| B2 | Post-deadline edit | Hash commitment; reveal must match | Closed |
| B3 | Hub operator alters or drops a submission | Signed receipt held by team; public Merkle proof; anyone can verify inclusion | Closed (detectable and provable) |
| B4 | Hub operator reads submissions before deadline and tips a colluder | Operator only sees hashes before `T_commit` | Closed |
| B5 | Hub operator picks entropy after seeing commitments | `H(S)` committed and anchored before `T_commit`; drand independent | Closed |
| B6 | Team copies another team's submission | Nothing is revealed until after `T_commit` | Closed within a round; cross-round copying is visible via similarity |
| B7 | Front-running the ensemble | Ensemble computed after reveal | Closed |
| B8 | Forged receipt or identity | Ed25519 keys; hub signature; key rotation with public log | Closed |

### C. Statistical gaming

| # | Attack | Mitigation | Residual |
|---|---|---|---|
| C1 | Lottery: enter 200 variants, publicize the winner | One pre-designated primary per team; all entries public and permanently listed; team all-entries median displayed; entry cap (e.g., 5 per round) | Reduced; external publicity of a secondary can be checked by anyone against the board |
| C2 | Sybil teams to bypass C1 | Identity verification (org email/ORCID/GitHub-account-age ≥ 1 yr, or a refundable stake); probation: new teams appear on the board only after 3 rounds; similarity clustering across teams is published | Reduced |
| C3 | Selective participation (submit only when confident) | Mandatory core set; worst-case imputation; coverage column; pairwise relative skill on shared questions | Closed for core; visible for optional |
| C4 | Horizon / target cherry-picking | Core set spans all horizons; per-horizon boards | Closed |
| C5 | Withdraw after seeing early truth | No withdrawal after `T_commit`; unrevealed = missing = worst-case | Closed |
| C6 | Exploit the metric (hedge to the median, spike mass, etc.) | Strictly proper rules only; clipped log score | Closed |
| C7 | Overfit to the leaderboard | No fixed test set exists; every round is fresh out-of-sample | Closed |
| C8 | Copy the ensemble | Allowed; `Δ_m ≈ 0` and high similarity expose it; contribution board | Closed as a *deception*, allowed as a strategy |
| C9 | Collude: split predictions across teams to guarantee one wins | Same as C1/C2; cross-team similarity and anti-correlation analysis | Reduced |
| C10 | Claim significance from noise | Bootstrap CIs; minimum N; rank ranges | Closed |

### D. Ground truth

| # | Attack | Mitigation | Residual |
|---|---|---|---|
| D1 | Manipulate the outcome (bot pageviews, star a repo, bet to move odds) | Target selection criterion (3); beacon-chosen large target sets; anomaly detection on truth series; void + ban on detection | Reduced; small-aggregate targets carry residual risk and are labelled |
| D2 | Data revision after resolution | `vintage_rule` per question; `T_final` revision window; both first-release and final scores stored; the *declared* one ranks | Closed by definition |
| D3 | Source discontinued or changed | Pre-specified fallback hierarchy; void rule; voided questions excluded for everyone | Closed |
| D4 | Ambiguous resolution | Machine-readable spec; "stranger test" review; dry-run resolution on historical data before a question ships; council for the residual | Reduced |
| D5 | Biased or corrupt resolver | Automated resolvers preferred; council of ≥ 5 with published reasoning and recusal rules; 7-day appeal window; council members may not participate | Reduced |
| D6 | Insider at the truth source | Diversify sources; prefer sources with their own integrity controls (statistical agencies); nothing else possible | Residual, low |

### E. Model identity and execution

| # | Attack | Mitigation | Residual |
|---|---|---|---|
| E1 | Humans do the work, labelled as AI | Tier S (hub executes); Tier A random re-execution audits and transcript requirement; badge only for S | Closed for S; reduced for A; open for B (and labelled) |
| E2 | Model swapped behind an API id | Fingerprint at `T_commit` and `T_run`; drift → new entry | Reduced |
| E3 | Container phones home to a mutable oracle / human | `closed` or `allowlist` policy in sandbox; `open-logged` publishes every request | Closed under `closed`/`allowlist`; visible under `open-logged` |
| E4 | Container escape, resource abuse, exfiltration of other teams' data | gVisor/Firecracker; one container per VM; no shared volumes; quotas; no secrets in the runner | Closed to the sandbox's guarantee |
| E5 | Evaluation code bug or tampering | Evaluator pinned and its hash anchored at `T_open`; open source; deterministic; independent re-run bounty; results signed | Closed (detectable) |
| E6 | Nondeterministic model runs used to retry until lucky | Tier S: single run with fixed seed and budget; Tier A: `n_samples` and `retries` mandatory fields, `pass@1` primary | Closed for S |

### F. Governance and systemic

| # | Attack | Mitigation | Residual |
|---|---|---|---|
| F1 | Rules changed mid-round | Rules versioned; round manifest pins version; changes apply only to rounds opening after publication | Closed |
| F2 | Question authors leak the set | Set finalized and published to everyone simultaneously at `T_open`; authors barred from that round; authorship logged | Reduced (the leak has limited value because commitment is still pre-truth; the only gain is prep time) |
| F3 | Organizer's own model advantaged | Organizers may enter only in a labelled "organizer" class; all organizer entries Tier S | Reduced |
| F4 | Leaderboard manipulation via disputes | Disputes public, time-boxed, resolved by council with reasoning | Reduced |
| F5 | Hub disappears; history lost | Everything in git + archive.org + Zenodo DOI per round; anchored roots let anyone rebuild trust from the data alone | Closed |

---

## 10. Ground-truth policy

1. **Sources** are named per question with a primary and ordered fallbacks.
2. **Vintages**: the hub snapshots every truth source daily; each snapshot is hashed and committed to `target-data/vintages/<source>/<date>.parquet`. Questions declare which vintage resolves them.
3. **Revisions**: first-release and final-at-`T_final` scores are both computed and stored. Only the declared one ranks; the other is on a secondary board so revision sensitivity is visible.
4. **Voiding**: voided questions are removed for all participants (not scored as missing). Void reasons are enumerated in the spec, and the council may void for unforeseen ambiguity by a ≥ 4/5 vote with written reasoning.
5. **Disputes**: 7 days after provisional resolution; public; council decision within 7 more days; decisions become precedent and are added to the resolution guidelines.
6. **Dry-run test**: before a question family ships, its resolver is run on ≥ 12 months of historical data to check it resolves mechanically.

---

## 11. Infrastructure

```
future-challenge/
├── hub-config/           admin.json, tasks.json per round (hubverse)
├── rounds/<round_id>/
│   ├── manifest.json     questions, task families, data vintage hash, rules version, drand round R, H(S)
│   ├── entropy.json      β_R, S, E   (after T_entropy)
│   └── roots/            Merkle roots + OTS proofs + Rekor entries
├── model-metadata/       one YAML per entry (identity, tier, keys)
├── model-output/         hubverse parquet
├── rationale/            per-entry, per-round
├── sidecar/              commitment records + receipts
├── target-data/
│   └── vintages/         daily snapshots, hashed
├── task-families/<name>/ generator.py, verifier.py, card.md, tests/
├── eval/                 scoring (WIS, CRPS, log, relative skill, bootstrap, contribution); pinned image
├── runner/               sealed executor: pull-by-digest, sandbox, proxy, quotas, log publisher
├── fingerprint/          API probe panel + drift detector
├── leaderboard/          static site built from eval outputs
└── docs/                 rules (versioned), question checklist, council procedures
```

Components:

- **Submission API + PR bot**: teams may use either; the bot just wraps the API. Validation via `hubValidations` in CI.
- **Commitment service**: receipts, Merkle roots, OpenTimestamps + Rekor anchoring, root mirroring to repo and social.
- **Entropy service**: drand client (League of Entropy, quicknet); hub secret commit/reveal; seed derivation; publishes `entropy.json`.
- **Sealed runner**: Kubernetes jobs on gVisor (CPU) / Firecracker (GPU) with egress through an mitmproxy-based logging proxy applying the entry's policy; results signed with the runner key.
- **Evaluator**: R/Python package built on `hubEvals`/`scoringutils`; containerized; digest in the round manifest.
- **Leaderboard**: static; per tier, per domain, per horizon; CIs; coverage; contribution; cost; time-series of skill per entry.
- **Archive**: each finalized round packaged to Zenodo with a DOI.

The forecast paradigm (F) can run entirely on existing hubverse tooling plus the commitment service — that is the MVP.

---

## 12. Governance

- **Steering group** (5–9 people, mixed institutions) owns the rules document; changes require public RFC ≥ 14 days.
- **Resolution council** (≥ 5, rotating, no active participants) resolves non-automated questions and disputes; reasoning published.
- **Question editors** author and review question sets against the checklist; barred from the rounds they author.
- **Security/audit rotation**: a named external party re-executes the evaluator on one random round per quarter and publishes a report. Reproduction discrepancies carry a bounty.
- **Rules versioning**: `rules_version` is `YYYY.MM`; every round manifest pins one.
- **Code of conduct + terms**: no outcome manipulation, no sybils, no resolver contact; violations → entry voided, team suspended, decision public.

---

## 13. Baselines always on the board

| Baseline | Paradigm | Purpose |
|---|---|---|
| Persistence / last value | F | Floor |
| Seasonal naive, climatology | F | Floor with seasonality |
| Simple statistical model (ETS/ARIMA/quantile regression) | F | "Did you beat a weekend of scripting" |
| Hub ensemble | F, G | The crowd |
| Human forecaster crowd (Metaculus community / GJP superforecasters where questions overlap) | F | Human reference |
| Bookmaker closing odds / prediction-market price at `T_commit` | F (sports, geopolitics) | Market reference; archived at `T_commit` |
| Random / majority verifier baseline | G | Floor |
| Human sample on 50 instances per family | G | Human reference, refreshed yearly |
| Previous-year frontier open model, frozen | G, S | Progress reference |

---

## 14. Cadence and launch plan

**Phase 0 — Forecast MVP (months 1–2)**
Weekly rounds on the §4.5 starter set: epi (existing hub targets), macro, weather stations, HPAI + egg-price chain, KEV vulnerability exploitation, overdose nowcast, ACLED conflict events, adoption panel; event-triggered outage and aftershock rounds added once the trigger definitions have been dry-run. Commitment service with OTS/Rekor anchoring. Hubverse tooling for validation and scoring. Leaderboard with relative skill, CIs, coverage, contribution. Tiers A and B only. Rules v2026.10.

**Phase 1 — Fresh tasks (months 3–4)**
Entropy service. Three families (symbolic math, program synthesis, grid puzzles) with verifiers and novelty checks. Monthly G rounds; Tier A with audits; API fingerprinting.

**Phase 2 — Sealed execution (months 5–8)**
Runner with sandbox and logging proxy. Tier S. Cost boards. Repo-level bug-fix and procedural-environment families. Human baselines.

**Phase 3 — Institutionalize (months 9–12)**
Council, external audits, Zenodo archiving, RFC process, first annual report. Consider a standing "AI Forecasting Cup" per year with rolling qualification from weekly rounds.

**Ongoing**: retire families on collision; add domains; refresh human baselines; publish yearly analysis of what predicts skill (rationale length, data access, cost).

---

## 15. Worked example — one forecast round

1. Mon 2026-09-07 00:00 UTC — `rounds/2026-w37/manifest.json` published: 1,540 questions (52 epi, 18 macro, 200 stations × 5 days, 500 pageview series, 50 HPAI + 2 chain links, 26 vulnerability, 50 overdose nowcast × 7 lags, 40 conflict, 85 adoption, 30 sports, 12 geopolitical), core set flagged, data vintage `sha256:…`, `H(S)`, drand round `R = 12,345,678` (fires Wed 23:59:30).
2. Team `acme-llmcaster` runs their pipeline Tuesday, produces parquet + rationale, computes commit hash with a fresh nonce, POSTs it Wed 21:14; receives receipt. Their primary is `acme-llmcaster-v3`; a secondary `acme-llmcaster-v3-noweb` is also committed.
3. Wed 23:59:59 — commitments close. Root anchored. 61 entries.
4. Thu 00:00 — drand fires; hub reveals `S`; next round's 500 pageview articles and 200 stations are drawn from `E`.
5. Thu 12:00 — reveals close; 59 match, 1 mismatch (public), 1 unrevealed (missing). Ensemble published.
6. Rolling — NOAA daily, Wikimedia daily, BLS on 2026-09-11, NHSN Fridays. Provisional board updates nightly.
7. Wed 2026-10-07 — `T_final` for the epi targets; scores frozen; round packaged; DOI minted.

---

## 16. What this cannot guarantee (residual risks, stated plainly)

1. **Opaque API models cannot be proven frozen.** Fingerprinting detects large changes, not fine-tunes that preserve probe outputs. The only clean answer is open weights or a sealed container — hence Tier S. Everything else gets a weaker badge.
2. **Tier A depends on audits.** A team that runs its own pipeline can insert a human. Random re-execution catches inconsistency, not a human who is consistently better than the model in a way the model can't reproduce — which the re-execution would in fact reveal as non-reproduction. Accepting ~10 % audit rate is a cost/assurance trade-off.
3. **Ground truth can be attacked at the margin** for small aggregates (an individual Wikipedia article, a small repo). Mitigation is scale and anomaly detection; a determined attacker with a budget can still poison a handful of targets. They are labelled, and voided when detected.
4. **Question ambiguity is a human problem.** Council process reduces it; precedent accumulates; it never reaches zero.
5. **Coverage bias.** Families and domains chosen by the organizers define "capability". Publish the selection process; accept community-proposed families.
6. **Forecasting skill ≠ general intelligence.** Paradigm F measures a real, important, hard-to-fake skill. Paradigm G broadens it to verifiable reasoning. Neither measures open-ended creative work; an LLM-judge track can be run but must be labelled as such.
7. **Statistical power takes time.** A new entry needs ≥ 3 rounds and ≥ 50 questions. Weekly cadence means a month before a meaningful rank.
8. **Reflexivity** in AI-about-AI questions (predicting the board itself). Keep them few and off the headline score.
9. **Cost.** Tier S requires compute the hub pays for; caps and sponsorship needed. Entry fees or stakes shift cost but raise the barrier to academic teams — prefer sponsorship + quotas.

None of these weaken the core guarantee: **for every scored item, the information that determines the score did not exist when the participant committed.** That is the strongest anti-contamination property an evaluation can have, and it is what the forecast hubs proved workable for six years.

---

## Appendix A — Commitment record

```json
{
  "round_id": "2026-w37",
  "model_id": "acme-llmcaster-v3",
  "commit": "sha256:9f2c…",
  "nonce_hash": "sha256:41ab…",
  "team_signature": "ed25519:…",
  "hub_receipt": {
    "server_time": "2026-09-09T21:14:03.118Z",
    "merkle_root": "sha256:e1d0…",
    "merkle_path": ["…"],
    "hub_signature": "ed25519:…",
    "ots_proof": "base64:…",
    "rekor_log_index": 184223991
  },
  "reveal": { "received": "2026-09-10T08:02:44Z", "verified": true }
}
```

## Appendix B — Entropy record

```json
{
  "round_id": "2026-10-G01",
  "hub_secret_commitment": "sha256:H(S)",
  "hub_secret": "hex:…",
  "drand": { "chain": "quicknet", "round": 12345678, "randomness": "hex:…", "signature": "hex:…" },
  "E": "sha256(β_R || S)",
  "seed_derivation": "sha256(E || family_id || index)",
  "families": { "symbolic-math@1.3.0": {"n": 400}, "program-synthesis@0.9.2": {"n": 200} }
}
```

## Appendix C — Question checklist (editors)

- [ ] Resolution source is independent of every plausible participant and of the hub.
- [ ] `expected_resolution_date > T_commit + 24 h`; no scheduled pre-announcement exists.
- [ ] Resolver ran mechanically on ≥ 12 months of history with no manual intervention.
- [ ] Vintage rule and fallback hierarchy written.
- [ ] Void conditions enumerated.
- [ ] Manipulation cost estimated and recorded; target labelled if < "high".
- [ ] Not about an identifiable individual; not retail-tradable.
- [ ] Baseline defined and computable at `T_open`.
- [ ] Stranger test: two editors resolved a synthetic past instance independently and agreed.

## Appendix D — Glossary

- **Commit / reveal**: publish a hash now, the content later; proves the content existed at hash time.
- **drand**: distributed, publicly verifiable randomness beacon (League of Entropy).
- **OpenTimestamps**: proof that a hash existed before a given Bitcoin block.
- **Rekor**: Sigstore's public, append-only transparency log.
- **WIS**: weighted interval score, proper scoring rule for quantile forecasts.
- **CRPS**: continuous ranked probability score.
- **Relative skill**: pairwise geometric-mean score ratio over shared questions, divided by the baseline's.
- **Vintage**: a dataset as it existed on a given date.
- **Tier S/A/B/H**: sealed / attested / self-reported / human.
