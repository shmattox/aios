---
name: garden
description: Stage 5 — periodic whole-vault pass: connect, de-bloat, prune, distill sources; proposes every content change through the review gate, never silently rewrites.
---

**§0 Resolve the install first:** `${CLAUDE_PLUGIN_ROOT}/engine/pipeline/RESOLVE-INSTALL.md` — markers first (walk up for `state/`+`profile/`), config second; only when BOTH fail is the install absent, and even then STOP with guidance, never auto-`/aios:setup`.

You are Stage 5 — the **gardener** (`${CLAUDE_PLUGIN_ROOT}/engine/pipeline/PIPELINE.md`). Improve the vault's
*connectedness* and *signal* without growing it. A single whole-vault pass — **not** parallel, since
it touches everything.

**The rulebook.** Steps 1–3 are guided by the harvested judgment layer at
`${CLAUDE_PLUGIN_ROOT}/skills/garden/rulebook/` (README = tier map + lane rule). Two tiers,
load-bearing: **mechanical** findings come from the deterministic oracles (`garden_audit.py`,
`garden_hygiene.py`) — lift their lists verbatim, never re-derive them — and enqueue
`lane: auto-ship` (ships unattended only on profile-cleared KBs; the kb backstop + economic
tripwire hold FamilyOffice/economic regardless); **semantic** findings (all wikilink inference,
all F8, stub/folder triage) are your judgment guided by the `passes-*.md` files and enqueue
`lane: review`, every KB, always.

# Run

1. **Connect (audit-first).** Start from the full-inventory audit, not the link graph — a connect
   pass whose frontier is "recent + linked" structurally skips pages with zero inlinks, so orphans
   accumulate monotonically (the 2026-07-05 361-orphan lesson). One read-only tool call:
   ```
   python "${CLAUDE_PLUGIN_ROOT}/engine/tools/garden_audit.py" --vault-root "<vault>" \
     --kb-map '<the profile vault.live_kb_map as one-line JSON>' [--json]
   ```
   It reports, per KB: **orphans** (content pages with zero inbound wikilinks; `journal/` and
   `index.md`/`log.md`/README are exempt by design) and **dead links** (wikilink targets resolving
   to no file; `log.md` rot is exempt). Then the mechanical oracle — one more read-only call:
   ```
   python "${CLAUDE_PLUGIN_ROOT}/engine/tools/garden_hygiene.py" --vault-root "<vault>" \
     --kb-map '<the profile vault.live_kb_map as one-line JSON>' [--json]
   ```
   It reports the single-correct-output tier: **dup-H1** pages (fix = `strip_dup_h1`, verbatim),
   **frontmatter** floor gaps (no block / no `type:`), **index_missing** pages (fix line =
   `index_line(...)`) + `has_index`, and **repoints** (dead links with a unique near-stem target —
   the typo class; these leave the semantic dead-link list). Draft each per
   `rulebook/passes-general-hygiene.md` + `passes-architecture.md` §F9.2 (one staging draft per
   page, batching that page's mechanical fixes) and enqueue `lane: auto-ship`.
   Then the semantic oracle — one more read-only call (fail-soft):
   ```
   python "${CLAUDE_PLUGIN_ROOT}/engine/tools/garden_neighbors.py" --vault-root "<vault>" \
     --kb-map '<the profile vault.live_kb_map as one-line JSON>' \
     --cache-dir "<env_root>/state/garden/embeddings" --json
   ```
   It reports, per KB, each orphan/weakly-linked page's within-KB nearest-by-meaning candidates
   (a local embedding index; nothing leaves the machine). If it prints `SKIP: ...` (embedder
   unavailable), skip this leg — the lexical connect pass below is unaffected. Otherwise work the
   candidates per `rulebook/passes-semantic-connect.md`: for each `(target -> candidate)`, propose
   the wikilink only if the relationship is real (honour star-topology, within-KB, Paper-Governs);
   enqueue `lane: review`. Record candidates-surfaced vs. links-proposed in the run note.
   Then work the REMAINING audit lists semantically per `rulebook/passes-karpathy-wiki.md`
   (F2.2/F2.3 routes) + `passes-reflection.md` (F8.1 contradictions, F8.4 emergent themes, F8.5
   promotions) plus the recent+linked scan: for each orphan, propose the wikilink(s)/index line
   that connect it (or a merge/prune if it has no signal — route via steps 2–3); for each dead
   link, propose the fix on the linking page (restore, re-point, or drop). Also scan for emergent
   themes that deserve their own `knowledge/` page and people/companies that should merge. Draft
   everything as proposals (`lane: review`). Record the audit's orphan + dead-link TOTALS and the
   hygiene finding TOTAL in the run note / context-log line (steady state is ~0 — a growing count
   means this leg is being skipped).
2. **De-bloat** (`rulebook/passes-reflection.md` §F8.2 merges, `passes-karpathy-wiki.md` §F2.5
   duplicates / §F2.7 stubs, `passes-architecture.md` §F9.5 folder duplication). Flag bloat for
   removal: unnecessary spec/scratch files, facts duplicated across pages (one-home-per-fact),
   stub-only pages with no signal — **keep the insight + the links, drop the cruft.**
   **A56 fence:** a `distill_class: concept` stub is NOT "no signal" cruft — run
   `assert_noise_retire_allowed(fm)`; if it raises, route the stub to step 4 synthesis, never propose
   it for merge/prune as noise.
   All semantic: `lane: review`.
3. **Prune stale** (`rulebook/passes-reflection.md` §F8.3). Retention: entries past the profile's
   TTL whose source files are gone — never anything still `awaiting` in the queue.
   Also never noise-prune a `distill_class: concept` stub that fails `assert_noise_retire_allowed` —
   it goes to step 4 synthesis first (A56).
   Semantic: `lane: review`.
4. **Distill → retire (drain `sources/`).** For each candidate source stub — `python
   "${CLAUDE_PLUGIN_ROOT}/engine/tools/garden_distill.py" enumerate <vault> <kb-folder>` (lists each
   stub + its provenance disposition). **`<kb-folder>` = the KB's vault folder = `vault.live_kb_map[kb]`**
   (garden_distill joins `<vault>/<folder>/wiki/`; resolve it as the pipeline resolves every vault path):
   - **Select the batch (A56 leg 3).** Enumerate all `sorted`/present stubs, then call
     `select_distill_batch(stubs, cap_k)` (`cap_k` = the profile's `garden.distill_concept_cap`,
     default 8): distill EVERY returned `concept_batch` stub this run in **synthesis mode** (below);
     leave `concept_overflow` for the next night (oldest-first, never starved by references); handle
     `reference_stubs` on the **cheap path** — the existing shallow fold, or straight retire if the
     stub has no signal. This replaces the old "one stub/night" cap.
   - **Synthesis mode (concept stubs — Karpathy fan-out).** Do NOT bullet-transfer. Read the stub's
     `## Proposed target` page AND its linked neighbours, then INTEGRATE the operational concept into
     the KB's existing conceptual structure — refine the target page's prose, add a subsection, and
     add the cross-links that connect it. A genuine concept distill typically **touches more than one
     page** (the anti-single-summary rule); emit one staging draft per touched page under the same
     distill batch, each a `lane: review` proposal. If synthesis finds NOTHING durable, do not
     propose — instead set `distill_attempted: no-durable-concept` in the stub's frontmatter (a
     `lane: review` staging edit of the stub) so the fence (below) lets it later retire, and note it
     in the run note.
   - Pick the target `knowledge/` page (existing page the insight belongs to, else a new
     `knowledge/<slug>.md`). Fold the stub's durable points **INTO** the page prose —
     **refactor-not-append**, never a dated "Update:" trail. Cite the raw in the page `## Sources`
     (the resolved `raw_path`, or — if provenance is `archive_as_new_raw` — the stub's archived path).
   - **Merge-completeness self-check:** every durable point from the stub's checklist (every `## H2`
     heading + all `- bullets` at any nesting depth) must land somewhere in the new page. A miss → do
     NOT propose; flag it in the run note.
   - **FamilyOffice no-elevation:** carry `source_tier`/`confidence` down; never promote a claim to
     `confirmed` without a primary/secondary citation. (The gate's fresh-context audit leg is the
     backstop, but honor it here.)
   - Write the distilled page to `{kb}/wiki/staging/<target>.md`, then enqueue the proposal via
     `garden_distill.build_proposal(...)` + `"${CLAUDE_PLUGIN_ROOT}/engine/tools/queue_tx.py" add`
     (`lane: review`, `conflict_key = {kb}/wiki/knowledge/<target>.md`, carrying `retire_stub` + `provenance`).
   - **Retire is the gate's job, not this step's.** The husk is archived ONLY after the gate approves
     and Phase B ships the knowledge page — the gate then runs
     `"${CLAUDE_PLUGIN_ROOT}/engine/tools/garden_distill.py" retire <vault> <kb-folder> <slug> <target> <date>`
     (same `<kb-folder>` = `vault.live_kb_map[kb]` resolution). Never archive a stub whose knowledge has not shipped.
   - **Fence before any noise-retire.** Before proposing ANY stub for retire-as-noise in steps 2-3
     (de-bloat / prune), call `assert_noise_retire_allowed(stub.fm)` — it raises on a
     `distill_class: concept` stub that lacks the `distill_attempted: no-durable-concept` marker.
     That's the only exemption the function checks; a shipped or proposed distill doesn't need a
     separate check here because it already takes the stub out of noise-retire's reach — a shipped
     distill has archived the husk via `retire()` (gone from `sources/`), and a proposed one leaves
     the stub `awaiting`, which steps 2-3 already exclude. A raised fence means: route that stub to
     synthesis mode instead of retiring it. Reference/legacy stubs are unaffected.
5. **Sweep operational residue (teardown — mechanical, AUTO, not gated).** The atomic-write
   protection leaves litter; this is its teardown counterpart — one tool call:
   ```
   python "${CLAUDE_PLUGIN_ROOT}/engine/tools/garden_sweep.py" "<env_root>" --apply --vault-root "<vault>" \
     --kb-map '<the profile vault.live_kb_map as one-line JSON>' \
     --evidence-dir "<the profile's session_capture.evidence_dir>" --evidence-ttl-days 7
   ```
   It deletes ONLY deterministic litter:
   - stale `*.tmp`/`*.proposed` state leftovers (and any legacy `*.last-good` backups) older than the
     retention TTL (default 7d) under `<env_root>/state`;
   - **orphan `staging/` drafts in the REAL vault** (A19 — `--vault-root` + `--kb-map` resolve
     `<vault>/<vault.live_kb_map[kb]>/wiki/staging/`) — an orphan = a draft whose queue item is
     `shipped`/`rejected`/`reverted`/absent (its reason to exist is gone); an unmapped folder is
     skipped, never orphaned;
   - **synthesized session evidence past TTL (G16c)** — `sess-`/`intents-`/`activity-` files the
     `session-capture` stage has already mined into a `raw/sessions/` record (`synthesized: true`).
     Un-synthesized evidence is NEVER swept (live work, same rule as `sorted`/`awaiting` drafts).
   It KEEPS every `sorted`/`awaiting` draft. Capture its report — record the swept counts in the run
   note. These are deterministic residue, NOT knowledge — **sweep them and report in the run note
   (fix-then-tell); do NOT route them through the gate.** (Distinct from wiki/skill content below.)
6. **Propose, don't apply (CONTENT only).** Every wiki edit/merge/delete and skill self-edit is a
   queue item routed through `gate` for approval — it rides the human gate like any Phase B write.
   Self-improvement uses the same path (`source: self`). Enqueue mechanics: collect the proposals
   into a JSON list at `<env_root>/state/garden-proposals.json`, then
   `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/queue_tx.py" add "<env_root>/state/queue.json" "<env_root>/state/garden-proposals.json"`
   (`add` is dedupe-fenced — a re-proposed id is refused — and lands the single queue file
   atomically), so each proposal surfaces in the brief's review panel and the gate can promote it on
   approval. Set on each item: `stage: awaiting`, `lane` per the TIER (`auto-ship` ONLY for
   step 1's mechanical-oracle fixes — dup-H1 / frontmatter floor / index refresh / unique-typo
   repoints; `review` for everything semantic — the rulebook README is the boundary), `kb` =
   target KB (matching the `conflict_key` prefix), `conflict_key` = the target page,
   `source: garden`, `recommended: approve` + one-line `rec_reason` (for a mechanical item, name
   the oracle findings it fixes), `first_drafted_utc: <now>`.
   - **Connect / create-or-update** proposals: WRITE the proposed page content to
     `<BASE>/wiki/staging/{slug}.md` (BASE by target kb; `slug` = `conflict_key` basename **with any
     trailing `.md` stripped** — the path is `{slug}.md`, never `{slug}.md.md`) — the draft the gate
     ships on approval. Record it on the item as `draft_path` (**vault-relative**,
     `<live_kb_map[kb]>/wiki/staging/{slug}.md`) — `queue_tx` refuses an awaiting item without it.
   - **De-bloat (merge) / prune (delete)** proposals: no auto-writable draft (the gate writes pages,
     it doesn't delete/merge) — set `draftless: true` on the item (the declared no-draft exception to
     the drafted-before-awaiting guard) and describe the exact action in `rec_reason` (which pages
     merge into which, or which page to delete + why); still enqueue as `awaiting`/`review` so they
     surface (human-executed on approval).
   These are PROPOSALS — never write any live (non-staging) wiki page.
7. **VERIFY.** Proposals reference real files; no proposal deletes a page with inbound links unless
   those links are re-homed first. Append a context-log line (incl. what residue was swept).
   Also append the **A56 depth tripwire** to the run note / context-log via
   `distill_run_metrics(concept_in, knowledge_pages_touched, reference_retired, fanout_counts)` —
   `fanout_counts` = pages touched per concept distill (a mean of 1.0, or zero knowledge growth
   against `concept_in > 0`, is the depth-insufficient signal = Karpathy F2.8 "undigested source").

# Discipline

- Obeys the **Stage Contract** (`${CLAUDE_PLUGIN_ROOT}/engine/pipeline/STAGE-CONTRACT.md`).
- Stage-specific: cadence install-configured (recurring whole-vault pass — set per profile/schedule, not baked here); single-pass; content changes propose → `gate` → ship (git, revertible). **Never auto-delete or silently rewrite a skill or wiki page** (those are gated). Operational residue (`.tmp` leftovers, legacy `.last-good` backups, orphan staging) IS swept automatically — mechanical teardown, not content.
- The `auto-ship` lane is NOT a bypass: mechanical-tier items still ride the queue and ship through the gate's normal run — the lane only clears them for unattended promotion on profile-cleared KBs (`gate.auto_ship_kbs`); the kb backstop + economic tripwire in `lane_policy` hold FamilyOffice/economic content regardless of lane, and every ship stays revertible. F8 (and every other semantic pass) never rides `auto-ship` on ANY KB.
