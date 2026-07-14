#!/usr/bin/env bash
# aios cron registrar for Linux. Mirror of deploy/mac/register-tasks.sh (launchd) and
# deploy/windows/register-tasks.ps1 (Task Scheduler).
# Reads ../tasks.manifest.json and installs one crontab line per ENABLED `native` task into a
# MANAGED BLOCK in the user's crontab (idempotent: the block is replaced wholesale on re-register,
# never appended twice). `manual` and `schedule-cloud` entries are skipped. Registering never
# fires a stage — cron only fires at the next matching minute.
#
# The manifest's cron fields are plain integers (min hour dom mon dow) — emitted verbatim; no
# range/list/step parsing, same contract as the other registrars.
#
# Usage: register-tasks.sh --env-root <path> --plugin-root <path> [--dry-run]
#   --dry-run  print the managed block; write nothing, install nothing.
set -euo pipefail

BEGIN_MARK="# BEGIN aios tasks (managed by deploy/linux/register-tasks.sh - do not edit inside)"
END_MARK="# END aios tasks"

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
RUNNER="$PLUGIN_ROOT/deploy/linux/run-task.sh"

# Build the managed block. Each line routes through the runner (which owns logging, allowedTools,
# the machine-run marker, and the A21 post-run check); the trailing redirect catches pre-runner
# failures (bash missing, runner unreadable) that would otherwise vanish into cron's mail void.
BLOCK="$("$PY" - "$MANIFEST" "$ENV_ROOT" "$PLUGIN_ROOT" "$RUNNER" <<'PY'
import json, os, shlex, sys

manifest, env_root, plugin_root, runner = sys.argv[1:5]
tasks = [t for t in json.load(open(manifest))["tasks"]
         if t.get("substrate") == "native" and t.get("enabled")]
for t in tasks:
    tid = t["id"]
    cron = t["cron"].strip()
    if len(cron.split()) != 5:
        sys.stderr.write("skipping %s: malformed cron %r\n" % (tid, cron))
        continue
    log = os.path.join(env_root, "state", "task-logs", tid, "cron.log")
    line = "%s /usr/bin/env bash %s %s %s %s >> %s 2>&1" % (
        cron, shlex.quote(runner), shlex.quote(tid), shlex.quote(env_root),
        shlex.quote(plugin_root), shlex.quote(log))
    # cron treats an unescaped % as end-of-command + stdin REGARDLESS of shell quoting —
    # a % anywhere in a path would truncate the line and eat the log redirect. Escape it.
    print(line.replace("%", r"\%"))
PY
)"

[ -n "$BLOCK" ] || { echo "no enabled 'native' tasks in manifest - nothing to register." >&2; exit 0; }

if [ "$DRY" = "1" ]; then
  echo "WOULD install this managed crontab block:"
  echo "$BEGIN_MARK"
  echo "$BLOCK"
  echo "$END_MARK"
  exit 0
fi

command -v crontab >/dev/null || { echo "crontab not found - install cron (e.g. cronie/cron) first" >&2; exit 1; }

# Pre-create log dirs so the very first fire can capture pre-runner failures. Derived from the
# MANIFEST, never parsed out of the rendered (quoted, %-escaped) cron lines — /bin/sh opens the
# >> redirect before exec'ing the runner, so a missing dir means the task silently never fires.
LOGDIRS="$("$PY" - "$MANIFEST" "$ENV_ROOT" <<'PY'
import json, os, sys
manifest, env_root = sys.argv[1:3]
for t in json.load(open(manifest))["tasks"]:
    if t.get("substrate") == "native" and t.get("enabled"):
        print(os.path.join(env_root, "state", "task-logs", t["id"]))
PY
)"
printf '%s\n' "$LOGDIRS" | while IFS= read -r LOGDIR; do
  if [ -n "$LOGDIR" ]; then mkdir -p "$LOGDIR"; fi
done

# Replace the managed block wholesale (crontab -l exits non-zero on an empty crontab - tolerate).
# Markers match on a stable PREFIX, not full-line equality - a user-touched marker (trailing
# whitespace, edited comment) must not defeat the strip, or re-register appends a SECOND block
# and every task double-fires.
EXISTING="$(crontab -l 2>/dev/null || true)"
CLEANED="$(printf '%s\n' "$EXISTING" | awk '
  index($0, "# BEGIN aios tasks") == 1 {inblk=1; next}
  index($0, "# END aios tasks")   == 1 {inblk=0; next}
  !inblk {print}')"
{ [ -n "$CLEANED" ] && printf '%s\n' "$CLEANED"; echo "$BEGIN_MARK"; echo "$BLOCK"; echo "$END_MARK"; } | crontab -

echo "installed managed aios block:"
crontab -l | awk '
  index($0, "# BEGIN aios tasks") == 1 {inblk=1}
  inblk {print}
  index($0, "# END aios tasks") == 1 {inblk=0}'
