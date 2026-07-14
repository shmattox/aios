# AIOS macOS scheduling (launchd)

`register-tasks.sh --env-root <path> --plugin-root <path> [--dry-run]` reads `../tasks.manifest.json`
and installs one launchd LaunchAgent (`com.aios.<id>`) per **enabled `native`** entry — the same
OS-agnostic scheduled class the Windows registrar reads — translating each cron to a
`StartCalendarInterval`. `manual` and `schedule-cloud` entries are skipped (run those in-session / via
`/schedule`). Plists are generated with Python's `plistlib` (correct escaping) and written to
`~/Library/LaunchAgents/`, then loaded with `launchctl bootstrap gui/<uid>`. **`--dry-run` prints the
plists and the `WOULD register …` lines, writing nothing and loading nothing** — the safe preview.

`RunAtLoad` is `false`, so registering never fires a stage. Registration is idempotent: an existing
agent of the same label is `launchctl bootout`ed first.

Each agent runs `/bin/bash run-task.sh <id> <env_root> <plugin_root>` — the single generic runner
(mirror of `../windows/run-task.ps1`). It resolves the task's body + `allowedTools` from the manifest,
launches headless `claude -p`, and logs to `<env_root>/state/task-logs/<task-id>/` (`last-run.log` =
stamped exit-code trailer, `last-result.txt` = full output; launchd's own stdout/stderr go to
`launchd.out.log`/`launchd.err.log` in the same dir for launchd-level failures).

Remove everything with `unregister-tasks.sh` (preview with `--dry-run`). It globs
`~/Library/LaunchAgents/com.aios.*.plist`, so it cleans up even tasks since renamed in the manifest.

Notes:
- **launchd PATH.** launchd hands the runner a minimal `PATH`; `run-task.sh` prepends
  `~/.local/bin`, `/opt/homebrew/bin`, and `/usr/local/bin` so `claude` and `python3` resolve under a
  scheduled (non-login) run on both Apple Silicon and Intel.
- **Missed runs / laptops.** launchd runs a missed `StartCalendarInterval` job when the machine next
  wakes — but a Mac that is closed/asleep at the scheduled time is unreliable. For an often-asleep
  laptop, prefer the no-always-on **cloud variant** (`/schedule`, `../cloud/`).
- **bash 3.2.** The scripts avoid bash-4 features (`mapfile`, associative arrays) so they run on the
  stock `/bin/bash` macOS ships.

`Bash(python:*)` is the exec surface for the unattended tasks — the deterministic engine tools do the
file writes, so the agent only runs them and reports. Each task's `allowed_tools` in the manifest is the
minimum contract for that task; widen it deliberately (a reviewed change), never casually.
