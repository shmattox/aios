# deploy/github — the GitHub Actions substrate (sub-project A)

**The contract** (vendor-neutral; the only interface between workflow and runner):

| Input | Meaning |
|---|---|
| `leg` | `deploy/tasks/<leg>.md`, executed verbatim |
| `env_root`, `plugin_root` | absolute checkout paths (desktop layout: `env/`, `env/SecondBrain`, `env/Projects/aios`) |
| `model`, `max_turns`, `allowed_tools` | from `tasks.manifest.json` via `leg_config.py` (native entry + `aios-gh-<leg>` override) |
| `result_path` | the runner MUST write `result.json` here: `{leg, run_id, status: ok|degraded|failed, summary, verify:{passed,notes}}` |

Exit code is derived from `result.json` by `run-agent/result_check.py`; **no result file = failure**.
The runner never runs git; `gitsync.py` commits/rebases/pushes (vault first, then env), or with
`--dry-run` exports patches and pushes nothing.

**Adding a runner:** create `run-agent/runners/<name>/` with a step that (1) reads the prompt from
`$RUNNER_TEMP/prompt.txt` (built by `prompt.py`), (2) executes it with the tool grants in
`allowed_tools`, (3) leaves `result.json` at `result_path`; then add the name to the `case` guard in
`action.yml` and gate the step on `inputs.runner == '<name>'`. Nothing else changes.

**Calling it:** `uses: shmattox/aios/.github/workflows/leg.yml@main` with `leg` + `dry_run` and the
three secrets (`secrets: inherit` from a repo that holds `CLAUDE_CODE_OAUTH_TOKEN`, `ENV_DEPLOY_KEY`,
`VAULT_DEPLOY_KEY`).
