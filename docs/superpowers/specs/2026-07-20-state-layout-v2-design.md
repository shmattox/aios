# State-layout v2 — nest the flat `state/` root behind a single path seam (A103)

**Date:** 2026-07-20 · **Status:** design-approved (Seth, in-session) · **Backlog:** aios `A103`
**Tier:** review-gate (touches every production pipeline path) · **Version:** ships as **0.8.0**

## Problem

Every runtime file in `<env_root>/state/` is a flat root-level path, and the root inflates by one
file per feature (queue, dedup ledger, context log, router manifest, Notion changelog, four
brief-cache files, garden handoff, sanitize roster — plus whatever the next feature adds). Two
defects, one structural cause:

1. **Human readability** — the root reads as sprawl; the 2026-07-20 env sweep (env commit
   `867d7ff`) archived the residue and completed the README inventory, but flat-plus-README was
   judged insufficient. **Seth decided 2026-07-20: actually nest them.**
2. **Inflation mechanics** — the path contract lives in ~130 literal references across ~25 prose
   files (`skills/*/SKILL.md`, `deploy/tasks/*.md`, pipeline docs), so every new state file lands
   as a new literal scattered through prose, and any layout change is an N-file sweep. Measured
   2026-07-20 (grep across `engine/ skills/ deploy/`): top offenders `skills/brief/SKILL.md` (28),
   `skills/brief/references/gather.md` (11), `deploy/tasks/gate.md` (10). The engine `.py` tools
   are already largely path-agnostic (paths arrive as CLI args); the contract is prose-owned.

Design decisions locked in the brainstorm (Seth, 2026-07-20): **Scheme A** (purpose-scoped
`pipeline/` + `brief/`) over lifecycle or per-stage grouping; **full seam** (tools resolve their
own paths from `<env_root>`; prose stops naming state paths) over a thin literal-path sweep.

## Target layout (Scheme A)

```
state/
  README.md                     ← the only root file
  pipeline/
    queue.json                  captured-ids.json
    context-log.jsonl           context-log-archive-YYYYMM.jsonl
    capture-router-manifest.jsonl   notion-changelog.jsonl
    garden-proposals.json       sanitize-patterns.txt   (gitignored, per-machine)
  brief/
    brief-cache.json  brief-cache.md  brief-cache.prev.json  brief-session.json
    sessions/                   ← was state/brief-sessions/
    resolve-cache/              ← was state/resolve-cache/
  domains/  revert/  rewind/  threads/  evidence/  factory/  factory-health/
  standing-checks/  brainstorm-packets/  task-logs/  backups/     ← ALL unchanged
```

Out of scope, deliberately: `revert/`, `rewind/`, `threads/`, `domains/`, `evidence/`,
`factory*/`, `standing-checks/`, `brainstorm-packets/`, `task-logs/`, `backups/` stay put — they
are already nested, some are huge (1,000+ revert pointers), and moving them buys nothing.

## Component 1 — the path seam (`engine/tools/state_paths.py`)

One module owns the logical-name → relative-path map:

- `STATE_PATHS = {"queue": "state/pipeline/queue.json", "captured_ids": ..., "context_log": ...,
  "brief_cache": "state/brief/brief-cache.json", ...}` — every runtime file and the two moved
  dirs, plus `LEGACY_PATHS` (the v1 flat locations) for migration.
- `resolve(env_root, key) -> Path` — returns the v2 path; before returning, runs the lazy
  migration check for that key (see Component 2). Env-root discovery reuses the existing
  walk-up-to-`state/`+`profile/` helper (`sanitize_check.py:98` pattern — lift it here, have
  `sanitize_check` import it back; single owner).
- CLI: `python state_paths.py resolve <env_root> <key>` (for prose/tests that need a literal) and
  `python state_paths.py migrate <env_root>` (explicit full migration, idempotent; used by setup
  and tests).

Every engine tool that touches a state file gains env-root resolution: `queue_tx.py select
<env_root> --stage awaiting`-style, resolving through the seam. **Explicit-path arguments remain
accepted everywhere** (tests, overrides, back-compat) — env-root resolution is the new default
invocation, not a removal. After this, a future layout change is one edit to `STATE_PATHS`, and a
new feature's state file is a seam entry, never a prose literal — the structural fix for the
inflation pattern.

## Component 2 — migration (zero hand-steps, three installs)

Installs: desktop (reference), laptop, ≥1 external user. Two channels converge them:

- **Tracked files** move via `git mv` on the desktop; laptop receives the moves on pull. (The env
  repo tracks `state/`; the sweeper's machine zone covers post-move churn.)
- **Gitignored/per-machine files** (`sanitize-patterns.txt`; any local residue) move via **lazy
  migration**: on any `resolve()`, if the v2 path is missing and the v1 path exists → atomic move
  (`os.replace`, parent dirs created). The external install migrates the same way on its first
  tool run after update — no manual step, no "run migration" instruction to communicate.

Idempotence rules (mechanical, fix-then-tell — auto-repair and report):
- v1 only → move to v2.
- v1 and v2 both exist, byte-identical → remove v1.
- v1 and v2 both exist, differ → **keep v2**, park v1 as `<name>.pre-v2` beside it, report in the
  tool's output. (v2 is newer by construction: it only exists if something already wrote post-
  migration.)
- Neither exists → fine; tools create at v2 on first write.
- Re-run of `migrate` on a clean v2 tree → no-op, exit 0.

Rollback: git is the recovery path for tracked files (the migration commit reverts cleanly);
`.pre-v2` parks preserve any diverged local file.

## Component 3 — prose de-literalization

Sweep the ~130 references in `skills/`, `deploy/tasks/`, `engine/pipeline/*.md`: instructions
change from literal `"<env_root>/state/queue.json"` arguments to the env-root invocation form.
Where prose genuinely needs to name a location (READMEs, QUEUE.md contract), it states the v2
path once and points at `state_paths.py` as the owner. `QUEUE.md`/`PIPELINE.md`/
`STAGE-CONTRACT.md` are updated as the contract of record. The env repo's `driftcheck` hook
guards routing docs against dead paths afterward.

Also in this sweep — **kill the scratch producer**: `deploy/tasks/brief-cache.md` gains one line
routing the cache-writer's build scratch (`_cache_build.*`) to the OS temp dir, never `state/`
(the 2026-07-20 env sweep untracked the residue; this fixes the generator).

## Component 4 — instance leg (env repo, same session the engine ships)

- Env-side flat-path consumers swept: top `CLAUDE.md` brief prose (`state/brief-*`),
  `Scripts/env-health-collect`, `Scripts/claude-user-config/.../compile_goals.py` (verify its
  actual read), `state/standing-checks/checks.yaml` predicates, both repos' `.gitignore` state
  rules (`sanitize-patterns`, `_cache_build*`, scratch guards → v2 paths).
- `state/README.md` rewritten to the v2 map (it is the human-facing map; the "no undocumented
  root entry" hygiene rule carries over as "no undocumented `pipeline/`/`brief/` entry").
- Plugin version bump to **0.8.0**, reinstall **both scopes** (user + project), restart note to
  Seth (per the 2026-07-20 stale-plugin lesson).

## Testing

- Unit: seam map completeness (every `STATE_PATHS` key resolves; no key collides), migration
  fixture (build a v1 tree → `migrate` → assert v2 layout, then re-run → no-op; the
  both-exist-differ park rule; the both-exist-identical dedup rule).
- Suite: full `python -m pytest` in `Projects/aios` green — existing tests updated where they
  construct flat paths.
- Live (reference install): one brief render and one pipeline pass (capture→sort→gate report)
  against the migrated layout; `grep -rn "state/queue.json|state/brief-cache|state/garden-proposals|state/captured-ids|state/context-log|state/sanitize-patterns" engine/ skills/ deploy/`
  returns zero hits outside `state_paths.py` + explicitly-blessed contract-doc mentions.
- Review: fresh-context **review-gate** (production pipeline paths), zero CRITICAL to ship.

## Risks

- **Mid-update skew** — a task doc from 0.8.0 running against a not-yet-migrated tree: safe, the
  first seam call migrates lazily. The reverse (0.7.x docs against a migrated tree) exists only if
  an install half-updates; explicit paths still accepted, and the v1 paths are gone only after
  something migrated — which implies 0.8.0 is present. Acceptable.
- **Parallel sessions during the desktop migration commit** — same class as any state/ commit;
  single session performs the migration, staged-index inspection before commit (standing lesson).
- **External install we can't observe** — lazy migration is the mitigation; the `.pre-v2` park
  rule means a worst-case conflict loses nothing.

## §Ecosystem-check

Capabilities: **C1** path-resolution seam · **C2** idempotent layout migration · **C3** prose
de-literalization sweep · **C4** migration/regression verification.

**Leg 1 — Anthropic-first.** Enumerated the official plugin cache + installed Anthropic skill set
(2026-07-20, live):

```
$ ls ~/.claude/plugins/cache/claude-plugins-official/
superpowers
$ ls ~/.claude/skills/
backlog-to-goals defuddle find-skills health-trend-analyzer moodtrip-hotel-search
obsidian-markdown owasp-security pdf red-team skill-security-auditor suggest-power-tools xlsx
```

Nothing native or Anthropic-published addresses a plugin's internal state-path indirection or
layout migration — these are code-level concerns inside our own engine, not a harness capability.

**Leg 2 — Public marketplace** (via the `find-skills` skill, live 2026-07-20):

```
$ npx skills find "state layout migration"
pbakaus/impeccable@layout        26.8K installs   (CSS/visual layout — unrelated)
flora131/atomic@layout           91 installs      (unrelated)
shipshitdev/library@layout       41 installs      (unrelated)
$ npx skills find "config path resolution plugin state"
anthropics/claude-for-legal@board-minutes   356 installs   (unrelated)
anthropics/claude-for-legal@written-consent 341 installs   (unrelated)
anthropics/claude-for-legal@matter-close    340 installs   (unrelated)
```

Both queries return only keyword noise (CSS layout, legal docs). No marketplace skill refactors a
plugin's own state layout — expected; this niche is internal by definition.

**Leg 3 — Own skills/tools** (richest leg; enumerated `_tools/`, `Scripts/`,
`Projects/aios/engine/tools/*.py` live 2026-07-20):

```
$ ls _tools/ Scripts/ ; ls Projects/aios/engine/tools/*.py
_tools: consolidate-memory dataroom-ingest driftcheck ecosystem-check env-maintenance gdrive-io
  installed-skills meeting-tickets new-business-unit statement-pull usage-audit
Scripts: chrome-bookmark-sync claude-user-config env-auto-sync env-health-collect env-tasks
  env-usage-audit factory-gate factory-health-sweep finance-feed notion-backup plugin-smoke
  standing-checks statement-reminder x-bookmark-capture youtube-list-capture
engine/tools: brief_render.py brief_session.py capture.py capture_router.py context_log.py
  domain_mirror.py queue_tx.py rewind.py sanitize_check.py session_synth.py state_paths(new) ...
```

Real reuse found, and it shapes the build: **`queue_tx.py`'s atomic-write discipline**
(tmp + fsync + `os.replace`) is the migration's move primitive; **`sanitize_check.py:98`'s
env-root walk-up** becomes the seam's shared discovery helper (lifted, not duplicated);
**`driftcheck`** (env pre-commit) already guards prose paths post-sweep (C3 verification);
**the aios pytest suite + the saved `review-gate` workflow** are C4's harness as-is. No existing
tool provides C1/C2 themselves — `rewind.py`/`domain_mirror.py` do file moves but are
queue/mirror-specific.

**Leg 4 — Full-service replacement.** Checked the wired-connector roster and the platform
registry (`_tools/ecosystem-check/references/platforms.md`, read live 2026-07-20):

```
Wired MCP: Notion, Google Drive, Gmail, Google Calendar, Slack, Granola, Replit, Supabase, Moodtrip.
Registry: Composio, Pipedream, Zapier, Make, n8n, Activepieces, Workato, Tray.ai, Arcade, Peliqan.
```

No external service can own the internal file layout of a local-first Claude Code plugin's
runtime state — the capability is definitionally in-repo. No WebSearch leg warranted (nothing a
search could surface would change an internal-refactor verdict); recorded as a judgment, not a
search result.

| Capability | Verdict | Source / why |
|---|---|---|
| C1 path seam | build-because-none | No native/marketplace/own tool provides logical-path indirection for our engine; reuses `sanitize_check`'s env-root walk-up. |
| C2 layout migration | build-because-none | No existing migrator; move primitive reuses `queue_tx.py`'s atomic-write pattern. |
| C3 prose sweep | adapt-skill | Mechanical edit pass; post-sweep guarded by our own `driftcheck` hook (existing). |
| C4 verification | drop-in-skill | Existing aios pytest suite + the saved `review-gate` workflow, unchanged. |

Reviewer leg: fresh-context anti-fabrication review, 2026-07-20 — **PASS**. Reviewer re-ran Leg 1
(exact match) and Leg 3 (item-for-item match), and source-verified the two internal-reuse claims
(`sanitize_check.py:97-98` `resolve_env_root` walk-up; `queue_tx.py:117-147` tmp+fsync+
`os.replace`). No fabricated or hollow leg found.
