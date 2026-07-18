---
name: setup
description: Onboard onto AIOS in one interview — detect OS and tools, map the vault, write the profile, generate CLAUDE.md, offer the scheduled pipeline.
---

You are onboarding a person onto AIOS. Output: `~/.aios/config.json` + a complete `<env_root>/profile/`
bundle + a generated `<env_root>/CLAUDE.md` + a scaffolded (or mapped) vault + a `state/` scaffold.
You do NOT edit the plugin engine — it is identical for everyone. Records (Notion / Drive / vault)
are the person's own; the profile only *points* at them.

**Core principle — detect & map, never assume a blank slate.** Many people already run an
Obsidian/markdown vault and some connected tools. Setup's job is to FIND what exists and wire the
profile to it (reference, never duplicate). Only create something when it genuinely doesn't exist.
**Every connector is optional** — AIOS runs vault-only with zero connectors; wire only what this
session actually exposes. The human confirms *judgment* calls (which DB plays which role), never
pastes a URL or ID.

# Phase 0 — Detect the environment

0. **Pre-flight — is AIOS already installed? (guard against clobbering an existing install).** BEFORE
   any detection or write, run the canonical resolution in `${CLAUDE_PLUGIN_ROOT}/engine/pipeline/RESOLVE-INSTALL.md`
   (the marker walk-up — cwd itself then each parent for a dir with BOTH `state/`+`profile/`, anchor-confirmed
   via `state/queue.json`|`profile/connectors.yaml` — then `~/.aios/config.json` as fallback). This finds an
   existing install even when the config is unreachable in a sandbox. **If it resolves an `<env_root>`, STOP** —
   do NOT re-run setup. Say: "AIOS is already installed at
   `<env_root>`. If a brief or skill reported 'not set up', that was a sandbox reachability issue (the
   config lives outside the Cowork mount), NOT a missing install — run the skill from `<env_root>` (the
   folder with `state/`+`profile/`) or connect the sandbox there. Re-running setup would OVERWRITE your
   profile and config. To reinstall anyway, confirm explicitly: 'yes, reinstall over `<env_root>`'."
   Proceed past this guard ONLY on a genuine fresh install (no markers AND no readable config) or that
   explicit confirmation.
1. **OS + Python spelling.** Try `python -c "import sys;print(sys.platform)"` → `win32` | `darwin` |
   `linux`. If that fails (stock macOS ships no `python` shim), try
   `python3 -c "import sys;print(sys.platform)"`. **If BOTH fail → STOP**: "AIOS needs Python 3
   (stdlib only). Install it, then re-run /aios:setup." Remember the OS (routes Phase 6) AND which
   spelling worked (`python` vs `python3`) — use that exact spelling for **every** subsequent `python`
   invocation in this install (Phase 5 writes, the Phase 6 smoke run, the deploy task bodies). Note:
   the scheduled tasks (Phase 6) invoke Python via the runner, which resolves the spelling itself
   (`python` on Windows, `python3` then `python` on macOS). Native scheduling is wired on **win32**
   (Task Scheduler), **darwin** (launchd), and **linux** (cron, A6).
2. **Connectors.** Enumerate which MCP tools are actually available this session (Notion, Gmail,
   Drive, Calendar). ALL are optional — **never fail on absence; wire only what exists.** If Notion
   is absent, Phases 2–3 are skipped entirely and the pipeline + brief run vault-only.
3. **Env root.** Ask where AIOS should live (default `~/aios`). If they already have an
   Obsidian/markdown vault (a folder of `.md` with frontmatter, possibly `.obsidian/`), offer to
   **MAP** it (reference it in the profile, **never copy** — ensure `00_Inbox/auto/` exists at the
   vault root (create if missing)); otherwise scaffold a fresh vault from
   `${CLAUDE_PLUGIN_ROOT}/engine/kb-schema/` taxonomy — **one KB folder per domain group** — with
   this exact per-KB layout (Contract 7):
   `raw/{inbox,processed,sessions,archive}` +
   `wiki/{knowledge,sources,people,companies,projects,mocs,journal,staging}` + `wiki/index.md` +
   `wiki/log.md` + `wiki/.templates/` + `outputs/{charts,decks,queries}`.
   (`journal`, never `daily`. `knowledge/` = the durable distilled layer, the Distill step's target.) Also scaffold a vault-root intake tree
   `00_Inbox/auto/{webclipper,gmail}/` (the capture-router task's intake; sources drop raw
   captures here).
4. **Write `~/.aios/config.json`** (Windows: `%USERPROFILE%\.aios\config.json`):
   `{"env_root": "<chosen absolute path>"}`. Create `<env_root>/profile/` and the `<env_root>/state/`
   scaffold (an empty `state/queue.json` — `{"queue": []}` — plus an empty `state/context-log.jsonl`) — the pipeline reads these.

# Phase 1 — Requirement-driven roles: figure out WHAT to map before mapping

Drive the mapping from what the **enabled skills need**, not from a fixed list. Ask which skills they
want on (brief is the wedge; capture/sort/ingest/gate/garden are the pipeline), then fill exactly the
roles those skills require — nothing more:

| Skill | Required roles |
|---|---|
| `brief` | `notion.tasks` per task source **(only if Notion connected)** · `notion.state` (headline-positions DB, e.g. Assets) **(only if Notion connected)** · `calendar` (optional context, only if connected) · the vault |
| `inbox-capture` | `gmail` and/or `drive` (inbound, only if connected), plus any source adapters they use |
| `sort` / `ingest` / `gate` | the vault + the queue (env-level state; **no connector**) |
| `garden` | the vault only |

Each task/state role is per **domain group** (a person may have several domain groups, or just one).
Collect the domain groups here too (Phase 4 detail) so discovery knows how many task sources to find.
Every Notion-backed role is only offered when Notion is connected — a vault-only install fills none of
them and the brief/pipeline run against the vault + queue alone.

# Phase 2 — Connector discovery: suggest, don't ask-to-paste

**SKIP this phase entirely if Notion is not connected — the pipeline + brief run vault-only.**

For each required role, DISCOVER the candidate and propose a mapping; the human confirms.

1. **Notion (proven flow).**
   a. `notion-get-teams` → list teamspaces; **filter out `in_trash: true`** (workspaces accumulate
      trashed dupes).
   b. **Teamspace IDs are NOT fetchable pages** (`notion-fetch` on a team ID 404s). Reach a
      teamspace's databases through a **hub page**: find its landing/"Dashboard"/"Governance" page
      (via `notion-search` scoped to the teamspace) and `notion-fetch` it — the result lists child
      DBs as `<database … data-source-url="collection://…">` tags. **If a `_Manifest`-style index DB
      exists, read it first** — it's a purpose-built "resolve every ID in one shot" registry (the
      fast path for an already-populated env / the refresh use case).
   c. **Do NOT rely on `notion-search` to find a DB by name** — semantic search returns *pages that
      mention the word*, not the database object. Search is for *locating the hub page*, not for
      resolving DB IDs.
   d. **Map by ROLE, not by title.** A DB's title may not match its role, AND titles drift — mapping
      by ID/role is what makes a rename a non-event. Read the hub's role descriptions (the body often
      says "Tasks — the living backlog…") to map role→DB, then **confirm with the human** (the
      name-match is a *suggestion*, never treated as truth). Roles: backlog/"living tasks"→`notion.tasks`,
      "Assets & Liabilities"/"Net Worth"/headline-positions→`notion.state`, "Decision Log"→`notion.decision`,
      "Events"→events signal, "Reach Out"→going-quiet signal, etc.
2. **Drive.** `list_recent_files` / `search_files` → propose the records root folder (only if Drive
   is connected).
3. **Confirm in ONE round.** Present the suggested role→DB mappings via AskUserQuestion (group all
   roles into one multi-question round). The mapping is a judgment ("*which* DB is your Tasks DB"), so
   it gets exactly one human confirm — but pre-filled with the name-match, so usually it's just "yes."
   Resolve to instance IDs automatically; never ask for a pasted URL/ID.
3a. **Ambiguity → ask, don't guess.** If 0 or ≥2 plausible DBs match a role, surface the options (or
    "none found — create one?") rather than picking silently.

# Phase 3 — Find-or-create the priority views

**SKIP this phase if Notion is not connected.**

Task sources are read through a **filtered, priority-ordered view** — an engine convention true for
every install (keeps reads small + priority-first; avoids the unfiltered-table dump). For each
`notion.tasks` source:

1. **Find** a view filtered `Status ≠ Done`, sorted `Priority → Due`, `page_size ≈ 25`, ideally named
   "Open — by priority". If it exists, take its `view://` ID.
2. **Create** it if absent (find-or-create) and take the new `view://` ID.
3. **Record only the `view://` ID** in `connectors.yaml` — the ID is the profile fact; the *rule*
   (filter/sort/size) lives in the engine, not the profile. Non-task sources (Assets, Reach-Out) stay
   raw `collection://` (no priority field to filter on).

# Phase 4 — Interview (the human-only parts)

One AskUserQuestion round where possible:

1. **Identity** — name, the entity/structure they operate (sole prop? company? family office?).
2. **Domains** — "what areas of your life/work do you want tracked?" Each becomes a `domains.yaml`
   row (name, type, trigger words, brief group). Start small — 3–5 is plenty. (Already partly
   gathered in Phase 1 to size discovery.)
3. **Discipline** — "rules you want enforced?" (validate ownership against paper; stay within
   allocation bands; never record a partner % from memory). Most people: none → empty `discipline.md`.
   Write only what they name.
4. **Goals** — a few sentences on what they're optimizing for; seeds the brief's priority-triage.
5. **Auto-ship opt-in (safety default: NOTHING auto-ships).** For each domain ask: "may the unattended
   gate ship reviewed, non-economic items in `<domain>` without you?" **Warn against opting in any
   domain holding financial / ownership / legal material** — those must stay human-gated. Write the
   opted-in KB list to `profile/connectors.yaml: gate.auto_ship_kbs` (default `[]` — empty means
   nothing auto-ships until they opt a domain in).
6. **Capture routing (one simple question, or take the default).** "Which KB should un-tagged captures
   land in by default?" → `capture_router.default_kb` (default: their first / personal domain group's
   KB). Folder-hint keyword→KB rules are optional and start empty → `capture_router.folder_hints: {}`
   (they tune these later as their bookmark folders evolve). Both keys live in `connectors.yaml`.
7. **Optional git history (safety default: NOT required).** Ask: "Track your vault in git for
   history + sync? [y/N]" (default **N**). AIOS needs no git to run — undo is a file snapshot
   (`rewind.py`), not a commit. A *yes* only means Phase 5 shows you the `git init` commands (and
   offers to run them); *no* skips it silently. Record the y/n for Phase 5.

# Phase 5 — Write, scaffold, generate

- **Write the five profile files** into `<env_root>/profile/`:
  - `identity.md` — name + entity/structure.
  - `connectors.yaml` — the vault root + `vault.live_kb_map` (kb→folder, one per domain group); the
    DISCOVERED Notion instance IDs + `view://` IDs from Phases 2–3 **if Notion was connected, else
    omit the notion.* block entirely**; `gate.auto_ship_kbs` (from Q5, default `[]`);
    `capture_router.default_kb` + `capture_router.folder_hints` (from Q6); `session_capture.evidence_dir`.
  - `domains.yaml` — one row per domain (name, type, trigger words, brief group) + `brief.trigger`
    (the ritual phrase), `brief.surface` (conversational default), `brief.default_scope: all`, and
    `session_capture.domain_map` (cwd→domain).
  - `discipline.md` — only the rules they named (empty if none).
  - `goals.md` — the optimizing-for narrative.
- For each declared domain that doesn't already exist, scaffold its KB folder (Phase 0 taxonomy) and
  add a `vault.live_kb_map` entry.
- **Optional git history (only if Q7 = yes) — instruct, don't automate (matches `new-business-unit`).**
  Run this *after* the vault/profile/KB scaffold exists so a baseline commit would capture a complete
  state. Print the exact commands as the user's native step:
  ```
  git init <vault>
  git -C <vault> add -A
  git -C <vault> commit -m "AIOS baseline"
  ```
  Tell them: history is on once they run it; commit as they go or wire their own cadence; for sync,
  add a remote and push (BYO); **AIOS commits nothing for them.** Then offer `Run these now? [y/N]`
  and execute the three commands **only** on an explicit yes. Guards, checked before running:
  - `git -C <vault> rev-parse --is-inside-work-tree` already succeeds → **skip** (already
    tracked; no re-init, no clobber), report it.
  - `git` is absent (i.e. `git --version` fails) → don't offer to run; print "install git, then run the
    commands above." **Never fail setup** — the engine runs without git.
  - Q7 = no, or `Run now?` = no → print the commands and move on; execute nothing.
  This `Run now?` is the ONLY place setup ever invokes git, and only on explicit opt-in.

- **Generate `<env_root>/CLAUDE.md`** via the deterministic renderer (A25): compose the 7 token
  VALUES below from the profile (that's the judgment — which ritual shape this install gets),
  write them to a temp `tokens.json`, then:
  ```
  python "${CLAUDE_PLUGIN_ROOT}/engine/tools/setup_render.py" \
    --template "${CLAUDE_PLUGIN_ROOT}/engine/templates/CLAUDE.template.md" \
    --tokens <tokens.json> --out "<env_root>/CLAUDE.md"
  ```
  The tool owns the substitution, the Session-End renumber (Contract 3), and the unresolved-token
  guard (Contract 6 — it refuses to write a file with a literal `{{...}}`). The 7 tokens:
  - `{{ENTITY_NAME}}` ← `identity.md`.
  - `{{DOMAIN_COUNT}}` ← count of `domains.yaml` rows.
  - `{{BRIEF_TRIGGER}}` ← `domains.yaml: brief.trigger`.
  - `{{ROUTING_TABLE}}` ← a markdown table from `domains.yaml` rows (trigger-keywords → specialist).
  - `{{DISCIPLINE_MODULES}}` ← `discipline.md` (empty block if none enabled).
  - `{{SESSION_START_STATE_BLOCK}}` / `{{SESSION_END_STATE_BLOCK}}` — emit per the two cases below.

  **SESSION_START_STATE_BLOCK.**
  - *Notion wired* — emit the Notion ritual, iterated per domain group (one Tasks-query line per
    group's tasks source; Session-Log / _Manifest lines only for DBs that exist in the profile):
    ```
    1. Fetch the most recent **Session Log** row — `<notion.session_log DS>`.  (per group that has one)
    2. Query **Tasks** where `Status != Done` AND `Priority = <urgent>` — `<notion.tasks DS>`.  (per group)
    3. Resolve IDs from **_Manifest** if present — `<notion.manifest DS>`.  (only if it exists)
    N. If the message names a domain, identify the target specialist (don't pre-load its knowledge).
    ```
  - *Vault-only* — emit the vault ritual:
    ```
    1. Read the brief cache (`<env_root>/state/brief-cache.json`) for current standing state.
    2. `queue_tx.py select` the held / awaiting items to know what's in review.
    N. If the message names a domain, identify the target specialist (don't pre-load its knowledge).
    ```
  - **Both cases MUST end with the specialist line as the final numbered item** (Contract 2 — the
    template no longer carries it). Number it as the next integer after the block's real items.

  **SESSION_END_STATE_BLOCK.** Each case here emits **exactly 2 numbered items**, so the template's
  hardcoded trailing items (numbered 3 and 4 after the token) read continuously — no renumber needed.
  - *Notion wired*:
    ```
    1. Write a **Session Log** row (Date, Focus, What We Did, Decisions, Open Threads, Next Focus) — `<notion.session_log DS>`.
    2. Update **Tasks**; **Decision Log** for strategic/irreversible only; **Change Log** for factual flips.
    ```
  - *Vault-only*:
    ```
    1. Append a session record to `<vault>/<primary-kb>/raw/sessions/<UTC-date>.md` (frontmatter `type: session-record`) — capture folds it into the journal note next run.
    2. `queue_tx.py add` any follow-ups surfaced this session as raw items (so ingest drafts them).
    ```
  - **Renumber rule (Contract 3) — enforced by `setup_render.py`** (it renumbers the Session-End
    section sequentially after substitution; never edit the template).

- **Token guard (Contract 6) — enforced by `setup_render.py`** (it refuses to write a file with a
  literal `{{...}}` and lists the unresolved tokens; a non-zero exit = STOP and fix your tokens.json).

# Phase 6 — Pipeline activation (OS-routed)

- **FIRST, on every OS: one manual smoke run.**
  1. Seed the shipped example into the vault — copy (quoted paths)
     `"${CLAUDE_PLUGIN_ROOT}/skills/setup/example/welcome.md"` →
     `"<vault>/<default-kb>/raw/inbox/example/welcome.md"`.
  2. Run the capture+sort+ingest task body at `${CLAUDE_PLUGIN_ROOT}/deploy/tasks/ingest.md` in-session
     (it captures via `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/capture.py"` — using the Python
     spelling from Phase 0 — sorts, and drafts Phase A into `<vault>/<kb>/wiki/staging/`). That task
     body is written for a headless native run and expects `<env_root>` from its runner prompt; when
     executing it in-session here, use `<env_root>` from `~/.aios/config.json`.
  3. Show the gate's verdict WITHOUT running the gate: `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/queue_tx.py" select "<env_root>/state/queue.json" --stage awaiting` (Python spelling from Phase 0) — show the person the seeded item's `lane`, `recommended`, and `rec_reason` fields (the Phase-A pipeline computed them; nothing ships during setup — the real gate runs later as `/aios:gate` or the scheduled `aios-gate-auto`).
  4. Render **`aios:brief`** (proves the ritual phrase works end-to-end).
- **win32 — offer the scheduled pipeline.** On yes (Contract 5):
  1. Dry-run first:
     `powershell -File "${CLAUDE_PLUGIN_ROOT}/deploy/windows/register-tasks.ps1" -EnvRoot "<env_root>" -PluginRoot "${CLAUDE_PLUGIN_ROOT}" -DryRun`
  2. **Show the person the `WOULD register …` lines; confirm.**
  3. Re-run **without** `-DryRun` (real registration).
  4. Verify: `Get-ScheduledTask -TaskName 'AIOS *'` → expect **N** tasks, where N is the enabled
     `native` count DERIVED from the manifest (never a memorized number — it grows as tasks are
     added): `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/task_manifest.py"` (Python spelling from
     Phase 0). Manual + cloud + `enabled:false` entries are skipped. (Today N is 7.)
  - If they decline: daily driving is session-only (`/aios:pipeline` + `/aios:gate` +
    `/aios:brief`); re-run /aios:setup anytime to add schedules.
- **darwin — offer the scheduled pipeline (launchd).** Same shape as win32, via the Mac registrar. On yes (Contract 5):
  1. Dry-run first (writes nothing, loads nothing — prints the plists + `WOULD register …` lines):
     `bash "${CLAUDE_PLUGIN_ROOT}/deploy/mac/register-tasks.sh" --env-root "<env_root>" --plugin-root "${CLAUDE_PLUGIN_ROOT}" --dry-run`
  2. **Show the person the `WOULD register …` lines; confirm.**
  3. Re-run **without** `--dry-run` (real registration — installs LaunchAgents to `~/Library/LaunchAgents/`, `launchctl bootstrap`ed; `RunAtLoad` is false so nothing fires on registration).
  4. Verify: `launchctl list | grep -c com.aios` → expect **N** agents, N = the manifest-derived
     enabled-native count `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/task_manifest.py"` (today 7 —
     read it, don't assert a literal). Uninstall anytime with `deploy/mac/unregister-tasks.sh`.
  - Caveat (laptops): launchd fires a missed job when the machine next wakes, but a closed/asleep Mac at the scheduled time is unreliable — for an often-asleep machine the no-always-on **cloud variant** (`/schedule`) fits better.
  - If they decline: daily driving is session-only, same as the win32 decline path.
- **linux — offer the scheduled pipeline (cron).** Same shape as win32/darwin, via the Linux
  registrar. Needs a running cron daemon (`cronie`/`cron`) — if `crontab` is absent, fall back to
  session-only below. On yes (Contract 5):
  1. Dry-run first (writes nothing — prints the managed crontab block):
     `bash "${CLAUDE_PLUGIN_ROOT}/deploy/linux/register-tasks.sh" --env-root "<env_root>" --plugin-root "${CLAUDE_PLUGIN_ROOT}" --dry-run`
  2. **Show the person the block; confirm.**
  3. Re-run **without** `--dry-run` (real registration — installs one marker-fenced managed block
     via `crontab -`; idempotent, other crontab lines untouched; nothing fires on registration).
  4. Verify: `crontab -l | grep -c run-task.sh` → expect **N** lines, N = the manifest-derived
     enabled-native count `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/task_manifest.py"` (today 7 —
     read it, don't assert a literal). Uninstall anytime with `deploy/linux/unregister-tasks.sh`.
  - Caveat: plain cron has NO catch-up — a machine asleep/off at the scheduled minute skips that
    run entirely; for a sometimes-off machine the no-always-on **cloud variant** (`/schedule`)
    fits better.
  - If they decline (or no cron daemon): daily driving is session-only — `/aios:pipeline` (or the
    stages individually as above) + `/aios:gate` + `/aios:brief`; re-run /aios:setup anytime.

# VERIFY (the gate — every run, blocking before declaring setup done)

Report each check's result explicitly; any failure → fix before declaring success.

- `~/.aios/config.json` parses and its `env_root` directory exists.
- The five `profile/` files are present.
- The generated `<env_root>/CLAUDE.md` has **zero** `{{` matches (Contract 6).
- Vault taxonomy dirs exist for each KB (`raw/{inbox,processed,sessions,archive}` +
  `wiki/{knowledge,sources,people,companies,projects,mocs,journal,staging}` + `wiki/index.md` +
  `wiki/log.md` + `wiki/.templates/` + `outputs/{charts,decks,queries}`).
- Vault-root intake tree `00_Inbox/auto/{webclipper,gmail}/` exists (the capture-router task's
  intake; sources drop raw captures here).
- `<env_root>/state/` scaffold exists (`queue.json` + `context-log.jsonl`).
- The smoke run produced a staged draft and a queue item with `lane`, `recommended`, and `rec_reason` fields.
- If a schedule was registered: the OS count matches the enabled-native count DERIVED from the
  manifest — `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/task_manifest.py"` (the expected N; today 7,
  but read it, never assert a literal — a task add must not fail this gate). Per-OS actual count:
  - Windows: `Get-ScheduledTask -TaskName 'AIOS *'` (count of returned tasks) equals N.
  - Linux: `crontab -l | grep -c run-task.sh` equals N.
  - macOS: `launchctl list | grep -c com.aios` equals N.

# Refresh path (regenerate an existing profile)

Refresh is the DELIBERATE re-run over an existing install — so it is exactly the case the Phase-0
pre-flight guard holds for. The guard WILL detect the existing install and require the explicit
"yes, reinstall over `<env_root>`" confirmation; giving that confirmation IS choosing refresh (an
accidental re-run in a sandbox stops at the guard instead). Then the same flow runs against an
already-populated env: Phase 0 detects everything, Phases 2–3 re-discover and
re-resolve IDs (catching drift where a DB was renamed/moved), and Phase 5 rewrites the profile + the
generated CLAUDE.md. Because it re-runs discovery, the refresh is also the portability proof — the same
code that reconstructs one person's profile onboards the next from scratch.

# Guardrails

- **Never put a fact in the plugin engine.** If onboarding reveals something the engine should do for
  everyone, that's an engine change (versioned), not a profile entry.
- **Discovery is read-only until the human confirms.** Enumerate freely; only write the profile +
  create views/DBs after the one confirm round.
- **Auto-ship defaults to nothing.** `gate.auto_ship_kbs` starts `[]`; a domain ships unattended only
  after an explicit Q5 opt-in, and never a financial/ownership/legal one.
- Paper-Governs / economic facts: record `verbal` until the person provides executed paper.
