# Verify it yourself

These checks tie one day's forecasts to the packet the models read and to freezes made before the day opened. The daily files appear from 2026-10-26, when the first round runs.

TODO(author): [BLOCKER B5] Post this only after the daily-round workflow has produced one day's files; until the site is up, link the repository.

## Setup

Every command is `uv run fc`, because bare `fc` is the shell's history editor in bash and zsh.

TODO(author): repo URL, in place of "$REPO_URL" below.
TODO(author): [BLOCKER B11] docs/participation.md still shows bare fc; point readers there only after it is fixed.

```bash
git clone "$REPO_URL" future-challenge && cd future-challenge
uv sync --extra hf
SEASON=2026-us-midterms
DAY=2026-10-27
```

## Check 1: the packet is the one the day pinned

The two hashes should match, and every bundle should carry the same one.

```bash
sha256sum target-data/packets/$SEASON/$DAY/packet.md
uv run python -c "import json,sys; print(json.load(open(sys.argv[1]))['packet']['packet_md_sha256'])" rounds/$SEASON/$DAY/manifest.json
for b in runs/$SEASON/$DAY/*/bundle.json; do
  echo "$b $(uv run python -c "import json,sys; print(json.load(open(sys.argv[1]))['packet_md_sha256'])" "$b")"
done
```

## Check 2: each freeze precedes the day

This loops over the Tier S entries (fc-baseline.yml is tier H, so it is skipped).

```bash
for f in model-metadata/*.yml; do
  [ "$(uv run python -c "import yaml,sys; print(yaml.safe_load(open(sys.argv[1])).get('tier'))" "$f")" = S ] || continue
  uv run fc verify --entry "$f" --season-dir rounds/$SEASON/season --round-manifest rounds/$SEASON/$DAY/manifest.json
done
```

To check the weights too, set `f` to one entry file. This downloads only its frozen weights file (up to 11 GiB) and the JSON files, at the pinned revision.

```bash
uv run python -c "
import sys, yaml
from huggingface_hub import snapshot_download
r = yaml.safe_load(open(sys.argv[1]))['model_identity']
snapshot_download(r['repo_id'], revision=r['revision'], local_dir='snap',
                  allow_patterns=[r['inference']['weights_file'], '*.json'])" "$f"
uv run fc verify --entry "$f" --season-dir rounds/$SEASON/season --round-manifest rounds/$SEASON/$DAY/manifest.json --snapshot snap
```

## Check 3: the day's forecasts were committed

```bash
for s in sidecar/*/$SEASON-$DAY.json; do
  uv run fc verify --sidecar "$s" --roots rounds/$SEASON/$DAY/roots --manifest rounds/$SEASON/$DAY/manifest.json
done
```

Entries without a hub-held key have no daily sidecar. Their record is the runner-signed bundle.json from check 1.

TODO(author): [BLOCKER B10] Once the hub public key is published, add `--hub-key` with its path to both verify commands.

## What the output means today

Each line is PASS, FAIL or PENDING, a check name and a trust label. The check names are "sidecar well-formed", "merkle inclusion", "root published", "anchor attests to root", "anchor precedes T_open", "rekor entry", "receipt server_time precedes T_open", "commitment opens to identity record" and "snapshot matches pinned hashes". Check 3 says T_commit where check 2 says T_open, and only check 2 prints the last two. Only the checks labelled trustless decide the verdict.

The anchor step is available once anchoring ships. Until then "anchor attests to root" fails with status not-anchored, "anchor precedes" stays PENDING, the verdict is FAILED and the command exits non-zero. The freeze time rests on my signature on the receipt.

TODO(author): [BLOCKER B1] When the first real .ots proof verifies, rewrite this section with the passing output.
