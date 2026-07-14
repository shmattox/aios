# AIOS Windows scheduling

`register-tasks.ps1 -EnvRoot <path> -PluginRoot <path> [-DryRun]` reads `../tasks.manifest.json`
and registers one Windows Task Scheduler task (`AIOS <id>`) per **enabled `native`** entry (the OS-agnostic scheduled class; the Mac registrar in `../mac/` reads the same tag),
converting each cron to a Daily or Weekly trigger. `manual` and `schedule-cloud` entries are skipped
(run those in-session / via `/schedule`).

Every task fires `run-hidden.vbs` (no console flash) -> `run-task.ps1 -TaskId <id> -EnvRoot <path>
-PluginRoot <path>` -- the single generic runner. It resolves the task's body from the manifest,
launches headless `claude -p` with the manifest's `allowedTools`, and logs to
`<env_root>/state/task-logs/<task-id>/` (`last-run.log` = stamped exit-code trailer,
`last-result.txt` = full output).

Unregister everything with `unregister-tasks.ps1` (preview with `-WhatIfMode`).

**Optional tasks (opt-in).** Manifest entries with `enabled: false` (e.g. `aios-brief-cache`, the brief
precompute cache-writer) are skipped by `register-tasks.ps1` on purpose — they're opt-in per install.
Register one on demand, without editing the manifest (so the shipped default stays opt-in), with
`register-optional-task.ps1 -TaskId <id> -EnvRoot <path> -PluginRoot <path> [-DryRun]`. It reads the same
manifest, ignores only the `enabled` filter, and builds an identical action/trigger — so an opted-in task
is indistinguishable from the always-on set. Reversible: `Unregister-ScheduledTask -TaskName 'AIOS <id>'`.
The choice is per machine (the schedule is machine-local) — run it on each machine that should have it.

Notes: the runner sets `PYTHONUTF8=1` (engine tools emit UTF-8 on Windows). `-ReportOnly` drops the
tools listed in a task's `report_only_drops` (a permission-layer drop, not prompt-only) so a task can
propose without writing.

`Bash(python:*)` is the exec surface for the unattended tasks — the deterministic engine tools do the
file writes, so the agent only runs them and reports. Each task's `allowed_tools` in the manifest is the
minimum contract for that task; widen it deliberately (a reviewed change), never casually.
