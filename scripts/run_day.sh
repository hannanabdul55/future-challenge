#!/usr/bin/env bash
# RUN-03 / ROUND-04: the GPU box's daily job. Run from a systemd timer (or cron) at 00:05 UTC.
#
#   SEASON=2026-us-midterms RUNNER_KEY=keys/runner/private.pem HUB_KEY=keys/hub/private.pem \
#   scripts/run_day.sh [YYYY-MM-DD]
#
# For every Tier S entry in model-metadata/: run the frozen model on today's packet, then
# commit the forecast through the service so it sits in the day's Merkle root before the
# 12:00 UTC close, then push outputs + run bundles. A failed entry is recorded as missing
# (the runner always writes a signed bundle) and never stops the others.
set -uo pipefail
SEASON="${SEASON:-2026-us-midterms}"
DAY="${1:-$(date -u +%F)}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNNER_KEY="${RUNNER_KEY:-keys/runner/private.pem}"
HUB_KEY="${HUB_KEY:-keys/hub/private.pem}"
IMAGE="${IMAGE:-fc-forecaster:2026.10}"
LOG="$ROOT/runs/$SEASON/$DAY/run_day.log"
cd "$ROOT"
mkdir -p "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1
echo "== run_day $SEASON $DAY $(date -u +%FT%TZ)"

git pull --ff-only || echo "WARN: git pull failed; continuing with local tree"
if [ ! -f "rounds/$SEASON/$DAY/manifest.json" ]; then
  echo "round $DAY not open yet; building locally (backstop)"
  uv run fc packet build --date "$DAY" --out "target-data/packets/$SEASON/$DAY" --season "$SEASON" \
    --markets "questions/$SEASON/markets.json" || true
  uv run fc round open --season "$SEASON" --date "$DAY" --packet "target-data/packets/$SEASON/$DAY" || exit 1
fi

fail=0
for meta in model-metadata/*.yml; do
  id="$(basename "$meta" .yml)"
  tier="$(python3 -c "import yaml,sys;print((yaml.safe_load(open(sys.argv[1])) or {}).get('tier',''))" "$meta")"
  [ "$tier" = "S" ] || continue
  echo "-- $id"
  if uv run fc run --entry "$meta" --season "$SEASON" --date "$DAY" --fetch --image "$IMAGE" \
       --runner-key "$RUNNER_KEY" --timeout "${TIMEOUT:-2400}"; then
    out="model-output/$id/$SEASON-$DAY.csv"
    side="sidecar/$id/$SEASON-$DAY.json"
    # Hub-run entries are committed by the hub with the entry's registered key when one is
    # held in keys/entries/<id>.pem; otherwise the runner signature on the bundle is the record.
    if [ -f "keys/entries/$id.pem" ]; then
      uv run fc commit --round "$SEASON-$DAY" --model "$id" --submission "$out" \
        --rationale "runs/$SEASON/$DAY/$id/transcript.jsonl" --key "keys/entries/$id.pem" --out "$side" \
      && uv run fc service submit --sidecar "$side" --round-dir "rounds/$SEASON/$DAY" --hub-key "$HUB_KEY" \
      || { echo "WARN: commit/submit failed for $id"; fail=1; }
    fi
  else
    echo "WARN: run failed for $id (bundle records the reason)"; fail=1
  fi
done

git add model-output runs rounds sidecar target-data/packets 2>/dev/null
git commit -qm "run($SEASON): $DAY tier S outputs" || true
git push || echo "WARN: push failed"
echo "== done $(date -u +%FT%TZ) fail=$fail"
exit 0
