# deploy/github — the GitHub Actions substrate (sub-project A)

**The contract** (vendor-neutral; the only interface between workflow and runner):

| Input | Meaning |
|---|---|
| `leg` | `deploy/tasks/<leg>.md`, executed verbatim |
| `body_path` | that leg's body path (`deploy/tasks/<leg>.md`), resolved from the manifest by `leg_config.py` and passed straight through — the adapter never re-derives it |
| `env_root`, `plugin_root` | absolute checkout paths (desktop layout: `env/`, `env/SecondBrain`, `env/Projects/aios`) |
| `model`, `max_turns`, `allowed_tools` | from `tasks.manifest.json` via `leg_config.py` (native entry + `aios-gh-<leg>` override) |
| `result_path` | the runner MUST write `result.json` here |

Exit code is derived from `result.json` by `run-agent/result_check.py`; **no result file = failure**.
The runner never runs git; `gitsync.py` commits/rebases/pushes (vault first, then env), or with
`--dry-run` exports patches and pushes nothing.

**Context-log check (`ctx` step, A21 parity).** `result.json` is the model's own self-report of
what it did — not evidence. After the runner returns, the workflow independently asks the engine's
own ledger (`state/context-log.jsonl`) whether it carries a record for the leg's declared
`context_stages` (from `leg_config.py`'s `context_stages`, comma-joined) with a `ts` inside the run
window (run start minus 120s clock-rounding slack, through `context_log.py check`). **No push
happens unless it does** — the `sync` step's condition includes `steps.ctx.outcome == 'success'`,
so a failed check blocks the write exactly like a failed leg does. A leg with no declared
`context_stages` skips the check (nothing to verify) and is treated as passing.

**`result.json` schema.** `result_check.py` validates and enforces exactly five keys: `leg` (must
match the leg being run), `run_id`, `status` (`ok`|`degraded`|`failed`), `summary` (string), and
`verify` (a dict with `passed`/`notes`) — required, and `passed` must be exactly `true` whenever
`status: ok` (anti-green-wash, spec §4/§5; an absent `verify` block on an `ok` run is treated as a
failure, not evidence of verification). `degraded` and `failed` don't re-check `verify`. The
spec's richer envelope (`started_utc`, `ended_utc`, `commits`, `usage`) is **not** enforced here —
a runner may still write those fields, but `result_check.py` neither requires nor reads them today.

**Adding a runner:** v1 ships `claude` only, wired inline in `action.yml` — there is no
`run-agent/runners/<name>/` directory. To add one: add a step to `action.yml` gated on `if:
inputs.runner == '<name>'` that (1) reads the prompt from `$RUNNER_TEMP/prompt.txt` (built by
`prompt.py`), (2) executes it with the tool grants in `allowed_tools`, (3) leaves `result.json` at
`result_path`; then add the name to the `case` guard step at the top of `action.yml`. Nothing else
changes.

**Calling it:** `uses: shmattox/aios/.github/workflows/leg.yml@main` with `leg` + `dry_run` and the
three secrets (`secrets: inherit` from a repo that holds `CLAUDE_CODE_OAUTH_TOKEN`, `ENV_DEPLOY_KEY`,
`VAULT_DEPLOY_KEY`).
