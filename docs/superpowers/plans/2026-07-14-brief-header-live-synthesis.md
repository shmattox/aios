# Brief Header Live-Synthesis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the precomputed, chronically-stale brief "preview blurb" (`state/brief-headline.md`) and its instant-paint machinery; replace it with a static loading line plus a header prose synthesis generated live from the data each brief actually shows.

**Architecture:** The brief is skill-prose (`SKILL.md` + `references/gather.md`) driving deterministic engine tools. The header prose was model-authored into a `.md` file at gather time and echoed verbatim on the next trigger — a precompute that goes stale because its write-back doesn't fire at-desk. This change removes the artifact and the two-pass paint, moves the same synthesis to render-time, and repoints/removes the code, template, and env docs that referenced the file. Structured `brief-cache.json` is untouched.

**Tech Stack:** Markdown skill instructions; Python 3 stdlib engine tools (`pytest` for the two tests touched); two git repos — `aios` (Tasks 1–4) and `claude-env`/env-root (Task 5).

## Global Constraints

- **Two repos.** Tasks 1–4 edit the **aios** repo (`C:/Users/sethh/Documents/Claude/Projects/aios`). Task 5 edits the **env-root / claude-env** repo (`C:/Users/sethh/Documents/Claude`). Commit in the repo the file belongs to; never `git` from a sandbox (this is a native session — git is safe here).
- **Never rewrite historical records.** Dated archive keeps its original wording — it correctly describes what the system did then. Do **not** touch: `Projects/aios/docs/superpowers/{specs,plans}/2026-07-0*`, `Projects/aios/.superpowers/**`, `Memory/archive/**`, `Memory/decisions.md` prior lines, `Memory/general.md`. Only the 2026-07-14 spec/plan and the *live* instruction/code/template/env-doc surfaces change.
- **Structured cache is out of scope.** `brief-cache.json` / `brief-cache.md` and `validate_cache` stay exactly as they are. `headline_bubbles` (the count chips inside the JSON) **stays** — only the standalone `brief-headline.md` prose file dies.
- **The deterministic health lines survive, relocated.** The pipeline-health, factory-health, and economic-figures lines (emitted verbatim by `pipeline_health.py` / `brief_render.py factory-health` / `resolve_brief.py header`) were the headline file's last lines. They move to **render-time**, lifted into the header — never hand-composed (unchanged deterministic-render rule).
- **Acceptance grep is the real scope contract:** after all tasks, no *live* instruction/code/template/env-doc references `brief-headline` (historical archive excepted).

---

### Task 1: Remove the latent precomputed-headline monitor

`brief_freshness_check.py` exists solely to alarm when the scheduled precompute wrote no fresh `brief-headline.md`. Grep confirms it is invoked by **no** live task or tool (only historical SDD reports + BACKLOG mention it). With the headline file gone, it monitors a file that will never exist. Delete it and its test rather than repoint it (YAGNI — nothing calls it; a future precompute monitor would trivially target `brief-cache.json`).

**Files:**
- Delete: `engine/tools/brief_freshness_check.py`
- Delete: `engine/tools/tests/test_brief_freshness_check.py`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing (removal only).

- [ ] **Step 1: Confirm no live invocation exists**

Run:
```bash
cd C:/Users/sethh/Documents/Claude/Projects/aios && \
grep -rln "brief_freshness_check" . --include=*.py --include=*.md --include=*.json --include=*.yaml \
  | grep -vE "__pycache__|\.superpowers/|docs/superpowers/plans/2026-07-08|BACKLOG\.md"
```
Expected: **no output** (only historical/plan/backlog mentions exist, which are excluded). If any live task body or tool prints, STOP — it needs repointing to `brief-cache.json` instead of deletion; revisit with that finding.

- [ ] **Step 2: Delete the tool and its test**

Run:
```bash
cd C:/Users/sethh/Documents/Claude/Projects/aios && \
git rm engine/tools/brief_freshness_check.py engine/tools/tests/test_brief_freshness_check.py
```

- [ ] **Step 3: Run the engine test suite to confirm nothing depended on it**

Run:
```bash
cd C:/Users/sethh/Documents/Claude/Projects/aios && python -m pytest engine/tools/tests/ -q
```
Expected: PASS (no collection errors, no import failures referencing the deleted module).

- [ ] **Step 4: Commit**

```bash
cd C:/Users/sethh/Documents/Claude/Projects/aios && \
git commit -q -m "Remove latent brief-headline freshness monitor

brief_freshness_check.py only watched state/brief-headline.md's mtime;
that file is being deleted and no live task invokes the check. A future
precompute monitor would target brief-cache.json.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Rewrite the brief SKILL.md entry + render flow

Replace the instant-paint entry and the two-pass render with a static loading line plus a render-time header synthesis. Fix every scattered PASS-1/PASS-2/headline reference in the file.

**Files:**
- Modify: `skills/brief/SKILL.md` (lines ~6–32 the `# ⚡ FIRST` block; ~66–113 `## Render flow`; and one-liners at ~119, ~179–180, ~230, ~232, ~313, ~421)

**Interfaces:**
- Consumes: `brief-cache.json` (unchanged), `brief_session.py cache-status` (unchanged), the three health-line tools (unchanged).
- Produces: the header-synthesis contract Task 3 (gather.md) points at — header = narrative prose (model) + count chips (`headline_bubbles`) + the three deterministic health lines, assembled at render, never written to a standalone file.

- [ ] **Step 1: Replace the `# ⚡ FIRST` section (lines 6–32) with a loading-ack section**

Replace the entire block from `# ⚡ FIRST — instant paint ...` through the `**§0 Resolve the install first ...**` line with:

```markdown
# ⚡ FIRST — loading ack (before any tool, before the gather)

The trigger phrase's FIRST emitted output is a single STATIC line — no file read, no precompute,
so it can never be stale. Emit it immediately, then proceed to resolve + gather:

```
🧭 Gathering your brief… (last run {age})
```

`{age}` is a nicety, never load-bearing: read it cheaply from the `generated_utc` in
`state/brief-cache.json` (or the `brief-session.json` mtime) if trivially available; if not,
omit the parenthetical entirely and just emit `🧭 Gathering your brief…`. Do NOT read, echo, or
synthesize any prose here — the header paragraph is produced later, at render time, from the data
this run actually gathers (`## Render flow`).

Resolve `env_root` for the gather: cwd if it contains both `state/` and `profile/`, else walk UP
parents to the first dir that does (in-mount markers; never chase the out-of-mount
`~/.aios/config.json`). If `env_root` does not resolve (cwd outside the env tree), do NOT run
`/aios:setup` — emit `RESOLVE-INSTALL.md` step-3 guidance and STOP.

**§0 Resolve the install first:** `${CLAUDE_PLUGIN_ROOT}/engine/pipeline/RESOLVE-INSTALL.md` —
markers first (walk up for `state/`+`profile/`), config second; only when BOTH fail is the install
absent, and even then STOP with guidance, never auto-`/aios:setup`.
```

- [ ] **Step 2: Rewrite `## Render flow` (lines ~66–113) two-pass → single flow**

Replace the `## Render flow — instant paint, then LIVE GATHER (the happy path)` heading and its body (through the end of the numbered PASS steps, ending before `## Cache contract`) with:

````markdown
## Render flow — loading ack, then LIVE GATHER, then render-time header (the happy path)

**The at-desk trigger is a live gather** (the overnight precompute was retired 2026-07-02, A8). The
loading ack (`# ⚡ FIRST`) is emitted instantly; the header prose is synthesized AFTER the gather,
from the exact data about to be shown — so it can never drift out of sync with the brief.

0. **Resolve scope** (instant — a cwd lookup per `# Scope`, not a gather). Scope decides which slice
   renders.
1. **Emit the loading ack** (`# ⚡ FIRST`) — one static line, immediately.
2. **Decide whether to gather or reuse a recent cache.** Run the tested boolean — don't re-derive it:
   ```
   python "${CLAUDE_PLUGIN_ROOT}/engine/tools/brief_session.py" cache-status "<env_root>/state/brief-cache.json" \
     --max-age-min <profile brief.max_age_min, default 720> \
     [--notion-enabled] [--session-has-notion] \
     --cwd <session cwd> --domain-map '<session_capture.domain_map JSON>' \
     --vault-root "<vault>" --kb-map '<vault.live_kb_map JSON>'
   ```
   → `status`: `fresh` | `stale` | `degraded` | `missing`.
   - **Stale / missing / degraded (the NORMAL at-desk case):** run the full gather
     (`references/gather.md` → `# Gather`), populate the cache data (`## Cache contract`),
     `validate_cache`, and **write the cache files back** (`brief-cache.{json,md}` only — there is no
     headline file to write). Render from that fresh data.
   - **Fresh AND at parity:** render straight from the cache, then the cheap delta-check (one Notion
     query for any urgent task new-or-newly-escalated since `generated_utc`; append "↑ since {time}"
     only if something changed).
3. **Synthesize the header at render time — from the data just gathered/loaded, never a stored file.**
   The header is assembled fresh on every run and consists of:
   - a **date + `as of {generated_utc}` masthead** (echo the scope: `All silos · as of …` at root,
     `Family Office · as of …` scoped);
   - the **count chips** lifted from `headline_bubbles` in the cache JSON;
   - a **2–3 sentence narrative synthesis** — the week's theme/gravity in {{ENTITY_NAME}}'s voice
     (model-authored from the gathered payload; NOT a bare task line), the **Top** item + **First move**;
   - the three **deterministic health lines**, each lifted VERBATIM (never hand-composed):
     `pipeline_health.py --path "<env_root>/state/context-log.jsonl"` (pipeline health),
     `brief_render.py factory-health "<env_root>/state/factory-health/latest.md"` (factory health),
     `resolve_brief.py header "<resolve.cache_dir>/sweep.json"` (economic-figures flag).
     If a tool is unreachable, OMIT its line — never hand-compose it.
   Because this synthesis runs against the data being displayed, its `as of` stamp is always THIS
   run's — the staleness the old precomputed `brief-headline.md` suffered cannot recur.
4. **Then the stationed walk** — `brief_session.py status` (scope-match guard, Resume/Start-over),
   then ONE STATION AT A TIME: Stage 0 (Settle), Stage 1 (KB), Stage 2 (domain cards). Preserve the
   designed segmentation (icon H2 headers + counts, `---` rules, `### N · {title}` item headers, the
   `⏱`/`📋`/`⚑` lines, the two-layer blockquote with inline A/B/Other buttons). Recompute the held
   panel fresh (`queue_tx.py select --stage awaiting`).
````

- [ ] **Step 3: Fix the `## Cache contract` summary paragraph (line ~119)**

Find the sentence naming three files (begins "Whoever gathered … ends by writing three files, smallest first, atomic …" and lists `brief-headline.md` … `brief-cache.json` … `brief-cache.md`). Replace it so it names **two** files and drops the headline:

```markdown
Whoever gathered — the on-trigger live gather (normal) or the optional scheduled cache-writer —
ends by writing two files, smallest first, atomic (write → re-read → verify parses → retry ×3):
`<env_root>/state/brief-cache.json` (the structured payload — the source of truth, always the full
all-silos superset; `validate_cache` is the completeness exit gate) and
`<env_root>/state/brief-cache.md` (GENERATED from the JSON via `brief_render.py`, never composed by
hand). The JSON carries `headline_bubbles` (the count chips the render-time header lifts) and an
optional `domain_display` map (kb → display-name) that `brief_render.py` consumes. Full write rules:
`references/gather.md` → `## Cache contract`. The header prose is NOT a file — it is synthesized at
render time (`## Render flow` step 3).
```

- [ ] **Step 4: Fix the remaining one-liner references**

Apply these exact edits:
- **~line 179–180** (`# Scope` intro): change `**Resolve scope FIRST, before PASS 1**, and carry it through both passes (headline, stations, KB filter, delta-check).` → `**Resolve scope FIRST, before the gather**, and carry it through the render (masthead, stations, KB filter, delta-check).`
- **~line 230**: `**Echo the scope in the headline masthead**` → `**Echo the scope in the render-time masthead**` (rest of line unchanged).
- **~line 232** (Factory Standup panel): `During PASS 2 gather for a` → `During the gather for a`.
- **~line 313** (walk tracker legend): `shows only after PASS 1 headline` → `shows only after the loading ack`.
- **~line 421** (Gather section): `Execute it whenever PASS 2 needs a live gather or a cache-writer run gathers` → `Execute it whenever the render flow needs a live gather or a cache-writer run gathers`.

- [ ] **Step 5: Verify no `brief-headline` or `PASS 1/2` references survive in the skill**

Run:
```bash
cd C:/Users/sethh/Documents/Claude/Projects/aios && \
grep -nE "brief-headline|PASS 1|PASS 2|instant preview card|instant paint" skills/brief/SKILL.md
```
Expected: **no output.**

- [ ] **Step 6: Commit**

```bash
cd C:/Users/sethh/Documents/Claude/Projects/aios && git add skills/brief/SKILL.md && \
git commit -q -m "Brief: replace instant-paint with render-time header synthesis

First output is now a static loading ack; the header prose is synthesized
after the gather from the data being shown, so it cannot go stale. Two-pass
render collapses to one flow. Drops all brief-headline.md references.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Update gather.md cache contract — drop the headline write

`references/gather.md` `## Cache contract` item 1 tells the gatherer to write `brief-headline.md` (with the three health lines baked in). Remove that write; the health lines now belong to render-time (Task 2 step 3). Renumber so the JSON becomes item 1.

**Files:**
- Modify: `skills/brief/references/gather.md` (lines ~72–110, `## Cache contract`)

**Interfaces:**
- Consumes: nothing new.
- Produces: the two-file write contract Task 2's render flow references.

- [ ] **Step 1: Replace cache-contract item 1 (the `brief-headline.md` block, lines ~77–98)**

Delete the entire numbered item `1. \`<env_root>/state/brief-headline.md\` — the ≤~1KB **instant preview card** …` through `… a silo masthead here would paint as the cross-domain cockpit).` and replace the `2.` that follows so the JSON is now item 1. Insert this note where item 1 was:

```markdown
> **No headline file.** The header prose (masthead + count chips + narrative + the three
> deterministic health lines) is synthesized at RENDER time from the data below, not written to a
> file — see `SKILL.md` `## Render flow` step 3. `headline_bubbles` (the count chips) lives in the
> JSON payload; the pipeline-health / factory-health / economic-figures lines are lifted verbatim at
> render by `pipeline_health.py` / `brief_render.py factory-health` / `resolve_brief.py header`.
```

Keep the `headline_bubbles` mentions elsewhere in the file (the settle chip at ~line 129, `{N} settled · {M} to confirm`) — those describe JSON content, not the deleted file.

- [ ] **Step 2: Update the section heading + intro line (line ~72–75)**

Change `## Cache contract — the three files (the tail of EVERY full gather)` → `## Cache contract — the two files (the tail of EVERY full gather)`, and in the sentence below change `ends by writing these, smallest first` to `ends by writing these two files, smallest first`.

- [ ] **Step 3: Verify**

Run:
```bash
cd C:/Users/sethh/Documents/Claude/Projects/aios && \
grep -nE "brief-headline|three files|instant preview card" skills/brief/references/gather.md
```
Expected: **no output.** (`headline_bubbles` still present is correct.)

- [ ] **Step 4: Commit**

```bash
cd C:/Users/sethh/Documents/Claude/Projects/aios && git add skills/brief/references/gather.md && \
git commit -q -m "Brief gather: drop brief-headline.md from cache contract (two files now)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Update the cache-writer task, setup skill, install template, and pipeline_health comment

Sweep the remaining live aios references: the scheduled cache-writer task body, the setup skill's vault ritual, the install CLAUDE template's Trigger-determinism rule, and one docstring comment.

**Files:**
- Modify: `deploy/tasks/brief-cache.md` (lines ~45–48, the write list)
- Modify: `skills/setup/SKILL.md` (line ~225, vault ritual)
- Modify: `engine/templates/CLAUDE.template.md` (line ~25, Trigger-determinism rule)
- Modify: `engine/tools/pipeline_health.py` (line ~4, docstring comment)

**Interfaces:**
- Consumes: nothing new. Produces: nothing new. Pure reference cleanup.

- [ ] **Step 1: `deploy/tasks/brief-cache.md` — drop the headline write**

In `# Write the CACHE …`, delete numbered item `1. \`<env_root>/state/brief-headline.md\` — the ≤1KB instant preview card. Its LAST line is the pipeline-health line …` (through its `never compose it from prose.` line) and renumber the `brief-cache.json` write to item 1. Add a one-line note under the `# Write the CACHE` heading:

```markdown
> No headline file is written — the scheduled cache-writer pre-warms `brief-cache.json` only; the
> header prose is synthesized at render time by the brief skill, never precomputed.
```

- [ ] **Step 2: `skills/setup/SKILL.md` — vault ritual reads the cache, not the headline**

Change line ~225 from:
```
    1. Read the brief cache (`<env_root>/state/brief-headline.md` + `brief-cache.json`) for current standing state.
```
to:
```
    1. Read the brief cache (`<env_root>/state/brief-cache.json`) for current standing state.
```

- [ ] **Step 3: `engine/templates/CLAUDE.template.md` — rewrite the Trigger-determinism rule**

Replace line ~25 (`**Trigger determinism (load-bearing).** The ritual phrase's FIRST output is always the headline card read from \`state/brief-headline.md\` …`) with:

```markdown
**Trigger determinism (load-bearing).** The ritual phrase's FIRST output is a static one-line loading ack (`🧭 Gathering your brief…`) — no file read, no precompute, so it can never be stale. Resolve `env_root` cheaply (walk up from cwd to the first dir containing both `state/` and `profile/`); never block on `~/.aios/config.json` or the plugin engine tools. The header prose is then synthesized at render time from the data the brief actually gathers, so it is always current. Enforced by the brief skill's top gate.
```

- [ ] **Step 4: `engine/tools/pipeline_health.py` — fix the docstring comment**

Change line ~4 from `The brief-cache stage lifts this line verbatim into \`state/brief-headline.md\` (deterministic` to `The brief render lifts this line verbatim into the header at render time (deterministic`. (Comment only — no code change.)

- [ ] **Step 5: Verify the whole aios repo is clean of live references**

Run:
```bash
cd C:/Users/sethh/Documents/Claude/Projects/aios && \
grep -rn "brief-headline" . --include=*.py --include=*.md \
  | grep -vE "docs/superpowers/(specs|plans)/2026-07-0[38]|docs/superpowers/(specs|plans)/2026-07-14|\.superpowers/|__pycache__"
```
Expected: **no output** — every remaining hit is dated archive.

- [ ] **Step 6: Run the engine test suite once more**

Run:
```bash
cd C:/Users/sethh/Documents/Claude/Projects/aios && python -m pytest engine/tools/tests/ -q
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd C:/Users/sethh/Documents/Claude/Projects/aios && \
git add deploy/tasks/brief-cache.md skills/setup/SKILL.md engine/templates/CLAUDE.template.md engine/tools/pipeline_health.py && \
git commit -q -m "Brief: purge brief-headline refs from cache-writer, setup, template, docstring

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Update the env-root CLAUDE.md rule + Memory (claude-env repo)

The env-root `CLAUDE.md` carries its own copy of the Trigger-determinism rule (line ~123) that *mandates* echoing the headline file. Rewrite it, record the decision, and update the front-door memory note. **This task is in the `claude-env` repo, not aios.**

**Files:**
- Modify: `C:/Users/sethh/Documents/Claude/CLAUDE.md` (line ~123, Trigger-determinism rule)
- Modify: `C:/Users/sethh/.claude/projects/C--Users-sethh-Documents-Claude/memory/brief-front-door-decision.md` (append) — the auto-memory note
- Append: `C:/Users/sethh/Documents/Claude/Memory/decisions.md` (one dated ADR line)

**Interfaces:** Documentation only.

- [ ] **Step 1: Rewrite the env-root CLAUDE.md Trigger-determinism rule (line ~123)**

Replace the paragraph beginning `**Trigger determinism (load-bearing).** The ritual phrase's FIRST output is always the headline card read from \`state/brief-headline.md\` …` with:

```markdown
**Trigger determinism (load-bearing).** The ritual phrase's FIRST output is a static one-line loading ack (`🧭 Gathering your brief…`) — no file read, no precompute, so it can never be stale. Resolve `env_root` cheaply (walk up from cwd to the first dir containing both `state/` and `profile/`); never block on `~/.aios/config.json` or the plugin engine tools. The header prose (masthead, narrative, Top/First-move, health lines) is synthesized at **render time** from the data the brief actually gathers, so it is always current — the precomputed `state/brief-headline.md` was retired 2026-07-14 because its write-back never fired at-desk and it drifted days stale. Enforced by the brief skill's top gate.
```

- [ ] **Step 2: Append the decision to `Memory/decisions.md`**

Add at the top of the dated entries (most-recent-first convention — verify the file's ordering first and match it):

```markdown
**2026-07-14 — Brief precomputed headline retired; header synthesized live.** The `state/brief-headline.md` preview blurb was echoed verbatim as the trigger's instant first paint, then meant to be rewritten each run — but the at-desk write-back never fired, so it showed a 7am (or days-old) paragraph every run. Rather than fix the fragile write-back, deleted the artifact + the two-pass instant-paint machinery (SKILL `# ⚡ FIRST`, PASS 1/2) + the latent `brief_freshness_check.py` monitor. First output is now a static loading ack; the valued header synthesis moved to render-time, generated from the exact data each brief shows (can't drift). `brief-cache.json` (structured source of truth) untouched. Spec/plan: `Projects/aios/docs/superpowers/{specs,plans}/2026-07-14-brief-header-live-synthesis*`.
```

- [ ] **Step 3: Append to the front-door memory note**

Add to `brief-front-door-decision.md` body:

```markdown

**2026-07-14 update:** the brief's precomputed "preview blurb" (`state/brief-headline.md`) was retired — it drifted days-stale because its write-back didn't fire at-desk. The header prose is now synthesized live at render time from the data each brief shows; first output is a static loading ack. See [[brief-front-door-decision]] · spec `Projects/aios/docs/superpowers/specs/2026-07-14-brief-header-live-synthesis-design.md`.
```

- [ ] **Step 4: Verify env-root is clean of live references**

Run:
```bash
cd C:/Users/sethh/Documents/Claude && \
grep -rn "brief-headline" CLAUDE.md Memory/decisions.md Memory/memory.md 2>/dev/null | grep -v "2026-07-14"
```
Expected: **no output** (the new 2026-07-14 lines mention it descriptively as retired — that's fine; any *older* live mention is a miss).

- [ ] **Step 5: Commit (claude-env repo)**

```bash
cd C:/Users/sethh/Documents/Claude && \
git add CLAUDE.md Memory/decisions.md && \
git commit -q -m "Brief: retire precomputed headline; header synthesized live (env docs)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
(The auto-memory note under `~/.claude/...` is outside the repo — it's written by Step 3 but not committed here; that's expected.)

---

## Final verification (run after all tasks)

- [ ] **Whole-tree grep — no live references remain**

```bash
cd C:/Users/sethh/Documents/Claude && \
grep -rn "brief-headline" Projects/aios/skills Projects/aios/engine Projects/aios/deploy CLAUDE.md \
  --include=*.py --include=*.md | grep -vE "__pycache__"
```
Expected: **no output.**

- [ ] **Live brief run — header is this-run-fresh**

Fire `Wake up. Daddy's home.` from the env root. Expected, in order: (1) the `🧭 Gathering your brief…` static line first; (2) a header paragraph whose `as of` stamp is the current run (not a prior day); (3) no `state/brief-headline.md` created — confirm with `ls state/brief-headline.md` → "No such file". Show the header + the `ls` result in chat.

- [ ] **Engine suite green**

```bash
cd C:/Users/sethh/Documents/Claude/Projects/aios && python -m pytest engine/tools/tests/ -q
```
Expected: PASS.

---

## Spec-coverage self-check

- Spec §Decision (keep prose, fresh only, loading line) → Task 2 step 3 (render-time synthesis) + Task 2 step 1 (loading ack). ✓
- Spec §Design.1 (delete artifact + two-pass) → Task 2 steps 1–2, Task 3, Task 4 step 1. ✓
- Spec §Design.2 (static loading line) → Task 2 step 1; templates Task 4 step 3, Task 5 step 1. ✓
- Spec §Design.3 (render-time synthesis) → Task 2 step 3. ✓
- Spec §Design.4 (keep brief-cache.json) → Global Constraints + untouched by all tasks. ✓
- Spec §"Downstream docs" (SKILL, gather, CLAUDE.md, memory) → Tasks 2, 3, 5. ✓
- Spec §Acceptance (grep clean; loading line then fresh header; no headline write) → Final verification block. ✓
- Beyond the spec's doc-list but forced by the acceptance grep (freshness monitor, setup skill, install template, pipeline_health comment, cache-writer task) → Tasks 1 and 4. ✓
