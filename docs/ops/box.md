# GPU box runbook and state record

The daily forecasting job runs on one personal GPU machine, referred to here only as
"the GPU box" (ssh alias `gpu`). This file records its state and how to operate it. It
contains no secrets: environment variables are listed by name only, never by value.

State recorded 2026-11-16 (plan 06-06).

## Host facts

| Item | Value |
|---|---|
| OS | Ubuntu 26.04.1 LTS inside WSL2 |
| GPU | NVIDIA GeForce RTX 4070 SUPER, 12 GB (about 6 GB was in use by another service at preflight) |
| RAM | 25 GB |
| Disk | 295 GB volume, 238 GB free |
| Up since | 2026-10-30 |
| systemd | enabled in WSL; the user manager is running; linger is on, so user timers run without a login session |
| Time zone | US Pacific (all fc timers use explicit UTC times) |
| Docker | not installed at preflight; installed by the author at the plan 06-06 checkpoint (see "Docker" below) |

An on-demand LLM user service shares the GPU. Its unit names live only in the box env
file's `FC_STOP_UNITS`, so this document does not name them.

## Layout

- `~/fc-ops` is the only checkout the daily job uses. It is a clean clone of
  `origin/main`, left on `main` and fast-forwarded with `git pull --ff-only`. Anything
  that has not been pushed to `origin/main` is not on the box.
- `~/future-challenge` is the author's separate training checkout. It has diverged from
  origin, is left alone, and still hosts the board publish cron. Nothing in the daily
  job reads, resets, commits in or pushes from it.
- `~/.config/fc/env` holds the job's environment (mode 600, box only).

Ops checkout verification (2026-11-16): `uv sync --extra dev --extra hf` and
`uv run pytest -q` pass (292 tests) at origin/main commit 2f6f7df.

## Push authentication

`git -C ~/fc-ops push --dry-run origin main` exits 0 non-interactively (checked
2026-11-16), so the daily job's pushes authenticate over SSH without a prompt.

## Keys

Generated on the box with `uv run fc keygen --out keys/<name>` under `umask 077`.

| Key | Private half (box only) | Public half (committed) |
|---|---|---|
| runner | `~/fc-ops/keys/runner/private.pem` | `hub-config/keys/runner.pub.pem` |
| hub | `~/fc-ops/keys/hub/private.pem` | `hub-config/keys/hub.pub.pem` |
| house | `~/fc-ops/keys/house/private.pem` | `hub-config/keys/house.pub.pem` |

Modes: `keys/` and each `keys/<name>/` directory are 700; each `private.pem` is 600.
The private halves are gitignored (`/keys/` at the repo root, plus `private.pem` at any
depth) and have never left the box. Only `public.pem` files were copied out.

SHA-256 fingerprints of the committed public PEM files:

| File | SHA-256 |
|---|---|
| `hub-config/keys/runner.pub.pem` | `a2ba5ebac042f64a2b03d9b1708427d51b31c586a123e2b0911e6607c9d3921e` |
| `hub-config/keys/hub.pub.pem` | `4800dc86d34a5c71804f2f7d1bd0827116456dfc2cca172f6d207d314f9474bd` |
| `hub-config/keys/house.pub.pem` | `112368888002852b52e1c311506d56cab40c3b2456459120ca55b152eeccf56b` |

## Docker

Not installed at preflight. The author installs Docker Engine and the NVIDIA Container
Toolkit (needs sudo) and then the smoke test is recorded here.

Status: pending (plan 06-06 Task 2 checkpoint).

## Environment file

Path: `~/.config/fc/env`, mode 600. Variable names (values are never recorded here):

`ALERT_WEBHOOK`, `ALERT_FORMAT`, `SEASON`, `IMAGE`, `RUNNER_KEY`, `RUNNER_PUB`,
`HUB_KEY`, `FC_STOP_UNITS`, `FC_MIN_FREE_MIB`, `FC_MANIFEST_WAIT_MIN`, `HF_HUB_OFFLINE`.

Which names are currently set on the box is recorded below once the file is baselined.
`FC_MIN_FREE_MIB` is a provisional placeholder until plan 06-08 replaces it with a
measured value.

## Pausing the job

```
systemctl --user disable --now fc-commit.timer fc-reveal.timer
```

Every manual action on the box is logged in `docs/ops/interventions.md`.
