#!/usr/bin/env bash
# aios launchd registrar for macOS. Mirror of deploy/windows/register-tasks.ps1.
# Reads ../tasks.manifest.json and installs one LaunchAgent (com.aios.<id>) per ENABLED `native`
# task, translating the cron to a StartCalendarInterval. `manual` and `schedule-cloud` entries are
# skipped (run those in-session / via /schedule). Plists are generated with Python's plistlib (correct
# escaping), then loaded with `launchctl bootstrap gui/<uid>`. Idempotent: an existing agent of the
# same label is booted out first. RunAtLoad is false — registering never fires a stage.
#
# Usage: register-tasks.sh --env-root <path> --plugin-root <path> [--dry-run]
#   --dry-run  print the plists and the launchctl commands; write nothing, load nothing.
set -euo pipefail

ENV_ROOT=""; PLUGIN_ROOT=""; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env-root)    ENV_ROOT="${2:?}"; shift 2 ;;
    --plugin-root) PLUGIN_ROOT="${2:?}"; shift 2 ;;
    --dry-run)     DRY=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -n "$ENV_ROOT" ] && [ -n "$PLUGIN_ROOT" ] || { echo "usage: register-tasks.sh --env-root <path> --plugin-root <path> [--dry-run]" >&2; exit 2; }

PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || { echo "python3 not found (AIOS requires Python 3)" >&2; exit 1; }

MANIFEST="$PLUGIN_ROOT/deploy/tasks.manifest.json"
[ -f "$MANIFEST" ] || { echo "manifest not found at $MANIFEST" >&2; exit 1; }
RUNNER="$PLUGIN_ROOT/deploy/mac/run-task.sh"
LA_DIR="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"

# Python does the plist generation (plistlib) + file writing; it prints one TAB line per task
#   <label>\t<plist_path>\t<time_desc>\t<cron>
# to stdout for the launchctl loop below, and (dry-run) prints each plist preview to stderr.
GEN_LINES="$("$PY" - "$MANIFEST" "$ENV_ROOT" "$PLUGIN_ROOT" "$RUNNER" "$LA_DIR" "$DRY" <<'PY'
import json, os, plistlib, sys

manifest, env_root, plugin_root, runner, la_dir, dry = sys.argv[1:7]
dry = dry == "1"
DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

tasks = [t for t in json.load(open(manifest))["tasks"]
         if t.get("substrate") == "native" and t.get("enabled")]
if not dry:
    os.makedirs(la_dir, exist_ok=True)

for t in tasks:
    tid = t["id"]
    mn, hr, dom, mon, dow = t["cron"].split()
    sci = {}
    if mn  != "*": sci["Minute"]  = int(mn)
    if hr  != "*": sci["Hour"]    = int(hr)
    if dom != "*": sci["Day"]     = int(dom)
    if mon != "*": sci["Month"]   = int(mon)
    if dow != "*": sci["Weekday"] = int(dow)

    time_desc = "%02d:%02d" % (int(hr), int(mn))
    if dow != "*":
        time_desc = "%s %s" % (DOW[int(dow) % 7], time_desc)

    label = "com.aios.%s" % tid
    log_dir = os.path.join(env_root, "state", "task-logs", tid)
    if not dry:
        # launchd does not create parents for StandardOut/ErrPath — make the dir now so the very
        # first scheduled fire can capture launchd-level failures (before run-task.sh creates it).
        os.makedirs(log_dir, exist_ok=True)
    plist = {
        "Label": label,
        "ProgramArguments": ["/bin/bash", runner, tid, env_root, plugin_root],
        "StartCalendarInterval": sci,
        "RunAtLoad": False,
        "StandardOutPath":   os.path.join(log_dir, "launchd.out.log"),
        "StandardErrorPath": os.path.join(log_dir, "launchd.err.log"),
    }
    xml = plistlib.dumps(plist)
    path = os.path.join(la_dir, label + ".plist")
    if dry:
        sys.stderr.write("\n--- WOULD register '%s' at %s (cron: %s) -> %s\n" % (label, time_desc, t["cron"], path))
        sys.stderr.write(xml.decode())
    else:
        with open(path, "wb") as f:
            f.write(xml)
    print("\t".join([label, path, time_desc, t["cron"]]))
PY
)"

[ -n "$GEN_LINES" ] || { echo "no enabled 'native' tasks in manifest — nothing to register." >&2; exit 0; }

FAILED=()
while IFS=$'\t' read -r LABEL PLIST TIME_DESC CRON; do
  [ -n "$LABEL" ] || continue
  if [ "$DRY" = "1" ]; then
    echo "WOULD register '$LABEL' at $TIME_DESC ($CRON)"
    continue
  fi
  # bootout is asynchronous; an immediate bootstrap can hit "already loaded" before the old instance
  # finishes unloading. Retry a couple times with a short settle rather than letting one failure abort
  # the batch (which would leave a silent partial install). Bootstrap in an `if` so set -e won't fire.
  launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
  ok=0
  for _attempt in 1 2 3; do
    if launchctl bootstrap "gui/$UID_NUM" "$PLIST" 2>/dev/null; then ok=1; break; fi
    sleep 1
    launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
  done
  if [ "$ok" = "1" ]; then
    echo "registered '$LABEL' at $TIME_DESC ($CRON)"
  else
    echo "FAILED to register '$LABEL' — inspect with: launchctl print gui/$UID_NUM/$LABEL" >&2
    FAILED+=("$LABEL")
  fi
done <<< "$GEN_LINES"

if [ "${#FAILED[@]}" -gt 0 ]; then
  echo "${#FAILED[@]} agent(s) failed to register: ${FAILED[*]}" >&2
  exit 1
fi
