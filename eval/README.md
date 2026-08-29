# eval

Scoring lives in `fc/eval/`. This directory holds the pinned evaluator image definition and outputs.

- `Dockerfile` builds the evaluator; its digest goes in the round manifest at `T_open` (DESIGN.md §9 E5).
- `out/` (git-ignored) receives `scores.csv`, `relative_skill.csv`, `contribution.csv` from `fc score`.

Reproduce any published round: `docker run --rm -v $PWD:/hub <evaluator@sha256:...> fc score ...`
