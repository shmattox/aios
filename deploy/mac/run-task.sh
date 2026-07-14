#!/usr/bin/env bash
# aios generic native-task runner for macOS (launchd) AND Linux (cron — deploy/linux/run-task.sh
# execs this file; keep it OS-agnostic bash). Mirror of deploy/windows/run-task.ps1.
# Resolves the task body + allowedTools from the manifest and runs headless `claude -p`, logging to
# <env_root>/state/task-logs/<task-id>/ (last-run.log = stamped exit trailer, last-result.txt = output).
# Git is banned UNLESS the task's manifest carries read-only git grants (A20 — parity with the ps1's
# conditional git line). Written to bash 3.2 (stock macOS) — no mapfile.
#
# Usage: run-task.sh <TaskId> <EnvRoot> <PluginRoot> [--report-only]
set -euo pipefail

TASK_ID="${1:?usage: run-task.sh <TaskId> <EnvRoot> <PluginRoot> [--report-only]}"
ENV_ROOT="${2:?env root required}"
PLUGIN_ROOT="${3:?plugin root required}"
REPORT_ONLY="${4:-}"

# launchd hands us a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin) — not the login shell's. Prepend the
# common install dirs so claude/python3 resolve under launchd (Apple Silicon, Intel, and ~/.local/bin).
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export PYTHONUTF8=1

PY="$(command -v python3 || command -v python || true)"

MANIFEST="$PLUGIN_ROOT/deploy/tasks.manifest.json"
LOG_DIR="$ENV_ROOT/state/task-logs/$TASK_ID"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/last-run.log"
OUT="$LOG_DIR/last-result.txt"
STAMP="$(date +%Y-%m-%dT%H:%M:%S)"

log_err() { echo "$STAMP  ERROR: $1" >> "$LOG"; echo "ERROR: $1" >&2; }

[ -n "$PY" ] || { log_err "python3 not found (AIOS requires Python 3)"; exit 1; }

# A21: run window floor for the post-run context-log check (120s slack for clock rounding).
SINCE_UTC="$(date -u -v-120S +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '-120 seconds' +%Y-%m-%dT%H:%M:%SZ)"

# A21: shared post-run tail — write the result, rotate a dated forensic copy, and run the
# deterministic context-log check (the model self-reporting "line appended" is not evidence).
# WARN-only: the task's own exit code stands; the WARN goes loud into last-run.log + last-result.
complete_run() {  # $1 = result text
  printf '%s\n' "$1" > "$OUT"
  cp "$OUT" "$LOG_DIR/last-result-$(date +%Y%m%d-%H%M%S).txt" 2>/dev/null || true
  { ls -1 "$LOG_DIR"/last-result-*.txt 2>/dev/null | sort -r | tail -n +11 | while IFS= read -r _old; do
    rm -f "$_old"
  done; } || true
  CTX_STAGES="$("$PY" -c 'import json,sys
t=[x for x in json.load(open(sys.argv[1]))["tasks"] if x["id"]==sys.argv[2]]
print(",".join(t[0].get("context_stages",[])) if t else "")' "$MANIFEST" "$TASK_ID" 2>/dev/null || true)"
  if [ -n "$CTX_STAGES" ] && [ "$REPORT_ONLY" != "--report-only" ]; then
    set +e
    CHK="$("$PY" "$PLUGIN_ROOT/engine/tools/context_log.py" check \
          --path "$ENV_ROOT/state/context-log.jsonl" --stage "$CTX_STAGES" --since "$SINCE_UTC" 2>&1)"
    CHK_CODE=$?
    set -e
    if [ "$CHK_CODE" -ne 0 ]; then
      echo "$STAMP  ctx-check $(printf '%s' "$CHK" | tr '\n' ' ')" >> "$LOG"
      printf 'CTX-CHECK: %s\n' "$CHK" >> "$OUT"
    else
      echo "$STAMP  ctx-check OK" >> "$LOG"
    fi
  fi
}

# A25: script-type tasks run a deterministic engine shell directly — no model in the loop.
TASK_TYPE="$("$PY" -c 'import json,sys
t=[x for x in json.load(open(sys.argv[1]))["tasks"] if x["id"]==sys.argv[2]]
print(t[0].get("type","") if t else "")' "$MANIFEST" "$TASK_ID")"
if [ "$TASK_TYPE" = "script" ]; then
  SCRIPT_REL="$("$PY" -c 'import json,sys
t=[x for x in json.load(open(sys.argv[1]))["tasks"] if x["id"]==sys.argv[2]]
print(t[0].get("script","") if t else "")' "$MANIFEST" "$TASK_ID")"
  SCRIPT="$PLUGIN_ROOT/$SCRIPT_REL"
  [ -f "$SCRIPT" ] || { log_err "script not found at $SCRIPT"; exit 1; }
  cd "$ENV_ROOT"
  set +e
  RESULT="$("$PY" "$SCRIPT" --env-root "$ENV_ROOT")"
  CODE=$?
  set -e
  complete_run "$RESULT"
  LAST="$(printf '%s\n' "$RESULT" | awk 'NF{l=$0} END{print l}')"
  echo "$STAMP  exit=$CODE  $LAST" >> "$LOG"
  exit "$CODE"
fi

# claude binary: ~/.local/bin/claude then PATH (mirror of Windows' ~/.local/bin/claude.exe then PATH).
CLAUDE="$HOME/.local/bin/claude"
[ -x "$CLAUDE" ] || CLAUDE="$(command -v claude || true)"
[ -n "$CLAUDE" ] || { log_err "claude not found on PATH"; exit 1; }

# Resolve the task body path from the manifest.
BODY_REL="$("$PY" -c 'import json,sys
t=[x for x in json.load(open(sys.argv[1]))["tasks"] if x["id"]==sys.argv[2]]
print(t[0]["body_path"] if t else "")' "$MANIFEST" "$TASK_ID")"
[ -n "$BODY_REL" ] || { log_err "unknown task id $TASK_ID"; exit 1; }
SKILL="$PLUGIN_ROOT/$BODY_REL"
[ -f "$SKILL" ] || { log_err "body not found at $SKILL"; exit 1; }

# Resolve allowedTools (dropping report_only_drops when --report-only), one per line into an array.
TOOLS=()
while IFS= read -r _tool; do
  [ -n "$_tool" ] && TOOLS+=("$_tool")
done < <("$PY" - "$MANIFEST" "$TASK_ID" "$REPORT_ONLY" <<'PY'
import json, sys
manifest, tid, mode = sys.argv[1], sys.argv[2], sys.argv[3]
t = next(x for x in json.load(open(manifest))["tasks"] if x["id"] == tid)
tools = list(t.get("allowed_tools", []))
if mode == "--report-only":
    drops = set(t.get("report_only_drops", []))
    tools = [x for x in tools if x not in drops]
for x in tools:
    print(x)
PY
)

RO_NOTE=""
[ "$REPORT_ONLY" = "--report-only" ] && RO_NOTE=" REPORT-ONLY MODE: propose, never write."

# A20: conditional git line (parity with run-task.ps1) — tasks whose manifest carries read-only git
# grants get the exact invocation form the prefix-matcher accepts; tasks without keep the ban.
GIT_LINE="Do NOT run git."
GIT_GRANTS=""
for _t in ${TOOLS[@]+"${TOOLS[@]}"}; do
  case "$_t" in "Bash(git "*) GIT_GRANTS="${GIT_GRANTS:+$GIT_GRANTS, }$_t" ;; esac
done
[ -n "$GIT_GRANTS" ] && GIT_LINE="Read-only git is allowed for this task ($GIT_GRANTS): invoke it ONLY as 'cd <repo> && git log ...' / 'cd <repo> && git show ...' — each segment must match a grant; 'git -C <path> ...' matches NO grant and will be denied. NEVER any git write (add/commit/push/checkout/reset/rebase)."

PROMPT="You are the aios '$TASK_ID' stage running UNATTENDED as a scheduled native task (headless claude -p).
Env root: $ENV_ROOT   Plugin root: $PLUGIN_ROOT
Read your full instructions from: $SKILL
Execute them completely NOW. $GIT_LINE Do NOT ask questions or wait for input; follow the
instructions exactly and finish.$RO_NOTE"

cd "$ENV_ROOT"
ARGS=(-p "$PROMPT" --output-format text)
[ "${#TOOLS[@]}" -gt 0 ] && ARGS+=(--allowedTools "${TOOLS[@]}")
# Per-task model tier + turn cap (parity with run-task.ps1; A25).
TASK_MODEL="$("$PY" -c 'import json,sys
t=[x for x in json.load(open(sys.argv[1]))["tasks"] if x["id"]==sys.argv[2]]
print(t[0].get("model","") if t else "")' "$MANIFEST" "$TASK_ID")"
[ -n "$TASK_MODEL" ] && ARGS+=(--model "$TASK_MODEL")
TASK_MAXTURNS="$("$PY" -c 'import json,sys
t=[x for x in json.load(open(sys.argv[1]))["tasks"] if x["id"]==sys.argv[2]]
print(t[0].get("max_turns","") if t else "")' "$MANIFEST" "$TASK_ID")"
[ -n "$TASK_MAXTURNS" ] && ARGS+=(--max-turns "$TASK_MAXTURNS")

set +e
# A16: mark this as a machine (fleet) run — the session-evidence hook stamps `machine_run: true`
# so session-capture never synthesizes the pipeline's own headless sessions into records. Scoped
# to the claude invocation only (parity with the ps1's try/finally): a sourced manual run must
# never leak the var into an interactive shell, or later human sessions get silently pruned.
RESULT="$(AIOS_MACHINE_RUN="$TASK_ID" "$CLAUDE" "${ARGS[@]}")"
CODE=$?
set -e

complete_run "$RESULT"
LAST="$(printf '%s\n' "$RESULT" | awk 'NF{l=$0} END{print l}')"
echo "$STAMP  exit=$CODE  $LAST" >> "$LOG"
exit "$CODE"
