> Reference for `skills/brief/SKILL.md` — the render/format CONTRACT sections moved out of the core
> skill 2026-09-02 (A7395, progressive disclosure). Every rule here is normative and unchanged; the core
> skill keeps the loading-ack gate, invariants, walk flow and discipline and points here per step. The
> engine renderers (`brief_render.py`, `brief_session.py`) are the executable form of these sections.

## Scope — what each scope renders (moved verbatim from `# Scope`)

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
- **Dev scope ONLY — gate acceptance metrics (A73).** Run the A73 gate-acceptance metrics
  (deterministic; lift `render` output verbatim):
  ```
  python "${CLAUDE_PLUGIN_ROOT}/engine/tools/gate_metrics.py" report --queue "<env_root>/state/queue.json" \
    --today <today> --out "<env_root>/state/factory/gate-metrics.json"
  python "${CLAUDE_PLUGIN_ROOT}/engine/tools/gate_metrics.py" render --queue "<env_root>/state/queue.json" --today <today>
  ```
  If the tool prints "metrics unavailable", lift that line as-is — never substitute zeros.
  (Capture the output as UTF-8 — the tool emits UTF-8 regardless of locale.)
- **Dev scope ONLY — the Factory Standup panel.** During the gather for a `dev` (or root `all`)
  walk, ensure `<env_root>/state/factory/standup.json` is current — refresh it by running (read-only)
  `python Scripts/factory-gate/factory_standup.py --root <env_root> --today <today>` — no scheduled job
  refreshes it yet (Seth's switch, same as the dormant `Env factory-gate` task), so the brief regenerates
  it on each Dev-slice render. **Then (H90 leg 5) run the veto intent-triage post-processor** —
  `python Scripts/factory-gate/veto_triage.py --root <env_root>` — which annotates each veto item in
  `standup.json` with an intent-match verdict (cached per closing-sha, cheap-tier model call, fail-open),
  so the render surfaces only the flagged/unverified ships and collapses the intent-matched clean ones to
  a count. Skip it silently if it errors (advisory — the untriaged full veto list still renders). Then lift
  `render_factory_standup(json.load(...standup.json...))` (`engine/tools/brief_render.py`) VERBATIM
  into the masthead, directly under the factory-health line — same deterministic-render rule as every
  other card (`# Cache contract` → factory-health), never hand-typed. **Dev scope only** — never
  render this panel for `familyoffice`/`personal` scope (System, which it rides with, is already
  omitted there). **Read-only** — it surfaces the four groups (✅ veto-window / ⚠ needs-you /
  ↪ handed-off / ✖ stuck); the brief never writes from this panel — ships and vetoes happen in the
  item threads or via `git revert`, not on the Standup surface.
- **Resume must match scope (else start fresh)** — the `# Walk ledger` scope-match guard; record the
  scope in the walk's `walk_id`/note when starting so the match is explicit.


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

## Walk tracker (render at top of each station)

```
Settle ◑ 2/5  ·  KB ◑ 4/13  ·  System ○  ·  Personal ○  ·  Family Office ○  ·  Dev ○      ← root/`all` example
```
- ◉ = complete · ◑ = in_progress (show decided/total) · ○ = pending · shows only after the loading ack
- **Scoped walks render only the scope's stations** (see `# Scope`) — e.g. a Family Office brief shows
  `Settle ◑ · KB ◑ · Family Office ○`, a Dev brief `Settle ◑ · KB ◑ · System ○ · Dev ○`. Never show
  stations the scope omits.

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

The exact chat format of the graded block (Grade 1 solid / 2a precedent / 2b principle / 0 silent, then
the `🟠 Claude` line) is emitted by `brief_render.py` and lifted verbatim (Invariant 2) — it is not
mirrored here, so this doc can't drift from the renderer.

## Brainstorm-packet decision cards (A77 — after the walk render, before done)

The GM19 `factory-packet` skill pre-runs a seed's solo-runnable brainstorm legs and freezes the
residual Seth-judgment into a machine-readable `questions:` block; A77 surfaces those here so all
judgment converges on the one front door (2026-07-12). The gather already scanned + validated the
packets and stored the renderable set as `packet_cards` on the cache (`references/gather.md` →
`## Cache contract`). For EACH `packet_cards[]` entry (skip the section entirely when the list is
empty — zero pending packets renders nothing):

1. Present its `questions[]` through the **standard AskUserQuestion affordance** — one card per
   question, `header` as the chip, `question` as the prompt, each `options[]` `{label, description}`
   as a choice, `default` pre-selected. **Render the question set VERBATIM** (deterministic-render
   rule — the model authors nothing here; the packet froze the data, the walk only presents it).
2. On answer, write it straight back into the packet (act-then-tell — a tactical file write in the
   repo the packet lives in, the one write this surface makes):
   `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/brainstorm_packets.py" answer --packet "<packet path>"
   --answers '<JSON map {"q1":"<chosen label>", ...}>'`. The `--answers` value is a single
   JSON-encoded argument — pass it as one shell arg and let JSON carry any quotes/apostrophes in a
   free-text "Other" answer (do NOT string-interpolate a raw label into the shell; a label containing
   a quote would break it — build the JSON with a real encoder). The tool accepts an "Other"
   free-text value verbatim, and re-parses the rewritten packet before saving, so a value it cannot
   represent fails loud instead of corrupting the file. It flips `status: answered` only once every
   question is answered; a partial round leaves `status: awaiting-answers` so the remainder
   re-surfaces next brief.
3. The `answered` packet is NOT drafted into a spec here — **spec authorship stays session-owned and
   human-reviewed** (GM19 spec §Decisions 3); the next session sees `answered` and writes the spec.
4. A malformed packet never reaches this step — it was refused at gather as the `⚠ brainstorm packet
   malformed: …` health line (delta-gated), never rendered as a card.

## Sync proposals (A96 — the "Sync proposes" approval panel, inside the front door)

Scheduled sync tasks (`sync-gmail-gdrive`, later `sync-budget`/`sync-trello`) enqueue actionable
findings as `kind:proposal` queue items — a proposed operational-Notion task that a human approves,
NEVER an autonomous write (`lane_policy` forces a proposal to `hold` regardless of lane/kb). Surface
them here, one more lane into the same front door — no new station, no new surface:

1. Render the panel VERBATIM from `brief_session.py proposal_summary <env_root>/state/queue.json`
   (deterministic — the model authors nothing): its `panel_line` ("Sync proposes: N tasks awaiting
   approval — Approve all · Approve/Adjust/Reject") plus, per `rows[]`, the proposed
   `title` · `priority` · `due` and the `evidence` line, so approval is a glance not an investigation.
   A group over threshold renders as one **batch row** (reuses `held_summary`'s grouped shape, A15).
   Empty → render nothing.
2. **On Approve** (batch or per row), create the task through the fence — act-then-tell, one receipt:
   `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/notion_writeback.py" create-task --db <row.db>
   --writable <the profile's task_status db(s)> --title "<row.title>" --field Priority=<row.priority>
   --field Due=<row.due> --change-log <env_root>/state/notion-changelog.jsonl` — it REFUSES an
   unlisted db (rule 1) and economic content (rule 2), and reads back a receipt (rule 3). Then mark
   the queue item shipped. **Adjust** first edits title/priority/due (free text), then creates.
   Update-type proposals (flip an existing task's field) use the existing `flip` op.
3. **On Reject**, mark the queue item `rejected` with the reason. Its `dedupe_key` stays queryable
   via `brief_session.py proposal_dedupe_history` so the producer never re-proposes it (a rejection
   is remembered, not re-litigated daily).

# Render — per item + layout


Card shape (`{TITLE} [domain] · Urgency · Your playbook · Flags · Recommended → two-layer choices`) is
`render_card`'s job (Invariant 2). Layout (default **Act-vs-Track**): *Act* = the top ≈5 items (with a
"view more" for the rest), merged & de-duped across domains, **flags folded onto the item they concern**
(not a separate section), each row actionable — the Act rows are `brief_render.py overview <cache.json>
[limit]`'s job (A11), lifted verbatim (compact header + urgency + the two-layer blockquote). On a LIVE
gather (no fresh cache file), write the gathered `act[]` to a temp JSON and render through the same
op — the renderer is the sole card producer on every path. **`overview` now emits Act items only** —
tasks with a linked open thread stay in Act, reframed to the thread's live `next_action` (the `↻ In
motion` line); tasks whose thread has moved the ball to someone else's court (or is done) are routed to
the **⏳ In-motion** track. Emit that track directly under Act by lifting `brief_render.py in-motion
<cache.json>` verbatim (empty → one clean line — an item there is a *wait*, not a move, so it carries no
A/B buttons). This is what stops worked items re-surfacing cold — the `in_motion` field is written at
gather by `brief_threads.py annotate` (`## Cache contract` in `references/gather.md`). *Track* = quiet
reference below: State-by-domain (a one-line pointer into Notion — never rebuild the dashboard) and the
Phase-A review panel. The legacy content (Act · Review panel · Flags · Going quiet · State) is unchanged;
Act-vs-Track is how it's arranged. `conversational` surface renders this as prose; `widget` as the inline cockpit.

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
