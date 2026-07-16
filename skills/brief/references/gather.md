> Reference for `skills/brief/SKILL.md` — the full three-node gather procedure + the detailed
> cache-write contract. Executed by the core skill's render flow on a live gather (stale/absent/degraded cache) and by
> the optional scheduled cache-writer (`deploy/tasks/brief-cache.md`). Every rule here is normative.

# Gather — ALL THREE NODES (a Notion-only gather is a bug)

This is the happy-path gather the trigger runs when the cache is stale/absent, and the
same procedure the optional scheduled cache-writer executes. It always gathers **every** silo
(the core SKILL's `# Scope` rule 5) and ends at the `## Cache contract` below.

**No-Notion installs:** if `profile/connectors.yaml` has no `notion.*` block, skip every Notion
gather step in this section — the brief renders from the vault + queue + state only (urgency comes
from held/awaiting queue items and staged drafts). This is the normal mode for vault-only installs,
not a degraded error.

**Degraded gathers (a CONFIGURED source unreachable — the headless-precompute case):** if
`connectors.yaml` HAS a `notion.*` block but this run cannot reach Notion via MCP (a headless
`claude -p` precompute has no interactive grant), FIRST try the native API reader —
`engine/tools/notion_gather.py` (token from env/Credential Manager; the cache-writer task body
carries the exact invocation) — a live API read makes the gather full-capability
(`notion_live: true`), not degraded. Only when no API token is configured either, degrade
honestly — do NOT fail and do NOT bury the gap in per-item prose:
(1) carry the newest prior Notion data forward; (2) set the capability manifest honestly —
`notion_live: false`, `notion_carried_from: <date of the gather that data came from>`; (3) stamp
each carried-forward urgency's text with its data date — `(Notion as of {date})` — instead of
"couldn't verify/sync" complaints. The next at-desk trigger reads `notion_live`, treats the cache
as degraded, and refreshes live — a degraded cache is a bridge to the next live gather,
never the final word.

For each domain group in `domains.yaml`, in parallel:

1. **Notion — operational state.** Open tasks with their REAL due dates + status + priority;
   recent Decision/Change Log; the domain's state DB. Urgency comes from here — never infer it
   from a semantic-search snapshot.
2. **Obsidian vault — opinion / playbook.** The relevant `project`/`entity` pages under
   `<vault>/<vault.live_kb_map[kb]>/wiki/` — resolved **absolutely from the env root, NEVER
   cwd-relative** (core SKILL `# Scope` → Access ≠ scope); a scoped run still reads the full vault
   and filters only at render. This is where the OPINION lives: the real clock the A&L doesn't
   show, the "sale leads but nothing's locked," the playbook stance. **A brief with no vault read
   has no opinion — it is just a task list.**
3. **Drive — paper, as needed.** Pull an executed doc only to confirm a Paper-Governs flag.
4. **Resolve — economic FO tasks (A31, warm-cache driven).** The overnight sweep (`aios-resolve-sweep`,
   A34) has already flagged the economic tasks + candidate governing docs. `resolve.cache_dir` comes
   from `profile/domains.yaml` (default `state/resolve-cache`) — never hardcode the path. Do NOT
   re-run the sweep — READ its worklist and resolve each item:
   `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/resolve_brief.py" worklist "<resolve.cache_dir>/sweep.json"`
   → `{worklist: [{task_id, title, candidates[]}]}`. For EACH worklist item, in parallel (one
   `deep_model` sub-agent per task — dispatching-parallel-agents): (a) for each candidate, read the
   source doc and build a typed evidence row `{source, ref, says, value, qty, tier, executed}`
   (`tier` derived from `source`: drive→`paper`, notion→`operational`, trello→`verbal`; `executed`
   true only for an executed Drive doc); (b) SELECT the governing doc for the claim's quantity;
   (c) run `resolve_verdict.py` over the per-claim `evidence[]` — NEVER set the verdict yourself;
   (d) persist the dossier via the tool, so the path is sanitized and the sweep hash stamped —
   `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/resolve_brief.py" write "<resolve.cache_dir>" "<task_id>" <dossier.json> "<sweep content_hash>"`
   (the `content_hash` is the top-level field in `sweep.json`) — never a raw file write. Dossier
   field list: `{task_id, title, claim_qty, verdict, canonical, conflict, provenance, sweep_source}`
   (verdict/canonical/conflict/provenance copied from the `resolve_verdict` result).
   If the entity has no crosswalk or the candidates are empty, fall back to semantic search and, on a
   hit, propose adding the link back to the entity page **via `gate`** (self-heal; the brief never
   writes). **Every worklist item MUST end with a dossier file — the completeness check
   verifies this and the brief fails loud if one is missing.**

   **Steady-state = quiet, don't re-editorialize (A60).** When `resolve_brief.py check` returns a
   `ℹ resolve steady-state …` line (the overnight sweep found the SAME unresolved worklist for
   ≥ `STEADY_STATE_DAYS` runs — a known ceiling, not fresh news), that verbatim line IS the resolve
   surface for the day. Do **NOT** also synthesize a System-station card about resolve candidate
   quality ("Nth day running", "same doc list") — the quiet line already says it. Only raise a
   System card when `check` is LOUD (`⚠ resolve INCOMPLETE`) or `⚠ … DEGRADED` — a genuinely new or
   worsening state. The counter lives in `sweep-status.json` (`candidates_unchanged_days`), written
   by the sweep; the brief only reads the tool's verdict, never re-derives it.

## Cache contract — the two files (the tail of EVERY full gather)

Whoever gathered — the on-trigger live gather (normal) or the optional scheduled cache-writer —
ends by writing these two files, smallest first, atomic (write → re-read → verify parses → retry ×3):

> **No headline file.** The header prose (masthead + count chips + narrative + the three
> deterministic health lines) is synthesized at RENDER time from the data below, not written to a
> file — see `SKILL.md` `## Render flow` step 3. `headline_bubbles` (the count chips) lives in the
> JSON payload; the pipeline-health / factory-health / economic-figures lines are lifted verbatim at
> render by `pipeline_health.py` / `brief_render.py factory-health` / `resolve_brief.py header`.

1. `<env_root>/state/brief-cache.json` — the structured payload + `generated_utc` + the source
   counts the delta check uses. **`source_counts` MUST carry the capability manifest** — the
   machine-readable truth the render flow's parity check reads (never prose): `notion_live` (true ONLY if
   Notion was queried live in THIS gather), `notion_carried_from` (ISO date of the gather the
   Notion data actually comes from; equals this run's date when live), plus the existing
   `notion_sources_gathered`. **Populate it COMPLETELY — it is the source of truth; cards render
   deterministically from it, never from prose.** Every station item needs `title`, `domain`,
   `claude_voice.text`, and a valid `system_voice` (`{grade, text, cite}`; cite required for grades
   1/2a) or `system_voice: null` for Grade 0. The JSON also carries an optional `domain_display`
   map (kb → display-name, from the profile's domain groups), which `brief_render.py` consumes.
   **`headline_bubbles` MUST carry the four primary count chips the render-time header lifts** —
   `{N} need you` · `{N} to review` · `{N} Paper-Governs flags` · `{N} going quiet` — plus, when the
   settle pass ran, the `{N} settled · {M} to confirm` chip (below).
   `validate_cache` is the completeness exit gate. The cache is ALWAYS the full all-silos superset
   (core SKILL `# Scope` rule 5).

   **`settle` block (optional, written by the cache-writer's `# Settle reconcile` pass):**
   ```json
   "settle": {
     "auto_healed": [ /* verbatim from settle_reconcile.py's auto_healed[] — the deterministic
                          replay of an executed decision's Notion write that never landed */ ],
     "candidates":  [ { "task_id": "...", "title": "...",
                        "proposed_transition": "done|in_progress|due_rolled",
                        "evidence": [ {"source": "...", "ref": "...", "quote": "..."} ],
                        "confidence": "high", "domain": "dev" } ]
   }
   ```
   `candidates[]` entries are the model's inferred matches — each REQUIRES `task_id`, `title`, and
   `proposed_transition` (one of `done`/`in_progress`/`due_rolled`); `evidence`/`confidence`/`domain`
   are expected but not schema-enforced. `auto_healed[]` is the deterministic class and is never
   hand-authored. Neither list is ever flipped by the cache-writer itself — `candidates` wait for
   at-desk confirm. `validate_cache` checks `settle` when present: a non-dict `settle`, a malformed
   `candidates` entry, or a `proposed_transition` outside the three values is a validation error.
   The cache-writer also adds a `{N} settled · {M} to confirm` chip to `headline_bubbles`.
   **`in_motion` (thread reconciliation — the tail that stops worked items re-surfacing cold):**
   after populating the cache and BEFORE `validate_cache`, run
   `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/brief_threads.py" annotate "<env_root>/state/brief-cache.json" "<env_root>/state/threads"`.
   It deterministically joins each `act`/station item to its `state/threads/` action thread
   (by the item's OI-id referenced in the thread, by `conflict_key`, or by honoring a `thread_id` the
   gather already set) and writes `in_motion: {thread_id, status, next_action, updated_utc, court}` onto
   it (`court:"you"` for an open thread, else `"others"`). The renderer reads `in_motion` to keep
   open-thread items in Act (reframed to `next_action`) and route waiting/done items to the ⏳ In-motion
   track. **Do NOT hand-populate `in_motion` or `thread_id` in prose** — the tool is the sole populator
   (the old ad-hoc `thread_id` guesswork is exactly what let OI-1000/OI-1027 re-surface cold). The one
   judgment the gather still owns: when an item's cache `id` is synthetic (e.g. `FO-DEMO1`, not an
   `OI-N`) and you recognize it corresponds to an open action thread, set that item's `thread_id` to the
   thread's id so `annotate` can honor it — the deterministic OI-id/conflict_key join cannot see a link
   the ids don't share.
2. `<env_root>/state/brief-cache.md` — the full segmented brief, **GENERATED from the JSON** via
   `brief_render.py station <json> <station>` per station, concatenated under the masthead + walk
   tracker. Never compose the cards by hand.
