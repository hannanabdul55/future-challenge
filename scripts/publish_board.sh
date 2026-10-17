#!/usr/bin/env bash
# BOARD-05 (self-hosted): rebuild the season board and publish it to the Oracle VM behind Caddy.
# Run hourly from cron on the GPU box, e.g.
#
#   15 * * * * cd /home/hannanabdul/future-challenge && PATH=$HOME/.local/bin:/usr/bin:/bin \
#     scripts/publish_board.sh >> $HOME/.local/state/fc/publish_board.log 2>&1
#
# (Log outside runs/, which run_day.sh commits.) DRY_RUN_DIR=DIR builds the site into DIR
# and skips the upload.
#
# The board code does not filter by reveal time, so this script stages a copy of the inputs that
# holds only the daily rounds whose T_reveal has passed. A day's forecasts are never published
# before its 18:00 UTC reveal, however often this runs.
set -euo pipefail
SEASON="${SEASON:-2026-us-midterms}"
HOST="${BOARD_HOST:-ubuntu@64.181.231.190}"
WEBROOT="${BOARD_WEBROOT:-/var/www/fc.yaarbal.app}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cd "$ROOT"
echo "== publish_board $SEASON $(date -u +%FT%TZ)"

# Revealed days only: a round directory is staged once its manifest's T_reveal <= now.
revealed="$(uv run python - "$SEASON" <<'EOF'
import json, sys
from datetime import datetime, timezone
from pathlib import Path
now = datetime.now(timezone.utc)
for p in sorted(Path("rounds", sys.argv[1]).glob("*/manifest.json")):
    m = json.loads(p.read_text())
    if m.get("kind") == "daily" and datetime.fromisoformat(m["T_reveal"]) <= now:
        print(m["day"])
EOF
)"
echo "revealed days: ${revealed:-none}"

mkdir -p "$STAGE/in/rounds/$SEASON" "$STAGE/site/model-output"
cp -r model-metadata questions "$STAGE/in/"
[ -d sidecar ] && cp -r sidecar "$STAGE/in/"
mkdir -p "$STAGE/in/target-data"
for d in packets truth; do [ -d "target-data/$d" ] && cp -r "target-data/$d" "$STAGE/in/target-data/"; done
cp rounds/"$SEASON"/season.json "$STAGE/in/rounds/$SEASON/"
cp -r rounds/"$SEASON"/season "$STAGE/in/rounds/$SEASON/"
mkdir -p "$STAGE/in/model-output"
for day in $revealed; do
  cp -r "rounds/$SEASON/$day" "$STAGE/in/rounds/$SEASON/"
  for f in model-output/*/"$SEASON-$day.csv"; do
    [ -f "$f" ] || continue
    mkdir -p "$STAGE/in/$(dirname "$f")" "$STAGE/site/$(dirname "$f")"
    cp "$f" "$STAGE/in/$f"; cp "$f" "$STAGE/site/$f"
  done
done

uv run fc season score --season "$SEASON" --root "$STAGE/in" || echo "no scores yet"
uv run fc season board --season "$SEASON" --root "$STAGE/in" --out "$STAGE/site/index.html"
cp -r "$STAGE/in/eval/out/$SEASON" "$STAGE/site/data"
cp -r model-metadata "$STAGE/site/"

if [ -n "${DRY_RUN_DIR:-}" ]; then
  rm -rf "$DRY_RUN_DIR"; cp -r "$STAGE/site" "$DRY_RUN_DIR"; echo "dry run: site in $DRY_RUN_DIR"; exit 0
fi

# Upload to a fresh directory, then swap it in, so visitors never see a half-written board.
tar -C "$STAGE/site" -czf - . | ssh -o BatchMode=yes "$HOST" "set -e
  sudo rm -rf '$WEBROOT.new'; sudo mkdir -p '$WEBROOT.new'
  sudo tar -C '$WEBROOT.new' -xzf - --no-same-owner -m
  sudo chown -R caddy:caddy '$WEBROOT.new'
  [ -d '$WEBROOT' ] && sudo mv '$WEBROOT' '$WEBROOT.old'
  sudo mv '$WEBROOT.new' '$WEBROOT'; sudo rm -rf '$WEBROOT.old'"
echo "== published $(date -u +%FT%TZ)"
