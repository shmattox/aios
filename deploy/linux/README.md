# AIOS native scheduling — Linux (cron)

Mirror of `deploy/windows/` (Task Scheduler) and `deploy/mac/` (launchd). One managed crontab
block carries every ENABLED `substrate: native` task from `../tasks.manifest.json`; `manual` and
`schedule-cloud` entries are skipped (run those in-session / via `/schedule`).

## Register

```bash
bash deploy/linux/register-tasks.sh --env-root <env_root> --plugin-root <plugin_root> [--dry-run]
```

- Emits one cron line per task between `# BEGIN aios tasks (managed …)` / `# END aios tasks`
  markers and installs it with `crontab -`. **Idempotent** — re-registering replaces the block
  wholesale; your other crontab lines are untouched.
- The manifest's cron fields are plain integers (`min hour dom mon dow`), emitted verbatim — no
  range/list/step parsing (same contract as both other registrars).
- Registering never fires a stage; cron fires at the next matching minute.
- Each line routes through `deploy/linux/run-task.sh` (a shim exec'ing the OS-agnostic bash
  runner at `deploy/mac/run-task.sh`, which owns manifest resolution, `--allowedTools`, the
  conditional read-only-git prompt line, the `AIOS_MACHINE_RUN` marker, the A21 post-run
  context-log check, and the dated `last-result-*` rotation). The cron line's own
  `>> …/cron.log 2>&1` catches pre-runner failures that would otherwise vanish into cron's
  mail void.
- Logs land in `<env_root>/state/task-logs/<task-id>/` (`last-run.log`, `last-result.txt`,
  `cron.log`).

## Unregister

```bash
bash deploy/linux/unregister-tasks.sh [--dry-run]
```

Removes the managed block (marker-fenced, so it also cleans up tasks since renamed or removed
from the manifest). Everything else in your crontab survives.

## Notes

- Needs a running cron daemon (`cronie` / `cron`) and `python3` on PATH.
- cron's environment is minimal by design; the runner prepends `~/.local/bin`,
  `/opt/homebrew/bin`, and `/usr/local/bin` to PATH before resolving `claude`/`python3`.
- The desktop staying asleep means missed fires (plain cron has no catch-up); for a
  sometimes-off machine prefer the `/schedule` cloud variant (`deploy/cloud/`).
