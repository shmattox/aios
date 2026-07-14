#!/usr/bin/env bash
# aios cron de-registrar for Linux. Mirror of deploy/mac/unregister-tasks.sh.
# Removes the managed aios block from the user's crontab (marker-fenced, so it cleans up even
# tasks since renamed/removed from the manifest). --dry-run previews only.
#
# Usage: unregister-tasks.sh [--dry-run]
set -euo pipefail

# Markers match on a stable PREFIX (same rule as the registrar) - a user-touched marker line
# must not hide the block from removal.
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

command -v crontab >/dev/null || { echo "crontab not found - nothing to remove."; exit 0; }
EXISTING="$(crontab -l 2>/dev/null || true)"
BLOCK="$(printf '%s\n' "$EXISTING" | awk '
  index($0, "# BEGIN aios tasks") == 1 {inblk=1}
  inblk {print}
  index($0, "# END aios tasks") == 1 {inblk=0}')"

if [ -z "$BLOCK" ]; then
  echo "no managed aios block in the crontab - nothing to remove."
  exit 0
fi

if [ "$DRY" = "1" ]; then
  echo "WOULD remove this managed block:"
  printf '%s\n' "$BLOCK"
  exit 0
fi

CLEANED="$(printf '%s\n' "$EXISTING" | awk '
  index($0, "# BEGIN aios tasks") == 1 {inblk=1; next}
  index($0, "# END aios tasks")   == 1 {inblk=0; next}
  !inblk {print}')"
if [ -n "$CLEANED" ]; then
  printf '%s\n' "$CLEANED" | crontab -
else
  crontab -r 2>/dev/null || true
fi
echo "removed the managed aios block ($(printf '%s\n' "$BLOCK" | grep -c 'run-task.sh' || true) task line(s))."
