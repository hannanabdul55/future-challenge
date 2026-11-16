#!/usr/bin/env bash
# Render the fc-* systemd user units with this checkout's path (D-06).
#
#   scripts/systemd/install.sh                 verified, root-free install + enable of both timers
#   scripts/systemd/install.sh --dry-run DIR   render every unit into DIR and stop (no systemd)
#
# Root is never needed: these are systemd --user units. The live path refuses unless user
# lingering is on (timers must fire with no login session) and ~/.config/fc/env (the only
# place secrets live) exists with mode 600. Units are verified before anything is enabled; a
# failed verify restores whatever was installed before.
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


die() { echo "install.sh: refusing: $*" >&2; exit 1; }

USER_NAME="${USER:-$(id -un)}"
ENV_FILE="$HOME/.config/fc/env"
UNIT_DIR="$HOME/.config/systemd/user"

# systemd treats whitespace, %, quotes and backslashes in an unquoted ExecStart path specially.
case "$FC_ROOT" in
  *[[:space:]]* | *%* | *\"* | *\'* | *\\*)
    die "checkout path '$FC_ROOT' contains whitespace, %, a quote or a backslash; systemd units cannot hold it" ;;
esac

linger="$(loginctl show-user "$USER_NAME" -p Linger 2>/dev/null || true)"
[ "$linger" = "Linger=yes" ] ||
  die "user linger is not enabled (got '${linger:-nothing}'); run: loginctl enable-linger $USER_NAME"

[ -f "$ENV_FILE" ] || die "$ENV_FILE is missing; create it with the job's secrets and chmod 600"
mode="$(stat -c %a "$ENV_FILE" 2>/dev/null || stat -f %Lp "$ENV_FILE")"
[ "$mode" = "600" ] || die "$ENV_FILE has mode $mode; it must be mode 600 (chmod 600 $ENV_FILE)"

BACKUP="$(mktemp -d)"
trap 'rm -rf "$BACKUP"' EXIT
mkdir -p "$UNIT_DIR"
names=()
for f in "$SRC"/*.service "$SRC"/*.timer; do
  [ -f "$f" ] || continue
  n="$(basename "$f")"
  names+=("$n")
  if [ -f "$UNIT_DIR/$n" ]; then cp -p "$UNIT_DIR/$n" "$BACKUP/$n"; fi
done

render "$UNIT_DIR"
installed=()
for n in "${names[@]}"; do installed+=("$UNIT_DIR/$n"); done

if ! systemd-analyze --user verify "${installed[@]}"; then
  for n in "${names[@]}"; do
    if [ -f "$BACKUP/$n" ]; then cp -p "$BACKUP/$n" "$UNIT_DIR/$n"; else rm -f "$UNIT_DIR/$n"; fi
  done
  die "systemd-analyze verify failed; previous units restored, nothing enabled"
fi

systemctl --user daemon-reload
systemctl --user enable --now fc-commit.timer fc-reveal.timer
systemctl --user list-timers 'fc-*' --all
