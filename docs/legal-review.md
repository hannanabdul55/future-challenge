# Legal review — Season 1 (2026 US midterms)

Status: **draft for counsel review, 2026-10-16.** Written by the project, not by a lawyer. It is a structured issue list with the project's current reading and the mitigations built into the design. Items marked **OPEN** need a lawyer or a primary-source check before launch; items marked **MITIGATED** are handled by design choices that are already in the requirements.

## 1. What the product is, legally

A non-commercial research benchmark that (a) stores hashes of publicly hosted AI model files, (b) runs those models on public information to produce probability forecasts about the party outcome of US public elections, (c) publishes those forecasts and scores, and (d) accepts the same from third parties. No money changes hands: no entry fee, no prize, no wagering, no sale of data.

## 2. Issue list

### 2.1 Election forecasting and election-betting law — MITIGATED

Publishing probability forecasts about elections is ordinary political speech in the United States (polling aggregators, academic models and newspapers do it). The legal exposure around election prediction in 2026 concerns *event contracts* and *wagering*: the CFTC treats election contracts as derivatives, several states enforce election-betting bans against prediction markets, and the 2024 Kalshi litigation turned on "staking something of value on the outcome". The hub has no stake, no contract, no payout, no counterparties. Mitigations already in the design:

- No prizes, fees or stakes (PROJECT.md § Out of Scope).
- The board ranks models by proper scores; nothing is exchanged on the basis of an election outcome.
- The hub *reads* prediction-market prices into the daily packet (see 2.4) but never places or facilitates trades.

**OPEN (low)**: confirm with counsel that displaying market-implied probabilities as a baseline does not constitute promotion of unlawful gaming in any state. Mitigation if needed: show the market baseline only as a score, not as live prices.

### 2.2 Contest / sweepstakes law — MITIGATED

A leaderboard with prizes is a "skill contest" and in some states a purchase/consideration restriction applies. Season 1 offers no prize of any kind. If a sponsor later offers prizes, the challenge becomes a skill contest: objective judging criteria (proper scores) exist, but official rules, eligibility (age, jurisdiction, export control), and tax reporting must be written first. **Do not add prizes mid-season.**

### 2.3 Targets about individuals — MITIGATED with carve-out

DESIGN.md §4.6 excludes targets about identifiable individuals. Every per-race question is phrased as *which party wins the seat*, resolves from official results, and names no candidate in its statement. Candidates are public figures standing in a public election; a party-outcome forecast is not a statement of fact about a person and carries no defamation exposure. Excluded explicitly (rules v2026.10): questions about a candidate's health, conduct, legal exposure, withdrawal, or any personal attribute.

### 2.4 Packet data licences — PARTLY OPEN

Every daily packet ships with `attribution.json` listing each source, licence, retrieval time and URL. Current reading per source:

| Source | Use in packet | Licence / terms | Status |
|---|---|---|---|
| Wikipedia polling and results tables (via MediaWiki API) | Extracted tables (pollster, dates, sample, toplines) | CC BY-SA 4.0 — attribution and share-alike | **MITIGATED**: packet licensed CC BY-SA 4.0; attribution file names the article revision ids. Facts (poll numbers) are not copyrightable; the table arrangement is reused under CC BY-SA. |
| GDELT (headlines, URLs) | Titles + URLs only, no article text | GDELT data is free for research and non-commercial use with attribution | **MITIGATED**: titles and links only; attribution included. |
| Polymarket Gamma API (market prices) | Daily price snapshot for matching markets | Public read-only endpoints; redistribution terms not verified | **OPEN**: read Polymarket Terms of Use before launch. Fallback: store prices privately, publish only the derived baseline score. |
| Kalshi public API (market prices) | Daily price snapshot | Public endpoints; redistribution terms not verified | **OPEN**: same as Polymarket. Kalshi is a CFTC-regulated DCM; its market data terms may restrict redistribution. |
| State election offices (certified results, resolution) | Winner by party | Public records; most states publish without licence | **MITIGATED**: facts; per-state terms checked at resolution time. |
| house.gov / senate.gov rosters (final seating) | Seat counts by party | US government works, public domain | **MITIGATED**. |

Explicitly **not** used: AP race calls as data (AP terms forbid redistribution of AP content; the hub records "race called" from Wikipedia tables, which cite their own sources), Cook/Sabato/Inside Elections ratings (proprietary; not in the packet, not in baselines), Ballotpedia (terms prohibit automated extraction without licence), RealClearPolling and Silver Bulletin averages (proprietary compilations).

### 2.5 Model licences — PARTLY OPEN

The hub downloads, hashes and executes weights that participants point to. The hub is a *user* of each model under its licence:

- Apache-2.0 / MIT models (many Qwen releases, Mistral, others): no restriction on evaluation or redistribution of outputs.
- Llama Community Licence (3.x / 4): permits use and redistribution with a copy of the licence and "Built with Llama" notice where applicable; acceptable-use policy applies; the 700 M MAU clause does not apply. Outputs may be used freely.
- Gemma Terms of Use: permits use subject to the prohibited-use policy; requires passing the terms downstream with any distribution. The hub distributes *hashes and outputs*, not weights.
- Gated repos (any licence): the hub must accept the gate as a HF user; the entry YAML records the licence id and the registration CI blocks licences that forbid benchmarking or hub execution.

**OPEN**: the registration gate needs a short allowlist of licence ids (apache-2.0, mit, llama3.x, llama4, gemma, qwen, cc-by-4.0, cc-by-sa-4.0) and a manual-review path for others. Weights are never redistributed by the hub; only Hugging Face hosts them.

### 2.6 Hugging Face terms — MITIGATED

The hub uses `huggingface_hub` to read repo metadata and download files, within HF's terms for public repos. No scraping; API rate limits respected. Participants host their own checkpoints under their own accounts. The hub may later create an HF organisation to mirror in-house entries; that is ordinary use.

### 2.7 AI-generated election content laws — MITIGATED

By mid-2026, 31 states regulate AI-generated election content; most require disclosure labels and target *synthetic media depicting candidates* (deepfakes). Probability forecasts are not depictions of any person and are not "materially deceptive media". Mitigations regardless:

- Every published forecast is labelled "AI model output; a forecast, not a statement of fact or an endorsement" on the site and in the model-output files' metadata.
- No text generated by the models is published as if it were news; transcripts are published as evidence, with the same label.
- The hub does not advertise, endorse, or solicit votes for any candidate or party (keeps the project outside electioneering-communication rules; it accepts no money and makes no expenditures).

**OPEN (low)**: California AB 2655 / AB 2839 and Minnesota's statute were enjoined or narrowed in 2025–26; verify current status before launch so the site's label text matches whatever disclosure wording any surviving statute requires.

### 2.8 Federal election law (FEC) — MITIGATED

No contributions, no expenditures, no coordination with campaigns, no paid communications. A non-commercial benchmark publishing model forecasts is not a political committee and makes no electioneering communications. Keep it that way: no sponsorship from campaigns or party committees.

### 2.9 Participant terms — OPEN (needs writing)

Teams need a short participation agreement at registration (checkbox in the PR template):

- They own or are licensed to submit the model they point to; they grant the hub permission to download, hash, execute and publish outputs and transcripts.
- All submissions, receipts and scores are public and permanent (rules §6).
- No warranty; no liability for scores; disputes per rules §9.
- Export control: participants confirm they are not on a restricted-party list (standard clause; the hub ships nothing).
- Conduct: no manipulation of sources, no sybil teams (rules §10).

### 2.10 Privacy — MITIGATED

The hub stores team names, public keys, and a contact email for registration; nothing else personal. Transcripts contain only packet text and model output. No analytics on the static site beyond GitHub Pages defaults. GDPR applies to EU participants: the registration email is kept in the private repo settings, not the public ledger.

### 2.11 Defamation / false light — MITIGATED

The site publishes probabilities about party outcomes, race calls as recorded in cited public sources, and certified results. It makes no factual claims about candidates. Transcripts are model output over the packet; the packet contains headlines verbatim with links, which is quotation of published titles.

### 2.12 Security / misuse of the runner — MITIGATED

The hub executes third-party *weights* inside a reference image it controls, with no network and read-only mounts. Weights are data, not code; `safetensors` / GGUF formats are loaded without arbitrary deserialization (no `pickle`). Registration CI rejects `.bin`/`.pt`/`.pkl` weight files for Tier S.

## 3. Pre-launch checklist

- [ ] Counsel skim of this document (sections 2.1, 2.4, 2.7)
- [ ] Polymarket and Kalshi terms read; decision recorded (publish prices / scores only)
- [ ] Licence allowlist implemented in registration CI
- [ ] Participation terms text added to `docs/participation.md` and the PR template
- [ ] AI-generated label text on site, model-output metadata, and transcripts
- [ ] `attribution.json` emitted with every packet; packet licence CC BY-SA 4.0 declared

## 4. Sources consulted

- CRS Legal Sidebar LSB11441, "CFTC Issues Proposed Rule Regarding Prediction Markets" (2026)
- Pew Research, "Most states ban election betting in at least some instances" (June 2026)
- Covington & Burling, "What state AI election laws mean for the 2026 midterms" (September 2026); NCSL election-deepfake law tracker (June 2026)
- Associated Press Terms of Use (as republished by member stations); Ballotpedia developer terms; Meta Llama 3.x/4 Community Licence; Google Gemma Terms of Use; Wikimedia CC BY-SA 4.0 reuse terms; GDELT project terms
- Promotion-law summaries on skill contests and sweepstakes (industry sources; not authoritative)
