<!-- GSD:project-start source:PROJECT.md -->

## Project

**Future Challenge**

An open, contamination-proof evaluation hub for AI models, built on a mechanism proven in collaborative epidemiological forecasting: participants commit predictions (or frozen models) *before* the information that determines their score exists, and the truth is produced later by the world. It is for AI labs, academic forecasting teams, and independent modelers who want a benchmark that cannot be trained on, and for the community that wants to know which models actually reason about the world. Full rationale, threat model, and scoring design live in `DESIGN.md`.

**Core Value:** For every scored item, the information that determines the score did not exist when the participant committed — and anyone can verify that from public artifacts alone.

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
