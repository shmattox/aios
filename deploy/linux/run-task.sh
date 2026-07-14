#!/usr/bin/env bash
# aios generic native-task runner for Linux (cron). The runner contract is OS-agnostic bash and
# lives ONCE at deploy/mac/run-task.sh (manifest resolution, allowedTools, conditional git line,
# machine-run marker, A21 post-run context-log check, dated result rotation) — this shim just
# execs it so the cron line and the launchd plist point at their own OS's deploy dir. The mac
# runner's only darwinism (BSD `date -v`) already carries the GNU `date -d` fallback.
#
# Usage: run-task.sh <TaskId> <EnvRoot> <PluginRoot> [--report-only]
MAC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../mac" && pwd)" || { echo "aios linux shim: cannot resolve deploy/mac next to $0" >&2; exit 1; }
exec bash "$MAC_DIR/run-task.sh" "$@"
