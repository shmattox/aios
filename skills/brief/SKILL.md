---
name: brief
description: The daily decision-and-action launcher, chat-native — triggered by the profile ritual phrase; surfaces what needs a move today with urgency, playbook opinion, and actions.
---

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

The goal is NOT to tell {{ENTITY_NAME}} what exists. It is a **decision-and-action launcher**:
the few things that need a move today, each carrying (a) how urgent it *really* is, (b) what
their own playbook says, (c) the recommended next action(s), and (d) a door into a focused
working session. The brief is the front door; the judgment happens in the per-item threads.

# Invariants — stated ONCE here; every section below obeys them (don't re-justify inline)

These are enforced by the tools, not by repetition — the prose downstream points here rather than
restating them:

1. **The brief NEVER writes.** It is read-only over the queue/vault/Notion on gather. Every ship or
   pipeline write goes through `gate` (the sole writer: independent review → vault/Notion write +
   `state/revert/{id}.json` → `queue_tx` commit); approval only COMPOSES the command, `gate` runs it.
   The one exception is *tactical* Notion write-back from an action THREAD (not the brief surface),
   under the `notion_writeback.py` fences — see `# Notion write-back`.
2. **Every rendered card/line is engine-emitted and lifted VERBATIM** — `brief_render.py`
   (`station`/`card`/`overview`/`settle`/`render_dossier`), `brief_session.py held-summary`, and the
   resolve `check` line. Never re-word, re-order, re-grade, or drop a line; if you catch yourself
   typing a `🔵`/`🟠`/age line by hand, STOP and call the renderer — that is exactly how the two-layer
   block silently drops.
3. **Read absolutely from the env root, never cwd-relative** (`# Scope` → Access ≠ scope). Scope filters
   OUTPUT only; the vault/Notion/Drive are always reached at their env-root-anchored paths.
4. **`validate_cache` before presenting** — INVALID → don't render; fix the data, re-gather.
5. **Never auto-execute / never auto-ship a review-lane item** — surface the choice; the human picks.

# Trigger + surface — chat-native (Notion is the viewer, the thread is the doer)

This brief lives in the **chat thread**, not a published artifact. Awareness/state lives in
{{ENTITY_NAME}}'s Notion dashboard (the viewer they already have); the brief is where the *acting*
happens. AIOS has two halves — *background/autonomic* (capture→sort→ingest→garden, scheduled,
no UI) and *foreground/deliberate* (this brief + review). Only the foreground is chat-native.

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
   Because this synthesis runs against the data being displayed, its `as of` stamp is always THIS run's — it cannot drift stale the way a precomputed preview file would.
4. **Then the stationed walk** — `brief_session.py status` (scope-match guard, Resume/Start-over),
   then ONE STATION AT A TIME: Stage 0 (Settle), Stage 1 (KB), Stage 2 (domain cards). Preserve the
   designed segmentation (icon H2 headers + counts, `---` rules, `### N · {title}` item headers, the
   `⏱`/`📋`/`⚑` lines, the two-layer blockquote with inline A/B/Other buttons). Recompute the held
   panel fresh (`queue_tx.py select --stage awaiting`).

## Cache contract — the two files (the tail of EVERY full gather)

Whoever gathered — the on-trigger live gather (normal) or the optional scheduled cache-writer —
ends by writing two files, smallest first, atomic (write → re-read → verify parses → retry ×3):
`<env_root>/state/brief-cache.json` (the structured payload — the source of truth, always the full
all-silos superset; `validate_cache` is the completeness exit gate) and
`<env_root>/state/brief-cache.md` (GENERATED from the JSON via `brief_render.py`, never composed by
hand). The JSON carries `headline_bubbles` (the count chips the render-time header lifts) and an
optional `domain_display` map (kb → display-name) that `brief_render.py` consumes. Full write rules:
`references/gather.md` → `## Cache contract`. The header prose is NOT a file — it is synthesized at
render time (`## Render flow` step 3).

## Surface (`profile: brief.surface`) — how it renders AND acts

**Recommendations are ALWAYS actionable inline choices, in every surface** — never dead prose ending in
"→ approve / hold" that {{ENTITY_NAME}} has to retype a command for. Each recommendation is presented
via the host's **"ask the user" option affordance** (`AskUserQuestion` in Claude Code; the equivalent
inline option buttons in Cowork): click **A** (your-system action) / **B** (Claude's action) / **Other**
(type your own workflow). `brief.surface` controls the AWARENESS layout *around* those buttons:

- **`conversational` (default):** a prose narrative of what's going on, with the **inline action
  buttons** on each recommendation. This is the default {{ENTITY_NAME}} actually wants — a chat
  description of the situation *plus* clickable A/B/own actions, NOT a wall of prose and NOT a full
  standalone widget.
- **`widget`:** the full interactive HTML cockpit (Act-vs-Track + the bulk approve/reject panel) via
  the visualize `show_widget` tool (template `skills/brief/templates/review-panel.html`).
- **`hybrid`:** conversational + inline buttons by default; escalate to the widget cockpit for bulk
  held-items approval when the review panel is long. The engine READS the field; never hard-code it.
- **No artifact publish.** The brief never creates/updates a persisted artifact — that medium can't act
  (no `sendPrompt`/clipboard, can't read Notion's computed formula totals, can't be verified).

# Walk ledger — `<env_root>/state/brief-session.json`

The walk is **resumable across sessions**. One ledger file is the source of truth — NOT the chat thread.

## Read / resume on trigger
1. `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/brief_session.py" status <env_root>/state/brief-session.json`
2. If `status:in_progress`: **first check scope match** — compare the ledger's `station_order` to the
   station set the currently-resolved scope expects (`# Scope`). **If they DON'T match, skip Resume entirely:
   `start_over` and build a fresh scoped walk** (a root ledger must never resume as a silo brief, nor vice
   versa). Only if they match, show a **Resume card** (current_station, how many decided/deferred/total per
   station) and ask: **Resume** or **Start over?**
   - **Resume** → read the cache and continue from `current_station`, first undecided item.
   - **Start over** → `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/brief_session.py" start_over <state_path> <env_root>/state/brief-sessions/` then `new_walk` (with the scope's `--order`).
3. If no ledger or `status:complete` → `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/brief_session.py" resume_or_new` to create a fresh walk (carries over open deferrals automatically), seeded with the scope's `--order`.

## Write per action (execute-as-you-go)
Every decision or deferral is written to the ledger **immediately** when taken — not batched to the walk's end:
- **Decision:** `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/brief_session.py" record_decision <state_path> <item_id> <station> <choice> "<action>" [--executed] [--thread T] [--title T]`
  - `choice` = `system | claude | other | defer`
  - Run the action first (act-then-tell); write `--executed` flag. Economic/Paper-Governs content → pause for approval first.
- **Deferral:** `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/brief_session.py" record_deferral <state_path> <item_id> <station> <one-word-reason> <YYYY-MM-DD> [--title T]`
  - Reason is **one word** (e.g. `timing`, `blocked`, `unclear`). Do not accept multi-word reasons.
- **Advance station:** when all items in the current station are decided or deferred, call `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/brief_session.py" advance <state_path>` and continue to the next station.

## Carryover deferrals
`resume_or_new` automatically seeds the new walk's `deferrals` list with `resurface:next-walk` entries from the prior walk. Render each carryover item at the top of its station with the stored one-word reason shown in brackets: `[deferred: timing]`.

# Scope — folder-aware (which silo's brief)

The same trigger phrase renders **either the full cross-domain feed OR a single silo's brief, decided by
where the session is running** — so "Wake up. Daddy's home." in the env root is everything, but the same
phrase inside a project folder is that silo only. **Resolve scope FIRST, before the gather**, and carry it
through the render (masthead, stations, KB filter, delta-check).

**Access ≠ scope (load-bearing).** Scope decides what gets *rendered*; it NEVER restricts what the brief
can *read*. The vault, Notion, and Drive are always reached at their **absolute, env-root-anchored** paths
regardless of cwd — a brief fired inside `Projects/personal` reads the WHOLE `<vault>/` vault and simply
filters the *output* to the personal silo. If a scoped brief ever comes back with no playbook opinion (a bare
task list), the vault read resolved against cwd instead of the env root — that is the failure this guard
prevents. The consolidated vault is a sibling of `Projects/`, not a child of any one project; never assume
it lives under the cwd.

**Resolution (fact-free — read the lever, never hardcode paths):**
1. Read `profile: domains.yaml`. The cwd→silo lever is the EXISTING `session_capture.domain_map`
   (`Projects/<name>` → kb) — reuse it, do not duplicate it.
2. Take the session's working directory. **(a)** If it sits under a `Projects/<name>`, look up
   `domain_map[<name>]` → a kb (`familyoffice | personal | dev`). That kb is the scope. **(b)** If it sits
   under a single-silo KB root (each domain group's own vault folder maps 1:1 to its kb — e.g.
   `<vault>/<kb-folder>` → that kb), that kb is the scope — working inside one KB and firing the
   phrase scopes to that silo.
3. **Otherwise — env root, `<vault>/` root, or any cwd not under a mapped project/KB — scope = `all`**
   (`profile: brief.default_scope`, default `all`). The brief's default is `all`, NOT `domain_map._default`
   (which is `dev` for capture): an unmapped or ambiguous cwd shows EVERYTHING, never silently hides a silo.
4. An explicit override wins over cwd: a trigger suffix ("…, family office only" / "…, just dev") or an
   invocation arg sets the scope directly, bypassing cwd (but not rule 5).
5. **A cache-writer run is exempt — the CACHE is always `all`, overriding rules 2–4.** When gathering to
   write the cache (the live gather's cache-write tail, an explicit "refresh the brief cache", or the
   optional scheduled cache task), gather **every silo**: scope is a RENDER-time filter only; the cache
   must always be the full superset. Precedence top to bottom: **rule 5 (cache-write) > rule 4 (explicit)
   > rule 2/2b (cwd) > rule 3 (default `all`)**.

**What each scope renders** (kb → stations + KB filter). **The scopes and their Stage-2 stations are the
domain groups declared in `profile/domains.yaml`** — the rows below are the reference profile's groups,
shown as the concrete example; a different profile's groups substitute in the same structure. **Every
scope includes the Stage-1 `kb` station** (filtered to the scope's kb) — KB hygiene filing happens in a
silo brief too, never only at root:

| Scope | Stage-1 KB | Stage-2 stations | Headline theme |
|---|---|---|---|
| `all` (root) | all `kb` | System · Personal · Family Office · Dev | cross-domain — the week's gravity well across silos |
| `familyoffice` | `kb == familyoffice` | Family Office | FO only |
| `personal` | `kb == personal` | Personal | Personal/LifeOS only |
| `dev` | `kb == dev` | System · Dev | Dev/env only (GM unifies into `dev` via `domain_map`, so a GM-folder cwd lands here) |

- **System rides with Dev** (and root) — cross-cutting env/engine hygiene; OMITTED from
  `familyoffice`/`personal` scope so those stay purely that silo.
- **Seed the walk to the scoped station set — SETTLE FIRST, then KB, then the scope's Stage-2
  stations.** `brief_session.py … --order` / `--seed` carry exactly these station tokens:
  `settle,kb,familyoffice` (FO), `settle,kb,personal` (Personal), `settle,kb,system,dev` (Dev),
  `settle,kb,system,personal,familyoffice,dev` (root/`all`). Filter the seeded KB count to the
  scope's kb. An empty station renders "nothing needs a move in {silo} today," not a blank walk
  (the settle station's own empty case is "nothing to settle" — see `# Stage 0 — Settle`).
- **Echo the scope in the render-time masthead** — `Family Office · as of {generated_utc}` scoped,
  `All silos · as of …` for root.
- **Dev scope ONLY — the Factory Standup panel.** During the gather for a `dev` (or root `all`)
  walk, ensure `<env_root>/state/factory/standup.json` is current — refresh it by running (read-only)
  `python Scripts/factory-gate/factory_standup.py --root <env_root> --today <today>` — no scheduled job
  refreshes it yet (Seth's switch, same as the dormant `Env factory-gate` task), so the brief regenerates
  it on each Dev-slice render. Then lift
  `render_factory_standup(json.load(...standup.json...))` (`engine/tools/brief_render.py`) VERBATIM
  into the masthead, directly under the factory-health line — same deterministic-render rule as every
  other card (`# Cache contract` → factory-health), never hand-typed. **Dev scope only** — never
  render this panel for `familyoffice`/`personal` scope (System, which it rides with, is already
  omitted there). **Read-only** — it surfaces the four groups (✅ veto-window / ⚠ needs-you /
  ↪ handed-off / ✖ stuck); the brief never writes from this panel — ships and vetoes happen in the
  item threads or via `git revert`, not on the Standup surface.
- **Resume must match scope (else start fresh)** — the `# Walk ledger` scope-match guard; record the
  scope in the walk's `walk_id`/note when starting so the match is explicit.

A full gather caches **all** silos in one pass (`stations[]` keyed by domain, `held[]` tagged by `kb`) —
a scoped brief is a **render-time filter on that one cache**, never a second gather or a slower trigger.
Root and scoped briefs read the same `brief-cache.{md,json}`.

# Stage 0 — Settle (runs before the stationed walk)

The settle station closes the loop opened by a prior walk's Notion write-back: a decision recorded
`executed:true` with an intended `notion_write` (`{page_id, field, to}`) that the deterministic
reconciler (`settle_reconcile.py`, run at cache-precompute time) either already replayed (a KNOWN
unlanded write) or could only infer from evidence (git/Notion) and left for the human. Stage 0
surfaces both, then closes the confirm with the same record-intent pattern so next run's reconciler
can verify it landed.

1. **Auto-heals are already done — just report them.** The precompute already replayed every KNOWN
   unlanded write (`settle.auto_healed[]`); the renderer's `✅ Healed: {title} → {to}` lines are lifted
   verbatim, never re-run.
2. **Render the panel verbatim** — never hand-compose it (deterministic-render rule, same as Stage 2):
   `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/brief_render.py" settle <env_root>/state/brief-cache.json`.
   For a SCOPED brief pass the scope's kb via `--domain <kb>` (e.g. `--domain dev`) so only that
   silo's candidates show — root/`all` passes no `--domain` (all silos, unchanged). `auto_healed`
   rows always show regardless of scope. An empty settle block (no heals, no candidates) prints
   "nothing to settle" — render that line as-is and `advance` straight through to the KB station.
3. **Candidates** (`settle.candidates[]`, each `{task_id, title, proposed_transition, evidence,
   confidence, domain}`) are INFERRED, never executed — they wait for the human's click. Offer, per
   group (the renderer groups by `proposed_transition`): **Confirm all** — plus, per row: **Confirm /
   Adjust / Skip**. **Adjust** takes free text for a different target value; **Skip** is a no-op (the
   candidate regenerates from fresh evidence next gather if still true).
   **Economic content still refuses by type** — a candidate whose target field/value trips the
   `notion_writeback` fences (`pause_economic`, or the number/people/relation type refusal) is surfaced
   instead as a normal Stage-2 card, never confirmed here.
4. **On Confirm (batch or row), two writes IN ORDER — never one without the other:**
   a. Map `proposed_transition` to the Notion value and flip it: `done → Done`, `in_progress →
      In Progress` both flip `--field Status`; `due_rolled` flips `--field Due` instead (the rolled
      date), never `Status`.
      `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/notion_writeback.py" flip --page <task_id> --field
      <Status|Due> --to <mapped-value> --writable <id> [...] --change-log
      "<env_root>/state/notion-changelog.jsonl" --by aios-settle --run-id <YYYY-MM-DD>`
   b. Then record the write-intent so next run's reconciler can verify it landed (the same
      `{page_id, field, to}` shape `settle_reconcile.py` looks for):
      `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/brief_session.py" record_decision <state_path>
      <task_id> settle system "confirm → <mapped-value>" --executed --notion-write
      '{"page_id":"<task_id>","field":"<Status|Due>","to":"<mapped-value>"}'`
   The brief surface confirms; it never auto-flips without this click — same fail-loud boundary as
   every other write-back in this doc (see `# Notion write-back` below).
5. When the settle station is clear (every heal reported, every candidate confirmed/adjusted/skipped),
   call `advance` to move into Stage 1 (KB).

# Stationed walk — TWO STAGES, one station at a time

Station order: **Settle → KB → System → Personal → Family Office → Dev** (from `station_order`),
**filtered to the resolved scope** (see `# Scope` — `all` walks every station; a silo scope walks only
its own, with System riding along only in `dev`/`all`). **Settle is Stage 0** (`# Stage 0 — Settle`
above) — it runs first; an empty settle station renders "nothing to settle" and `advance`s straight
through. What follows is the **two-stage walk** (Stage 1 KB, Stage 2 domain cards) that Settle leads into:

- **Stage 1 — KB (knowledge-base processing):** the held drafts that are wiki *filing*, not decisions —
  `held[]` items with `kb_class:"hygiene"` (faithful daily-note merges + reciprocity fixes; they have a
  real staged draft on disk). Fast: batch-approve the faithful merges, expand any to read the full draft.
- **Stage 2 — the four domain stations (task cards):** `System → Personal → Family Office → Dev`, the
  real decisions. Each domain's items = `stations[domain]` PLUS any `kb_class:"decision"` held draft
  folded onto its matching card (`folds_into`), or as its own card if `folds_into` is `null`.

## Walk tracker (render at top of each station)
```
Settle ◑ 2/5  ·  KB ◑ 4/13  ·  System ○  ·  Personal ○  ·  Family Office ○  ·  Dev ○      ← root/`all` example
```
- ◉ = complete · ◑ = in_progress (show decided/total) · ○ = pending · shows only after the loading ack
- **Scoped walks render only the scope's stations** (see `# Scope`) — e.g. a Family Office brief shows
  `Settle ◑ · KB ◑ · Family Office ○`, a Dev brief `Settle ◑ · KB ◑ · System ○ · Dev ○`. Never show
  stations the scope omits.

## Stage 1 render — the KB station (batch + expand)
The KB station's items are NOT in the cache's `stations` object — pull them from `held[]` where
`kb_class:"hygiene"` (recompute fresh: `queue_tx.py select --stage awaiting`, keep hygiene). Seed the
walk with **the resolved scope's `--order`** (per `# Scope`) plus `--seed kb:{N},...`; **filter the KB
items to the scope's `kb`** (root keeps all). **Back-compat — if a held item has NO `kb_class` (cached
before the classifier ran), classify it on the fly:** `hygiene` if its `conflict_key` is under
`wiki/journal/`, OR its `id` starts with `garden-connect`, OR it is `rec:approve` with a
merge/reciprocity `rec_reason`; otherwise `decision`. Then:

1. Lead line: "**Stage 1 — knowledge-base processing: {N} drafts to file into the wiki.**"
2. Split the rendered list into **Faithful merges** (rec:approve) and **Hold for a look** (rec:hold).
3. Offer one **batch button — "Approve all {k} faithful merges"** — plus, per row, **Expand** (read
   the staged draft body at its absolute vault path — the draft-path resolution in the Phase-A
   `## Gather` below; NEVER cwd-relative) and **Approve / Hold / Reject**. For rec:hold rows, show
   the draft body up-front (these earned a look).
4. **Seeing the item is the point of Stage 1** — never make the owner approve a draft they can't read. If a
   draft file is missing on disk, say so and leave it Hold (don't approve blind).
5. On approve (batch or row): record each under station `kb` (`record_decision … kb …`) and hand the
   ids to `gate` (Invariant 1). On reject: `gate` marks rejected with reason.
   On hold: no-op (returns next walk). When the station is clear, `advance` to Stage 2.

## Stage 2 render — per domain station
Render the station's cards with the **deterministic engine renderer** — never hand-compose the card.
The card format (title + domain tag, urgency/playbook/flags, and the mandatory
`🔵 Your system / 🟠 Claude` two-layer block) lives in `engine/tools/brief_render.py`, so it cannot
drift between renders or surfaces:

1. Run `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/brief_render.py" station <env_root>/state/brief-cache.json <current_station>`
   and **emit its output verbatim** — do NOT re-word, re-order, re-grade, or drop any line. (For a single
   folded / `folds_into:null` decision item, use `... card <cache> <item_id>`.) If you catch yourself
   typing a `🔵`/`🟠` line by hand, STOP and call the renderer — the format is the engine's job, not prose.
2. **Fold in any held decision draft** whose `folds_into` == this card id: show a `📎 Held draft —
   {label}` line with an **Expand** to read its body, and carry **Approve / Reject** for it alongside
   the card's own buttons. A `kb_class:"decision"` held item with `folds_into:null` is rendered as its
   own card in its domain station (via the renderer's `card` op).
3. Offer **per-item buttons** (see below) — the two framed options map 1:1 to the two rendered layers:
   **A ← "Your system says"**, **B ← "Claude adds"**, plus **Other**.
4. On pick: run the action, write the ledger, render confirmation, move to next item.

When all items in the current station are handled, call `advance` and render the next station's header.

## Per-item buttons
```
[🔵 Your system says — <action>]   [🟠 Claude: <action>]   [Not sure → defer]   [Other]
```
- Grade 0 (system silent): show only `[🟠 Claude: <action>]` / `[Not sure → defer]` / `[Other]`.
- "Not sure → defer": prompt for a **one-word reason**, then `record_deferral`.
- "Other": free text → `record_decision` with `choice:other`.

# Graded voice — system (blue) and Claude (orange)

**Claude's outside view (🟠 orange) is ALWAYS present.** System voice (🔵 blue) appears only when earned.

Grade the system voice top-to-bottom, stop at first match:

| Grade | Test | Render |
|---|---|---|
| **1 · direct** | A record (task/page/decision-log/written rule) is ABOUT this exact question | 🔵 solid blue — "Your system says" + citation. |
| **2a · precedent** | No record, but a past decision governs this kind of thing | 🔵 dashed/labeled — "Your system's logic implies — by your decision on {X}…" |
| **2b · principle** | No record/precedent, but a written rule loosely governs | 🔵 faint/hedged — "Loosely, by your {rule}…" |
| **0 · silent** | None of the above | System row OMITTED + "— your system is silent —"; Claude only. |

**Safeguards (load-bearing):**
- **Grade 2 NEVER styled or worded like Grade 1.** "implies/loosely" vs "says"; dashed/faint vs solid. Conflating an extrapolation with a papered fact is the Paper-Governs failure mode.
- **Honesty floor — round down.** Unsure 1↔2 → call it 2. Unsure 2↔0 → call it 0. The system earns its voice.
- Use the `system_voice` field from `brief-cache.json` (graded at gather time + cite); the engine renders, never re-grades.
- **Resolved economic items (A31):** the `system_voice` grade comes from the dossier verdict, not a
  vault-page scan: `papered` → **Grade 1**, text = dossier `canonical`, cite = the Drive file_id;
  `conflict` → **Grade 1, flagged** ("$X per paper, but {source} says $Y — reconcile", from
  `dossier.conflict`); `verbal-only` → **Grade 2b** (verbal, unpapered — never Grade 1);
  `silent` → **Grade 0**, emitted ONLY after the resolve step's search genuinely found nothing.
  The verdict is ADVISORY — it sets the grade/cite so the decision is faster, but every economic
  promotion waits for Seth's approval (resolve-fate decision 2026-07-10; the brief never writes).

The exact chat format of the graded block (Grade 1 solid / 2a precedent / 2b principle / 0 silent, then
the `🟠 Claude` line) is emitted by `brief_render.py` and lifted verbatim (Invariant 2) — it is not
mirrored here, so this doc can't drift from the renderer.

## Resolve section (A31 — after the walk render, before done)
For each dossier file in
`<resolve.cache_dir>` (from `profile/domains.yaml`, default `state/resolve-cache`) matching a
flagged task, lift its card VERBATIM: `brief_render.render_dossier(<dossier>)` — do NOT hand-write
the papered/conflict/verbal-only line (the format is the engine's). Then run the completeness
check and lift its output VERBATIM:
`python "${CLAUDE_PLUGIN_ROOT}/engine/tools/resolve_brief.py" check "<resolve.cache_dir>/sweep.json" "<resolve.cache_dir>"`
— if it prints ANY `⚠ resolve …` OR `ℹ resolve …` line(s), each MUST appear in the brief body verbatim —
never suppress or reword. `⚠ resolve INCOMPLETE …` = a flagged economic task went unresolved; `⚠ resolve
sweep DEGRADED …` = the overnight sweep could not reach the source, so the worklist may be stale even when
it looks complete (A49); `ℹ resolve steady-state …` (A60) = the unresolved worklist has been unchanged for
≥ N sweeps (a known ceiling, not fresh news) — surface this quiet line and do NOT also raise a System-station
card about resolve candidate quality (`references/gather.md`). No line = resolve complete and the sweep is fresh.

## VERIFY step (before reporting success for any walk session)
Run `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/brief_session.py" validate_cache <env_root>/state/brief-cache.json --domains <the profile's domain-group keys, comma-separated>` — the explicit list catches a gather that dropped a whole silo (the bare structural mode can't). Any INVALID output → do NOT present the brief; surface the error and re-gather (fix the data, not the render).

Data gate + format gate = the two-layer block cannot be dropped: `validate_cache` guarantees the data
(every item has `claude_voice.text` + a valid/absent `system_voice`), the renderer guarantees the
format (Invariants 2, 4).

# Gather

The full three-node gather procedure (Notion operational state / vault playbook opinion / Drive
paper — plus the no-Notion-install and degraded-gather rules) lives in **`references/gather.md` →
`# Gather`**. Execute it whenever the render flow needs a live gather or a cache-writer run gathers; it
always covers **every** silo (`# Scope` rule 5) and ends at that file's `## Cache contract`.
Load-bearing reminders: the vault is read absolutely from the env root, never cwd-relative
(Access ≠ scope), and a brief with no vault read has no opinion — it is just a task list.

# Real titles — non-negotiable

Refer to every item by its **actual task/page title, verbatim.** Never a code, ticket id, or
shorthand ("NOTICE-A item", "the cap-table thing"). If something exists only as a vault flag with
NO task in Notion, say so and propose a plain-language title to create — **the missing task is
itself a finding** (often the most urgent items aren't tracked).

# Two-layer recommendations (the built-in board of advisors)

Every recommended action carries two clearly-labeled layers — **inside view** ("Your system says":
grounded in the user's Notion + vault + discipline rules, cited to their own records) and **outside
view** ("Claude adds": expert/best-practice knowledge, explicitly labeled as outside-the-system).
**Never let the outside view be mistaken for the user's papered reality** — that separation is
load-bearing for Paper-Governs trust. The A/B/Other buttons (`## Per-item buttons`) map 1:1 onto
these layers. Whichever is picked **opens that item's working thread** (`state/threads/{id}.md`) and
runs the chosen action there; for held pipeline items the same buttons carry **Approve / Reject /
Hold** → handed to `gate` (Invariant 1: clicking composes the command; gate is the writer). Keep each
item to ≤2 framed options + Other, so the choice is a glance, not a menu.

# Windows are durable threads (`state/threads/`)

"Open a window" is **not ephemeral** — it loads or creates `<env_root>/state/threads/{id}.md` and
CONTINUES it. On open, read an existing thread FIRST and pick up from its `next_action` — never
restart from scratch. While working, append to `## History` (newest last), update `status`
(`open|parked|resolved|reverted`) + `next_action`, link artifacts by path. **Schema (frontmatter):**
`id · item` (the real title) `· conflict_key · domain · opened_utc · status · next_action ·
artifacts[]` — `conflict_key` MUST be the kb-prefixed canonical form (`{kb}/wiki/...`, the queue's
value) so the (↻) open-thread match is exact; a bare `wiki/...` key won't match. In the brief, mark
any item with an OPEN thread (↻) and show its `next_action` — the launcher shows what's already in
motion instead of re-surfacing it cold.

# Render — per item + layout

Card shape (`{TITLE} [domain] · Urgency · Your playbook · Flags · Recommended → two-layer choices`) is
`render_card`'s job (Invariant 2). Layout (default **Act-vs-Track**): *Act* = the top ≈5 items (with a
"view more" for the rest), merged & de-duped across domains, **flags folded onto the item they concern**
(not a separate section), each row actionable — the Act rows are `brief_render.py overview <cache.json>
[limit]`'s job (A11), lifted verbatim (compact header + urgency + the two-layer blockquote). On a LIVE
gather (no fresh cache file), write the gathered `needs_you[]` to a temp JSON and render through the same
op — the renderer is the sole card producer on every path. **`overview` now emits Act items only** —
tasks with a linked open thread stay in Act, reframed to the thread's live `next_action` (the `↻ In
motion` line); tasks whose thread has moved the ball to someone else's court (or is done) are routed to
the **⏳ In-motion** track. Emit that track directly under Act by lifting `brief_render.py in-motion
<cache.json>` verbatim (empty → one clean line — an item there is a *wait*, not a move, so it carries no
A/B buttons). This is what stops worked items re-surfacing cold — the `in_motion` field is written at
gather by `brief_threads.py annotate` (`## Cache contract` in `references/gather.md`). *Track* = quiet
reference below: State-by-domain (a one-line pointer into Notion — never rebuild the dashboard) and the
Phase-A review panel. The legacy content (Needs you · Review panel · Flags · Going quiet · State) is unchanged;
Act-vs-Track is how it's arranged. `conversational` surface renders this as prose; `widget` as the inline cockpit.

# Phase A review panel — the Stage 1 approval surface (held KB drafts)

The brief is the ONE place pipeline items get human approval. `gate` **never** ships a
`review`-lane item — or any `source:self` skill-edit — without {{ENTITY_NAME}}'s explicit command
(`skills/gate`). Those held items surface HERE, after the node gather, so review happens in
one place instead of a separate UI. A multi-day absence just makes this panel longer; nothing
auto-decides a review-lane item while you're away.

**Two-stage split (the panel is Stage 1 only).** Held items carry `kb_class` (classified at gather
time): `hygiene` → THIS panel; `decision` (economic / Paper-Governs / security / IP, or no draft
yet) → a Stage-2 task card (`folds_into` its matching card, or its own) — the split defined in
`# Stationed walk`. **Always let {{ENTITY_NAME}} expand a held draft to read its full body before
approving — never approve a draft they can't see.**

## Gather (read-only over the queue)

Read the queue **through the helper** — `queue_tx.py select --stage awaiting` (it reads the single
canonical `<env_root>/state/queue.json`). **Never hand-parse or hand-edit the queue file**
(Stage Contract #4). From the selected `awaiting`
set, keep the items whose **lane** is `review` (held for approval) or `confirm` (opt-in; awaiting
confirm before its timeout) **AND whose `kb_class` is `hygiene`** (the `decision` ones are Stage-2
cards, not this panel) **AND — when the brief is scoped (`# Scope`) — whose `kb` matches the resolved
scope** (a Family Office brief shows only `kb == familyoffice` hygiene drafts; `all`/root keeps every
kb). "Cross-KB" below means id-based not category-bound — it still honors the scope's kb filter.
**Draft-path resolution (canonical for the whole skill):** read each item's draft at — prefer the
item's own `draft_path` (vault-relative: `<vault>/{draft_path}`), else
`<vault>/<vault.live_kb_map[kb]>/wiki/staging/{slug}.md` (`slug` = `conflict_key` basename);
`vault` + `live_kb_map` from connectors.yaml, resolved absolutely under the env root, NEVER
cwd-relative (`# Scope` → Access ≠ scope); a `kb` not in the map is an error — hold + flag, never a
fallback vault. The draft IS the thing being approved — {{ENTITY_NAME}} can **expand any row to read
the full body** before deciding. Read the `state/context-log.jsonl` tail so already-shipped items
aren't re-surfaced. `conflict_key` is canonical for the target (Stage Contract glossary). Read-only
over the queue (Invariant 1) — approval handoff to `gate` is the only writer.

## Age line — ALWAYS first, engine-rendered (A15)

The panel opens with the review-lane **age line** on EVERY run — residue hides in an unaged
list (the lane once rotted for two months). Run
`python "${CLAUDE_PLUGIN_ROOT}/engine/tools/brief_session.py" held-summary "<env_root>/state/queue.json"`
and echo its `age_line` **verbatim** (deterministic-render rule — never recompute it in prose).
It carries count + oldest age + a nag past 7 days; with zero held items it IS the whole panel
(`Review lane: clear ✓`) — the per-item section below is then omitted, never rendered empty.

## Render — per held item

```
{ACTUAL DRAFT TITLE, verbatim}           [kb · lane]
  Target:        {conflict_key} — where it lands on approval.
  What it says:  one-line distillation of the staged draft.
  Recommended:   {recommended} — {rec_reason}   (the pre-decided ballot from ingest: approve|hold|reject)
  Two-layer →
    Your system says:  grounded in the draft + discipline rules (Paper-Governs / one-home-per-fact).
    Claude adds:       outside-view best practice, explicitly labeled (never mistaken for papered reality).
  Decide →       Approve (ship it) · Hold (leave awaiting) · Reject (drop, with reason)
```

## Batch panel — when the lane is big (A15)

When `held-summary` reports `grouped: true` (> ~20 items), a row-per-item panel is unusable —
render its `groups[]` instead (the 2026-07-03 first-sitting format) so a large lane is still a
handful of decisions:

```
▸ {count}× {kb} → {folder}   (rec: {recommended})   e.g. {sample_slugs, comma-joined}
  Decide → Approve all · Hold · Reject all · Expand (list the rows)
```

Groups are mechanical classes (kb + target folder + ingest ballot) — batch-approve applies the
group's ids to `gate` exactly as individual approvals would; **Expand** drops to the per-item
render above for that group. **The tool groups by LANE only — before rendering, intersect each
group's `ids` with your gathered Stage-1 set (scope + `kb_class: hygiene`):** a group containing
decision-class or out-of-scope ids must have those ids excluded (they stay Stage-2 cards) or be
expanded — batch approval must never touch a draft the two-stage split routed away from this
panel. Never batch across a `rec: hold` group without expanding it.

## Review surface — conversational by default, widget on request (`brief.surface`, as `## Surface`)

- **`conversational` (default):** held items as prose (the block above); {{ENTITY_NAME}} approves/
  holds/rejects in plain language ("approve the two garden hubs") — you map those to the held queue
  `id`s and hand them to `gate`. No HTML; always works.
- **`widget` / on request:** the interactive cockpit via the visualize `show_widget` tool — per-row
  **Approve / Reject** toggles + a footer that composes the exact command and fires it via
  `sendPrompt()`, so the real approval runs as a visible chat message under full discipline. The
  widget **writes nothing** — clicking only types the command (the brief never ships).

Either surface is **cross-KB and id-based** (it hands queue `id`s to `gate`, not "Run
{category} Phase B"), and **never fabricates an item the queue doesn't contain.**

When the surface is `widget` (or {{ENTITY_NAME}} asks for the clickable panel), build it per
**`references/widget.md`** — the boilerplate/template rule (`templates/review-panel.html`, lifted
unchanged), the per-run `phase-a-data` JSON schema (one entry per held unit — never fabricate one
the queue doesn't contain), and the exact id-based commands the Approve/Reject buttons send to `gate`.

## Approve = hand off to gate

Approval is {{ENTITY_NAME}}'s explicit chat command (e.g. "approve {id}" / "ship the supabase page") →
the brief hands the `id` to `gate` (Invariant 1: gate runs the independent review, ships, and commits).
**Reject** → gate marks it `rejected` with the reason. **Hold** → no-op; returns in the next brief.

# Notion write-back (tactical, act-then-tell — §3.6 / G15e)

Distinct from pipeline approval (which goes to `gate` and writes the vault): tactical operational
Notion updates from an action thread run under the `notion.write` contract — **through
`${CLAUDE_PLUGIN_ROOT}/engine/tools/notion_writeback.py` (A7), never ad-hoc MCP writes**: the four
rules (the DB allowlist, the `pause_economic` content gate that overrides it, act-then-tell + the
`notion-changelog.jsonl` receipt, one action = one write = one receipt) are the tool's tested
fences; commands + flags live in **`references/write-back.md`**. The brief *surface* still never
writes; the thread is the doer.

# Discipline

- Obeys the **Stage Contract** (`${CLAUDE_PLUGIN_ROOT}/engine/pipeline/STAGE-CONTRACT.md`) on its read side (fact-free, self-contained). The five Invariants at the top govern; this section adds only what's specific.
- **Chat-native — never publishes an artifact.** Notion is the viewer (don't rebuild its dashboard); the thread is the doer.
- **Tactical Notion write-back is a THREAD action, not the brief surface (Invariant 1; §3.6 / G15e, 2026-06-22).** Action threads may WRITE tactical Notion state — task Status/Priority/Due, decision-log rows, LifeOS signal toggles (the `notion.write.writable` allowlist) — **act-then-tell** (do it, append a `notion-changelog.jsonl` row, tell {{ENTITY_NAME}} in one line). **Economic / ownership / Paper-Governs CONTENT always PAUSES for explicit approval** — the `pause_economic` content gate overrides the allowlist (logging *that* a decision was made is fine; recording its dollar/ownership term is not). KB/vault writes still belong to `gate`. See `# Notion write-back` above.
