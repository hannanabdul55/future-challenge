#!/usr/bin/env bash
# RUN-03 / ROUND-04: the GPU box's two-phase daily job.
#
# Phase 1 — Commit (00:05 UTC, MODE=commit, default):
#   SEASON=2026-year-end MODE=commit RUNNER_KEY=keys/runner/private.pem \
#   RUNNER_PUB=keys/runner/public.pem HUB_KEY=keys/hub/private.pem \
#   scripts/run_day.sh [YYYY-MM-DD]
#   Runs every admitted Tier S entry on today's packet, signs the daily output commitment
#   with RUNNER_KEY, submits it into the day's Merkle root before the 12:00 UTC commit close,
#   and pushes ONLY `rounds/`, `sidecar/`, and `target-data/packets/`. Plaintext forecasts
#   (`model-output/`) and transcripts (`runs/`) remain local until the 18:00 UTC reveal close.
#
# Phase 2 — Reveal (18:00 UTC, MODE=reveal):
#   SEASON=2026-year-end MODE=reveal scripts/run_day.sh [YYYY-MM-DD]
#   Closes the reveal window (`fc service close-reveals`) and publishes `model-output/`,
#   `runs/`, and `rounds/` together after Tier A reveals have closed.
set -uo pipefail
SEASON="${SEASON:-2026-year-end}"
MODE="${MODE:-commit}"
DAY="${1:-$(date -u +%F)}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNNER_KEY="${RUNNER_KEY:-keys/runner/private.pem}"
RUNNER_PUB="${RUNNER_PUB:-keys/runner/public.pem}"
HUB_KEY="${HUB_KEY:-keys/hub/private.pem}"
IMAGE="${IMAGE:-fc-forecaster:2026.10}"
LOG="$ROOT/runs/$SEASON/$DAY/run_day.log"
cd "$ROOT"
mkdir -p "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1
echo "== run_day $SEASON $DAY mode=$MODE $(date -u +%FT%TZ)"

git pull --ff-only || echo "WARN: git pull failed; continuing with local tree"

if [ "$MODE" = "reveal" ]; then
  uv run fc service close-reveals --round-dir "rounds/$SEASON/$DAY" || true
  git add model-output runs "rounds/$SEASON/$DAY" 2>/dev/null
  git commit -qm "reveal($SEASON): $DAY tier S outputs + reveal report" || true
  git push || echo "WARN: push failed"
  echo "== reveal done $(date -u +%FT%TZ)"
  exit 0
fi

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
    sign_key="$RUNNER_KEY"
    pub_arg=(--runner-pub "$RUNNER_PUB")
    if [ ! -f "$sign_key" ] && [ -f "keys/entries/$id.pem" ]; then
      sign_key="keys/entries/$id.pem"
      pub_arg=()
    fi
    if [ -f "$sign_key" ]; then
      uv run fc commit --round "$SEASON-$DAY" --model "$id" --submission "$out" \
        --rationale "runs/$SEASON/$DAY/$id/transcript.jsonl" --key "$sign_key" --out "$side" \
      && uv run fc service submit --sidecar "$side" --round-dir "rounds/$SEASON/$DAY" \
        --hub-key "$HUB_KEY" "${pub_arg[@]}" \
      || { echo "WARN: commit/submit failed for $id"; fail=1; }
    else
      echo "WARN: no runner key ($RUNNER_KEY) or entry key found to commit $id"; fail=1
    fi
  else
    echo "WARN: run failed for $id (bundle records the reason)"; fail=1
  fi
done

# Only push commitments, roots, and packets before 12:00 UTC — never plaintext model-output/ or runs/.
git add "rounds/$SEASON/$DAY" sidecar "target-data/packets/$SEASON/$DAY" 2>/dev/null
git commit -qm "commit($SEASON): $DAY tier S commitments rooted" || true
git push || echo "WARN: push failed"
echo "== commit done $(date -u +%FT%TZ) fail=$fail"
exit 0
