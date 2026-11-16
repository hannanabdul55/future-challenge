#!/usr/bin/env bash
# RUN-03 / ROUND-04 / OPS-02: the GPU box's fail-loud, two-phase daily job.
#
# Phase 1 — Commit (00:05 UTC timer, MODE=commit, default):
#   scripts/run_day.sh [YYYY-MM-DD]
#   Waits for the day's round (opened by .github/workflows/daily-round.yml at 00:00 UTC), or
#   opens it itself as a backstop and pushes it at once. Runs every Tier S entry on the day's
#   packet, signs the daily output commitment with RUNNER_KEY, submits it into the day's Merkle
#   root before the 12:00 UTC commit close, stamps the root with OpenTimestamps (best effort),
#   and pushes ONLY rounds/, sidecar/ and target-data/packets/. Plaintext forecasts
#   (model-output/) and transcripts (runs/) stay local until the 18:00 UTC reveal.
#
# Phase 2 — Reveal (18:00 UTC timer, MODE=reveal):
#   MODE=reveal scripts/run_day.sh [YYYY-MM-DD]
#   Closes the reveal window (fc service close-reveals) and publishes model-output/, runs/ and
#   rounds/ together.
#
# Fail loud (D-07): any failure (pull, packet build, round open, an entry's run, commit, submit,
# a low parse rate, push, close-reveals, a refused deadline, no GPU headroom) is recorded, makes
# the script exit 1 and posts one alert to ALERT_WEBHOOK (python -m fc.alert; a plain curl POST
# is the fallback). Days outside the season window exit 0 quietly, so pre-season timer firings
# do not alert. Nothing here swallows a failure.
#
# Job records: every in-season run writes runs/<season>/<day>/job-<mode>.json
#   {"schema": "fc-job/v1", "season", "day", "mode", "ok", "failures", "finished_at"}
# which fc.ops_evidence reads: a day on which the job alerted can never count toward OPS-03.
# run_day.log and the job records are published at reveal, so nothing sensitive may be printed:
# the log stream rewrites $HOME to ~, and neither the webhook URL nor the host name is ever
# echoed (the host name goes only into the webhook payload, added by fc.alert).
#
# Commit-phase deadline (watchdog parity, T-06-52). The watchdog's commit check runs at
# 02:15 UTC (.github/workflows/watchdog.yml). The longest commit phase this script permits ends
# by 02:00 UTC:
#     00:05 timer start
#   +    30 min manifest wait   (FC_MANIFEST_WAIT_MIN, capped at MAX_MANIFEST_WAIT_MIN)
#   +    10 min backstop        (BACKSTOP_ALLOW_MIN: packet build, round open, push)
#   +    60 min run budget      (TIMEOUT x Tier S entries <= RUN_BUDGET_S; fc run --timeout
#                                bounds each container)
#   +    15 min overhead        (OVERHEAD_ALLOW_MIN: GPU preflight, weights re-hash, commit,
#                                submit, OTS stamp, push with one retry)
#   = 02:00 UTC, which leaves 15 min before the watchdog looks. Raising a cap means moving the
# watchdog cron in the same change; tests/test_run_day.py reads both files and enforces it.
#
# Environment:
#   SEASON                 season id (default 2026-27-rolling)
#   MODE                   commit (default) or reveal
#   RUNNER_KEY, RUNNER_PUB runner signing key / public key (default keys/runner/{private,public}.pem)
#   HUB_KEY                hub signing key for fc service submit (default keys/hub/private.pem)
#   IMAGE                  forecaster container image
#   TIMEOUT                per-entry container timeout in seconds (default 2400)
#   ALERT_WEBHOOK          webhook URL for failure alerts (never printed or logged)
#   ALERT_FORMAT           text | discord | slack (default: detected from the URL host)
#   FC_MANIFEST_WAIT_MIN   minutes to wait for the day's manifest before the backstop (default 30,
#                          at most 30)
#   FC_MANIFEST_POLL_S     seconds between pulls while waiting (default 300)
#   FC_STOP_UNITS          space-separated systemd user units to stop for the run and start again
#                          at exit, also on failure (e.g. a GPU-hungry desktop service)
#   FC_STOP_SETTLE_S       seconds to let the GPU settle after stopping units (default 5)
#   FC_MIN_FREE_MIB        refuse to run below this much free VRAM (default 10000)
#   FC_MIN_PARSE_RATE      parse-rate floor; below it the job records a failure (default 0.80, must
#                          equal fc.ops_evidence.MIN_PARSE_RATE: the published threshold)
#   FC_REVEAL_WAIT_MAX_S   longest wait for T_reveal in reveal mode (default 1800)
#   FC_INJECT_FAILURE      1 = fail immediately (alert drills), before any git or network action
#   FC_NOW                 ISO time overriding the clock for the deadline checks (tests and
#                          rehearsals only; a WARN line is printed when set)
set -uo pipefail

RUN_BUDGET_S=3600
MAX_MANIFEST_WAIT_MIN=30
BACKSTOP_ALLOW_MIN=10
OVERHEAD_ALLOW_MIN=15

SEASON="${SEASON:-2026-27-rolling}"
MODE="${MODE:-commit}"
DAY="${1:-$(date -u +%F)}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNNER_KEY="${RUNNER_KEY:-keys/runner/private.pem}"
RUNNER_PUB="${RUNNER_PUB:-keys/runner/public.pem}"
HUB_KEY="${HUB_KEY:-keys/hub/private.pem}"
IMAGE="${IMAGE:-fc-forecaster:2026.10}"
TIMEOUT="${TIMEOUT:-2400}"
FC_MANIFEST_WAIT_MIN="${FC_MANIFEST_WAIT_MIN:-30}"
FC_MANIFEST_POLL_S="${FC_MANIFEST_POLL_S:-300}"
FC_STOP_UNITS="${FC_STOP_UNITS:-}"
FC_STOP_SETTLE_S="${FC_STOP_SETTLE_S:-5}"
FC_MIN_FREE_MIB="${FC_MIN_FREE_MIB:-10000}"
FC_MIN_PARSE_RATE="${FC_MIN_PARSE_RATE:-0.80}"
FC_REVEAL_WAIT_MAX_S="${FC_REVEAL_WAIT_MAX_S:-1800}"
FC_INJECT_FAILURE="${FC_INJECT_FAILURE:-0}"
FC_NOW="${FC_NOW:-}"

# --- argument sanity: DAY and SEASON become paths, so check them before touching the disk ----
case "$MODE" in
  commit | reveal) ;;
  *) echo "run_day: MODE must be commit or reveal" >&2; exit 2 ;;
esac
DAY_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
SEASON_RE='^[A-Za-z0-9][A-Za-z0-9._-]*$'
if ! [[ "$DAY" =~ $DAY_RE ]]; then echo "run_day: DAY must be YYYY-MM-DD" >&2; exit 2; fi
if ! [[ "$SEASON" =~ $SEASON_RE ]]; then echo "run_day: invalid SEASON" >&2; exit 2; fi

cd "$ROOT" || exit 2
MANIFEST="rounds/$SEASON/$DAY/manifest.json"
LOG=""
FAILURES=()
STOPPED_UNITS=()
UPGRADED_DIRS=()
FINISHING=0
RECORD_OK=""

# --- failure accounting ------------------------------------------------------------------------

fail() {
  FAILURES+=("$1")
  echo "FAIL: $1"
}

fatal() {
  fail "$1"
  finish
}

write_job_record() {
  mkdir -p "runs/$SEASON/$DAY" || return 1
  python3 -c '
import json, os, sys
from datetime import datetime, timezone
season, day, mode = sys.argv[1:4]
failures = sys.argv[4:]
path = "runs/%s/%s/job-%s.json" % (season, day, mode)
rec = {
    "schema": "fc-job/v1",
    "season": season,
    "day": day,
    "mode": mode,
    "ok": not failures,
    "failures": failures,
    "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(rec, fh, indent=2)
    fh.write("\n")
os.replace(tmp, path)
' "$SEASON" "$DAY" "$MODE" ${FAILURES[@]+"${FAILURES[@]}"} || return 1
  if [ "${#FAILURES[@]}" -eq 0 ]; then RECORD_OK=true; else RECORD_OK=false; fi
}

send_alert() {
  local msg="$1" url_esc
  sleep 1 # let the log pipeline flush so the alert's log tail includes the last lines
  if uv run python -m fc.alert --title "fc run_day FAILED" --message "$msg" --log-tail "$LOG"; then
    return 0
  fi
  if [ -z "${ALERT_WEBHOOK:-}" ]; then
    echo "ALERT NOT DELIVERED (ALERT_WEBHOOK is not set)"
    return 1
  fi
  # Fallback: plain curl POST. The URL travels through a config file descriptor and the message
  # through stdin, so neither shows up in the process list; stderr is discarded because curl
  # error text can echo the URL.
  url_esc=$(printf '%s' "$ALERT_WEBHOOK" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
  if printf '%s' "$msg" | curl -fsS -m 20 -o /dev/null -X POST -H 'Content-Type: text/plain' \
      --data-binary @- --config <(printf 'url = "%s"\n' "$url_esc") 2>/dev/null; then
    echo "alert delivered through the curl fallback"
    return 0
  fi
  echo "ALERT NOT DELIVERED (fc.alert and the curl fallback both failed)"
  return 1
}

finish() {
  if [ "$FINISHING" = 1 ]; then exit 1; fi
  FINISHING=1
  # A reveal run that already published a clean record keeps it: rewriting a tracked file after
  # the commit would leave the tree dirty. Any failure always rewrites it.
  if ! { [ "$MODE" = reveal ] && [ "$RECORD_OK" = true ] && [ "${#FAILURES[@]}" -eq 0 ]; }; then
    if ! write_job_record; then
      FAILURES+=("job record could not be written")
      echo "FAIL: job record could not be written"
    fi
  fi
  if [ "${#FAILURES[@]}" -eq 0 ]; then
    echo "== $MODE done $(date -u +%FT%TZ) ok"
    exit 0
  fi
  local joined="" f
  for f in "${FAILURES[@]}"; do joined="${joined:+$joined; }$f"; done
  echo "== $MODE FAILED $(date -u +%FT%TZ): $joined"
  send_alert "$SEASON $DAY mode=$MODE: $joined"
  exit 1
}

on_signal() {
  fail "terminated by signal $1"
  finish
}

restart_units() {
  local u
  for u in ${STOPPED_UNITS[@]+"${STOPPED_UNITS[@]}"}; do
    if ! systemctl --user start "$u"; then echo "WARN: could not start $u again"; fi
  done
}

trap restart_units EXIT
trap 'on_signal TERM' TERM
trap 'on_signal INT' INT

# --- helpers -----------------------------------------------------------------------------------

season_state() { # prints "in|out FIRST LAST", or "error"
  python3 - "rounds/$SEASON/season.json" "$DAY" 2>/dev/null <<'PY'
import json
import sys

try:
    doc = json.load(open(sys.argv[1]))
    first, last = str(doc["first_day"]), str(doc["last_day"])
except Exception:
    print("error")
    sys.exit(0)
print(("in" if first <= sys.argv[2] <= last else "out") + " " + first + " " + last)
PY
}

manifest_field() { # $1 = key; prints the manifest value, nothing on error
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' \
    "$MANIFEST" "$1" 2>/dev/null
}

secs_until() { # $1 = ISO timestamp; seconds from now (or FC_NOW) until it, negative if past
  python3 - "$1" "$FC_NOW" 2>/dev/null <<'PY'
import sys
from datetime import datetime, timezone


def parse(text):
    text = text.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    d = datetime.fromisoformat(text)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


target = parse(sys.argv[1])
now = parse(sys.argv[2]) if sys.argv[2] else datetime.now(timezone.utc)
print(int((target - now).total_seconds()))
PY
}

parse_rate_check() { # $1 = bundle path, $2 = floor; prints the rate; rc 0 ok, 1 below, 2 unreadable
  python3 - "$1" "$2" 2>/dev/null <<'PY'
import json
import math
import sys

try:
    rate = json.load(open(sys.argv[1]))["parse_rate"]
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or math.isnan(rate):
        raise ValueError(rate)
except Exception:
    print("unreadable")
    sys.exit(2)
print(rate)
sys.exit(0 if rate >= float(sys.argv[2]) else 1)
PY
}

is_uint() { [[ "$1" =~ ^[0-9]+$ ]]; }

stage() { # git add the paths that exist (git add errors on a missing pathspec)
  local p
  for p in "$@"; do
    if [ -e "$p" ]; then git add -- "$p" || return 1; fi
  done
}

push_with_retry() {
  if git push -q; then return 0; fi
  echo "WARN: push failed; retrying once after git pull --rebase"
  if ! git pull --rebase --autostash -q; then
    git rebase --abort >/dev/null 2>&1
    return 1
  fi
  git push -q
}

validate_config() {
  if ! is_uint "$FC_MANIFEST_WAIT_MIN" || [ "$FC_MANIFEST_WAIT_MIN" -gt "$MAX_MANIFEST_WAIT_MIN" ]; then
    fatal "FC_MANIFEST_WAIT_MIN '$FC_MANIFEST_WAIT_MIN' exceeds the $MAX_MANIFEST_WAIT_MIN min cap (watchdog deadline) or is not an integer"
  fi
  if ! is_uint "$FC_MANIFEST_POLL_S" || [ "$FC_MANIFEST_POLL_S" -lt 1 ]; then
    fatal "FC_MANIFEST_POLL_S must be a positive integer"
  fi
  if ! is_uint "$FC_MIN_FREE_MIB"; then fatal "FC_MIN_FREE_MIB must be an integer"; fi
  if ! is_uint "$FC_STOP_SETTLE_S"; then fatal "FC_STOP_SETTLE_S must be an integer"; fi
  if ! is_uint "$FC_REVEAL_WAIT_MAX_S"; then fatal "FC_REVEAL_WAIT_MAX_S must be an integer"; fi
  if ! python3 -c 'import sys; float(sys.argv[1])' "$FC_MIN_PARSE_RATE" 2>/dev/null; then
    fatal "FC_MIN_PARSE_RATE must be a number"
  fi
}

pull_or_fatal() {
  if ! git pull --ff-only -q; then fatal "git pull failed"; fi
}

# --- commit phase ------------------------------------------------------------------------------

wait_for_manifest_or_backstop() {
  local deadline now left nap
  deadline=$(( $(date +%s) + FC_MANIFEST_WAIT_MIN * 60 ))
  while [ ! -f "$MANIFEST" ]; do
    now=$(date +%s)
    left=$(( deadline - now ))
    if [ "$left" -le 0 ]; then break; fi
    nap="$FC_MANIFEST_POLL_S"
    if [ "$left" -lt "$nap" ]; then nap="$left"; fi
    echo "round $DAY not open yet; waiting ${nap}s (up to ${left}s more)"
    sleep "$nap"
    if ! git pull --ff-only -q; then echo "WARN: git pull failed while waiting; will retry"; fi
  done
  if [ -f "$MANIFEST" ]; then return 0; fi

  echo "round $DAY not open after ${FC_MANIFEST_WAIT_MIN} min; opening it here (box backstop)"
  if ! uv run fc packet build --date "$DAY" --out "target-data/packets/$SEASON/$DAY" \
      --season "$SEASON" --markets "questions/$SEASON/markets.json"; then
    fatal "packet build failed"
  fi
  if ! uv run fc round open --season "$SEASON" --date "$DAY" \
      --packet "target-data/packets/$SEASON/$DAY"; then
    fatal "round open failed"
  fi
  # Push at once so the workflow's own 00:20 backstop sees the manifest and skips (T-06-15).
  if ! stage "rounds/$SEASON/$DAY" "target-data/packets/$SEASON/$DAY"; then
    fatal "staging of the backstop round failed"
  fi
  if ! git commit -q -m "round($SEASON): open $DAY (box backstop)"; then
    fatal "commit of the backstop round failed"
  fi
  if ! push_with_retry; then fatal "push of backstop round failed"; fi
}

preflight_gpu() {
  local u free
  for u in $FC_STOP_UNITS; do
    STOPPED_UNITS+=("$u")
    if ! systemctl --user stop "$u"; then fail "could not stop $u"; fi
  done
  if [ -n "$FC_STOP_UNITS" ]; then sleep "$FC_STOP_SETTLE_S"; fi
  if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
    fatal "docker unavailable"
  fi
  if ! command -v nvidia-smi >/dev/null 2>&1; then fatal "nvidia-smi not found"; fi
  free=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -n 1 | tr -d '[:space:]')
  if ! is_uint "$free"; then fatal "cannot read free VRAM from nvidia-smi"; fi
  if [ "$free" -lt "$FC_MIN_FREE_MIB" ]; then
    fatal "insufficient free VRAM: $free MiB < $FC_MIN_FREE_MIB MiB"
  fi
  echo "GPU preflight ok: $free MiB free"
}

run_entry() { # $1 = metadata file
  local meta="$1" id out side sign_key bundle rate rc
  id="$(basename "$meta" .yml)"
  echo "-- $id"
  if ! uv run fc run --entry "$meta" --season "$SEASON" --date "$DAY" --fetch --image "$IMAGE" \
      --runner-key "$RUNNER_KEY" --timeout "$TIMEOUT"; then
    fail "run failed for $id (signed bundle records the reason)"
    return 0
  fi
  bundle="runs/$SEASON/$DAY/$id/bundle.json"
  rate=$(parse_rate_check "$bundle" "$FC_MIN_PARSE_RATE")
  rc=$?
  if [ "$rc" -eq 2 ]; then
    fail "parse rate unreadable for $id"
  elif [ "$rc" -ne 0 ]; then
    fail "parse rate $rate below $FC_MIN_PARSE_RATE for $id"
  fi
  # The commitment is made regardless: a low parse rate is the job's failure, not a reason to
  # drop a forecast that exists.
  out="model-output/$id/$SEASON-$DAY.csv"
  side="sidecar/$id/$SEASON-$DAY.json"
  sign_key="$RUNNER_KEY"
  local pub_arg=(--runner-pub "$RUNNER_PUB")
  if [ ! -f "$sign_key" ] && [ -f "keys/entries/$id.pem" ]; then
    sign_key="keys/entries/$id.pem"
    pub_arg=()
  fi
  if [ ! -f "$sign_key" ]; then
    fail "no signing key to commit $id"
    return 0
  fi
  if ! uv run fc commit --round "$SEASON-$DAY" --model "$id" --submission "$out" \
      --rationale "runs/$SEASON/$DAY/$id/transcript.jsonl" --key "$sign_key" --out "$side"; then
    fail "commit failed for $id"
    return 0
  fi
  if ! uv run fc service submit --sidecar "$side" --round-dir "rounds/$SEASON/$DAY" \
      --hub-key "$HUB_KEY" ${pub_arg[@]+"${pub_arg[@]}"}; then
    fail "submit failed for $id"
  fi
}

anchor_best_effort() { # OpenTimestamps: WARN lines only, never a failure
  local d upgrade_dirs=()
  if [ -d "rounds/$SEASON/$DAY/roots" ]; then
    if ! uv run python -m fc.anchor stamp --round-dir "rounds/$SEASON/$DAY"; then
      echo "WARN: OTS stamp did not complete for $DAY (the next run retries)"
    fi
  fi
  for d in "rounds/$SEASON"/*/; do
    d="${d%/}"
    if compgen -G "$d/roots/*.root.ots" >/dev/null; then upgrade_dirs+=("$d"); fi
  done
  UPGRADED_DIRS=("${upgrade_dirs[@]+"${upgrade_dirs[@]}"}")
  if [ "${#upgrade_dirs[@]}" -gt 0 ]; then
    if ! uv run python -m fc.anchor upgrade --round-dir "${upgrade_dirs[@]}"; then
      echo "WARN: OTS upgrade did not complete (the next run retries)"
    fi
  fi
}

commit_phase() {
  local t_commit secs meta id n entries=() paths=() d
  wait_for_manifest_or_backstop

  # Deadline guard: a commitment after T_commit is refused by the hub; do not burn the GPU on it.
  if [ -n "$FC_NOW" ]; then echo "WARN: FC_NOW=$FC_NOW overrides the clock (tests and rehearsals only)"; fi
  t_commit=$(manifest_field T_commit)
  secs=$(secs_until "$t_commit")
  if ! [[ "$secs" =~ ^-?[0-9]+$ ]]; then fatal "cannot read T_commit from $MANIFEST"; fi
  if [ "$secs" -le 0 ]; then fatal "past T_commit ($t_commit); commit phase refused"; fi

  for meta in model-metadata/*.yml; do
    [ -f "$meta" ] || continue
    if grep -Eq '^tier:[[:space:]]*"?S"?[[:space:]]*(#.*)?$' "$meta"; then entries+=("$meta"); fi
  done
  n="${#entries[@]}"
  if [ "$n" -eq 0 ]; then fatal "no Tier S entries in model-metadata/"; fi
  if ! [[ "$TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then fatal "TIMEOUT must be a positive integer"; fi
  if [ $(( TIMEOUT * n )) -gt "$RUN_BUDGET_S" ]; then
    fatal "TIMEOUT $TIMEOUT s x $n Tier S entries exceeds the run budget of $RUN_BUDGET_S s (watchdog deadline)"
  fi

  preflight_gpu
  for meta in "${entries[@]}"; do run_entry "$meta"; done
  anchor_best_effort

  # Only commitments, roots and packets go out before the reveal: never model-output/ or runs/.
  paths=("rounds/$SEASON/$DAY" "rounds/$SEASON/season/roots" sidecar "target-data/packets/$SEASON/$DAY")
  for d in ${UPGRADED_DIRS[@]+"${UPGRADED_DIRS[@]}"}; do paths+=("$d/roots"); done
  if ! stage "${paths[@]}"; then fail "staging failed"; fi
  if ! git diff --cached --quiet; then
    if ! git commit -q -m "commit($SEASON): $DAY tier S commitments rooted"; then
      fail "commit of the day's commitments failed"
    fi
  fi
  if ! push_with_retry; then fail "push failed"; fi
  finish
}

# --- reveal phase ------------------------------------------------------------------------------

reveal_phase() {
  local t_reveal secs
  if [ ! -f "$MANIFEST" ]; then fatal "round manifest missing; cannot reveal $DAY"; fi
  if [ -n "$FC_NOW" ]; then echo "WARN: FC_NOW=$FC_NOW overrides the clock (tests and rehearsals only)"; fi
  t_reveal=$(manifest_field T_reveal)
  secs=$(secs_until "$t_reveal")
  if ! [[ "$secs" =~ ^-?[0-9]+$ ]]; then fatal "cannot read T_reveal from $MANIFEST"; fi
  if [ "$secs" -gt 0 ]; then
    if [ "$secs" -gt "$FC_REVEAL_WAIT_MAX_S" ]; then
      fatal "too early for reveal: T_reveal $t_reveal is ${secs}s away (limit ${FC_REVEAL_WAIT_MAX_S}s)"
    fi
    echo "waiting ${secs}s for T_reveal ($t_reveal)"
    sleep "$secs"
  fi

  if ! uv run fc service close-reveals --round-dir "rounds/$SEASON/$DAY"; then
    fail "close-reveals failed"
  fi
  # Record the failures so far now: runs/ is staged below, so job-reveal.json is published.
  if ! write_job_record; then fail "job record could not be written"; fi
  if ! stage model-output runs "rounds/$SEASON/$DAY"; then fatal "staging failed"; fi
  if ! git diff --cached --quiet; then
    if ! git commit -q -m "reveal($SEASON): $DAY tier S outputs + reveal report"; then
      fatal "commit of the reveal failed"
    fi
  fi
  # On failure finish() rewrites job-reveal.json locally with ok false; a later reveal's staging
  # of runs/ publishes it.
  if ! push_with_retry; then fatal "push failed"; fi
  finish
}

# --- main --------------------------------------------------------------------------------------

STATE="$(season_state)"
if [ "$FC_INJECT_FAILURE" != "1" ]; then
  case "$STATE" in
    out\ *)
      read -r _ first last <<<"$STATE"
      echo "outside season $SEASON ($first..$last): nothing to do for $DAY"
      exit 0
      ;;
  esac
fi

LOG="$ROOT/runs/$SEASON/$DAY/run_day.log"
mkdir -p "$(dirname "$LOG")" || exit 2
HOME_RE="${HOME%/}"
if [ -n "$HOME_RE" ]; then
  HOME_RE=$(printf '%s' "$HOME_RE" | sed -e 's/[][\.*^$|]/\\&/g')
  exec > >(sed -u "s|$HOME_RE|~|g" | tee -a "$LOG") 2>&1
else
  exec > >(tee -a "$LOG") 2>&1
fi
echo "== run_day $SEASON $DAY mode=$MODE $(date -u +%FT%TZ)"

if [ "$FC_INJECT_FAILURE" = "1" ]; then fatal "INJECTED test failure (FC_INJECT_FAILURE=1)"; fi
case "$STATE" in
  in\ *) ;;
  *) fatal "season manifest rounds/$SEASON/season.json is missing or unreadable" ;;
esac
validate_config
pull_or_fatal

if [ "$MODE" = reveal ]; then reveal_phase; else commit_phase; fi
