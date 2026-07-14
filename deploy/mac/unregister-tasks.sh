#!/usr/bin/env bash
# aios launchd de-registrar for macOS. Mirror of deploy/windows/unregister-tasks.ps1.
# Boots out and removes every installed com.aios.* LaunchAgent (globbed from ~/Library/LaunchAgents,
# so it cleans up even tasks since renamed/removed from the manifest). --dry-run previews only.
#
# Usage: unregister-tasks.sh [--dry-run]
set -euo pipefail

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

LA_DIR="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"
shopt -s nullglob
PLISTS=("$LA_DIR"/com.aios.*.plist)

if [ "${#PLISTS[@]}" -eq 0 ]; then
  echo "no com.aios.* LaunchAgents installed — nothing to remove."
  exit 0
fi

for PLIST in "${PLISTS[@]}"; do
  LABEL="$(basename "$PLIST" .plist)"
  if [ "$DRY" = "1" ]; then
    echo "WOULD remove '$LABEL' ($PLIST)"
    continue
  fi
  launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
  rm -f "$PLIST"
  echo "removed '$LABEL'"
done
