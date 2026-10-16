<!-- GSD:project-start source:PROJECT.md -->

## Project

**Future Challenge**

An open, contamination-proof forecasting challenge for frozen AI models. Participants freeze a model (a Hugging Face revision whose files are hashed and externally timestamped, or an API model fingerprinted at freeze) *before* the events it will forecast have happened; the hub runs each frozen model daily on the same curated information packet, publishes the forecasts, and scores them when the world produces the answer. Season 1 is the 2026 US midterm elections (2026-11-03): daily forecasts of chamber control, seat counts, and every Senate and gubernatorial race by party. Full rationale, threat model, and scoring design live in `DESIGN.md` (see §3.4 for the daily season). Planning lives in `.planning/` (PROJECT, REQUIREMENTS, ROADMAP, STATE).

**Core Value:** For every scored forecast, the model that produced it was provably frozen before the information that determines the score existed — and anyone can verify that chain (weights hash → anchored timestamp → daily packet hash → forecast → outcome) from public artifacts alone.

### Constraints

- **Invariant**: Nothing that determines a score may exist at `T_commit` — every question and resolver must pass the §4.6 checklist; any leak voids the question
- **Statistics**: Only strictly proper scoring rules as primary metrics; rankings carry bootstrap CIs and minimum-N gates — no headline rank from noise
- **Reproducibility**: Evaluator digest pinned in the round manifest before `T_open`; anyone must be able to re-score a round from public artifacts
- **Tech stack**: Python ≥ 3.11, `uv`, pandas/pyarrow, `cryptography` (Ed25519); hubverse schema compatibility for model-output; keep dependencies minimal so the evaluator container stays small and auditable
- **Ethics / legal**: No targets about identifiable individuals, named-person deaths, individual crimes, or retail-tradable instruments
- **Budget**: No paid infrastructure assumed for v1; prefer free public APIs, GitHub Actions, static hosting; OTS and Rekor are free
- **Cadence**: Weekly rounds; the system must tolerate late truth arrival and revisions without human intervention

<!-- GSD:project-end -->

<!-- GSD:stack-start source:STACK.md -->

## Technology Stack

Technology stack not yet documented. Will populate after codebase mapping or first phase.
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->

## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->

## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->

## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->

## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:

- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->

## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
