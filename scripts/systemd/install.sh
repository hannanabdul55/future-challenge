#!/usr/bin/env bash
# Render the fc-* systemd user units with this checkout's path (D-06).
#
#   scripts/systemd/install.sh --dry-run DIR   render every unit into DIR and stop (no systemd)
#
# Root is never needed: these are systemd --user units.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FC_ROOT="${FC_ROOT:-$(cd "$SRC/../.." && pwd)}"

render() {
  # render DEST_DIR: substitute @FC_ROOT@ in every unit file. '|' delimiter: paths contain '/'.
  local dest="$1" f esc
  esc="$(printf '%s' "$FC_ROOT" | sed -e 's/[\\&|]/\\&/g')"  # escape sed replacement metachars
  mkdir -p "$dest"
  for f in "$SRC"/*.service "$SRC"/*.timer; do
    [ -f "$f" ] || continue
    sed "s|@FC_ROOT@|$esc|g" "$f" > "$dest/$(basename "$f")"
  done
}

usage() { echo "usage: $0 --dry-run DIR | $0" >&2; }

if [ "${1:-}" = "--dry-run" ]; then
  [ -n "${2:-}" ] || { usage; exit 2; }
  render "$2"
  echo "rendered units into $2"
  exit 0
fi

echo "live install is added in the next step" >&2
exit 2
