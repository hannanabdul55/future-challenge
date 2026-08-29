# Rules — version 2026.08

Normative summary. DESIGN.md is the rationale; this file is what a round manifest pins.

1. **Rounds.** A round manifest is published at `T_open`. Commitments close at `T_commit`; reveals close at `T_reveal`. Times are UTC. Late is late.
2. **Commitment.** `commit = SHA-256(submission || rationale || nonce)`, signed with the team's registered Ed25519 key. The hub returns a signed receipt. Merkle roots of all commits are published every 10 minutes and anchored to OpenTimestamps and Sigstore Rekor.
3. **Reveal.** Plaintext plus nonce must reproduce the commitment. Mismatch = invalid (public). Unrevealed = missing.
4. **Coverage.** Core questions are mandatory. A missing core question is scored as the worse of the baseline score and the 75th percentile of submitted scores on that question. Voided questions are removed for everyone.
5. **Scoring.** Quantiles: WIS. Samples: CRPS. Binary/categorical: log score (clipped at 0.001), Brier secondary. Rankings use pairwise relative skill vs `fc-baseline` over shared questions (≥ 20 shared per pair); 95 % bootstrap intervals; ≥ 50 questions across ≥ 3 rounds for the headline board.
6. **Entries.** ≤ 5 entries per team per round; exactly one designated `primary` before `T_commit`. All entries are permanent and public.
7. **Identity.** Teams register a verified identity (organizational email, ORCID, or a GitHub account older than one year) and an Ed25519 public key. New teams appear on boards after three rounds.
8. **Tiers.** S sealed (hub-executed), A attested (self-run, transcripts, ≥ 10 % re-execution audits), B self-reported, H human/hybrid. Boards are per tier. API identities are capped at A.
9. **Ground truth.** Resolved per the question's `resolution` block. Provisional scores are updated as data arrives; final scores freeze at `T_final` (target date + 28 days unless the question says otherwise). Disputes: 7 days after provisional resolution, public, decided by the council within 7 days.
10. **Conduct.** No outcome manipulation, sybil teams, or contact with resolvers. Violations void the entry and suspend the team; decisions are public.
11. **Changes.** Rules are versioned `YYYY.MM`. A change applies only to rounds that open after it is published, following a public RFC of at least 14 days.
