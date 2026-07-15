---
name: gate
description: The Phase B review gate — reads the queue, ships the cleared low-risk lane, holds Paper-Governs/economic items for human approval; every ship revertible.
---

**§0 Resolve the install first:** `${CLAUDE_PLUGIN_ROOT}/engine/pipeline/RESOLVE-INSTALL.md` — markers first (walk up for `state/`+`profile/`), config second; only when BOTH fail is the install absent, and even then STOP with guidance, never auto-`/aios:setup`.

You are the **review & ship gate** — the one place a queued item becomes durable wiki truth,
and the place {{ENTITY_NAME}} reviews instead of doing. Contract: `${CLAUDE_PLUGIN_ROOT}/engine/pipeline/QUEUE.md`.
Overview: `${CLAUDE_PLUGIN_ROOT}/engine/pipeline/PIPELINE.md`.

# Run

1. **Load.** Load the queue through the helper (`queue_tx.py select`/
   `load`) — the canonical store is the single file `<env_root>/state/queue.json`; **never
   hand-parse or hand-edit it.** If it fails to validate, fail loud (restore from git or a rewind
   snapshot — fix-then-tell, don't degrade). Read the
   `context-log.jsonl` tail for recent history.
2. **Claim a working set.** Take items at stage `awaiting` whose lease is free; claim each
   (set `claimed_by` + `claimed_at`, one atomic write). **Skip any whose `conflict_key` is already
   claimed** by another worker — serialize on shared targets. Independent items fan out across workers.
3. **Split by lane — via `"${CLAUDE_PLUGIN_ROOT}/engine/tools/lane_policy.py"` (the deterministic decision, defined once as tested
   code).** You supply only the PASS/BLOCK judgment (step 4); the manual gate calls
   `lane_policy.manual_ship_action(item, review_passed, now)` — it maps lane + verdict + clock →
   `ship` / `hold` / `reject` AND applies the `economic_tripwire` as an **advisory floor** (HOLE C):
   an auto-ship/past-TTL-confirm item whose text smells economic/ownership/Paper-Governs is surfaced
   for your EXPLICIT approval (`hold`) instead of being bulk-shipped, even in a cleared KB. Do **not**
   re-derive the rule inline (it drifted once — a `hold` ballot on an auto-ship lane). *(Unattended/
   scheduled run: call `scheduled_ship_action` instead — same rules + the tripwire + a body-presence
   floor; see the Scheduled variant section below.)* It encodes:
   - **`auto-ship`** (low-risk, deterministic, reconstructable) → ship now (step 5); set
     `stage: shipped`, `approved_by: auto-ship`. Surfaced post-hoc, not pre-approved.
   - **`confirm`** *(opt-in)* → `ship` only once past its TTL from `first_drafted_utc`, else `hold`.
   - **`review`** (per `${CLAUDE_PLUGIN_ROOT}/engine/pipeline/QUEUE.md §Lanes` — Paper-Governs / economic / ownership / etc.,
     **and every `source:self` skill-edit**) → **never ships here.** Stage for human approval with the
     two-layer recommendation and the pre-decided ballot (`recommended`). A BLOCK on any lane → `reject`.
4. **Independent review (the gate itself).** Before any ship, a fresh-context check of the draft
   against its source — read the raw at `<vault>/<payload_path>` (vault-relative): does it match?
   does it violate a discipline rule — Paper-Governs (no economic term promoted past `verbal`
   without an executed Drive doc), one-home-per-fact, no quantitative duplication? **If the source
   cannot be resolved/read, that is itself a CRITICAL finding — do not ship.** **Critical findings
   BLOCK** → mark `rejected` with reason; never ship a blocked item.
5. **Ship = one tool call (A25).** All ship MECHANICS live in
   `"${CLAUDE_PLUGIN_ROOT}/engine/tools/ship.py"` — `resolve` prints the mechanical facts for a
   candidate (slug, target, draft found? + legacy staging fallback, `draft_excerpt` for the
   tripwire, journal?); `ship` writes the canonical page (daily-note MERGE guard + pre-merge copy
   included), the revert pointer, and flips the item to `shipped`; `reject` flips with the BLOCK
   reason. Do NOT re-derive paths/slugs/merge logic by hand. The three invocations:
   ```
   python "${CLAUDE_PLUGIN_ROOT}/engine/tools/ship.py" resolve --queue "<env_root>/state/queue.json" \
     --vault-root "<vault>" --kb-map '<the profile's vault.live_kb_map as JSON>' --id <id>
   python "${CLAUDE_PLUGIN_ROOT}/engine/tools/ship.py" ship --queue "<env_root>/state/queue.json" \
     --vault-root "<vault>" --kb-map '<live_kb_map JSON>' --id <id> \
     --approved-by <auto-ship | the approver | auto-ship-scheduled> --revert-dir "<env_root>/state/revert"
   python "${CLAUDE_PLUGIN_ROOT}/engine/tools/ship.py" reject --queue "<env_root>/state/queue.json" \
     --id <id> --reason "<the BLOCK reason>" --decided-by <human if a person rejected | auto for a review BLOCK>
   ```
   `resolve` → JSON facts (`slug`, `target_path`, `draft_found`, `draft_excerpt`, `is_journal`).
   `draft_found:false` → `ship.py reject … --reason "no draft found"`, skip the item; a non-zero
   `resolve` exit (e.g. kb not in the map) = HOLD + flag. A non-zero `ship` exit → the item did NOT
   ship (nothing half-landed) — record the anomaly. Each `ship`/`reject` commits its own queue flip
   atomically (via `queue_tx` under the write lock) — no batch write-back, never a raw queue write.
   **Native git only — never from the sandbox**; the ship records intent and the native auto-sync commits.
   - **Distill retire (post-ship) — the ONLY caller of `retire`.** If the shipped item carries
     `retire_stub` + `provenance` (a garden Distill proposal — its `knowledge/` page just shipped), run
     `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/garden_distill.py" retire <vault> <kb-folder> <stub_slug> <target> <date>`
     — **resolve `<kb-folder>` = `vault.live_kb_map[kb]`** (garden_distill joins `<vault>/<folder>/wiki/`,
     while the item's `retire_stub` carries the short `{kb}/…`, so map it as the gate resolves every
     other vault path) — to relink inbound `[[sources/<slug>]]` → the knowledge target and `move` the husk to
     `raw/archive/wiki-sources-retired-<date>/`. Run it **only now**, after the insight has shipped:
     `retire` refuses (mutates nothing) if the knowledge target is absent (ship-first invariant), is
     atomic (verify-before-move), and never hard-deletes. The husk move is revertible via
     `rewind.py undo-ship` (file-snapshot undo — not git); if your vault is git-tracked, also commit
     the moved husk + relinked files with the ship. **Without this clause the husk never archives — the distill loop stays half-open.**
     Then close the ledger (A28): `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/rewind.py" mark-retired
     <queue.json> --ck <retire_stub> "distill-retired -> knowledge/<target>"` — the stub's prior
     shipped item(s) get `retired: true` so `reconcile` reads the archived husk as a lifecycle exit,
     never a missing ship (zero matches = harmless no-op). Same rule for any human-executed
     `draftless` prune/merge that removes a page: mark the removed page's `conflict_key` retired.
6. **Log + release.** Append a `context-log.jsonl` line (`items_in/out`, `auto_shipped`, `held`,
   `rejected`, `repairs`, `anomalies`, `duration_ms`). Release all leases.

# Scheduled (unattended) variant — `aios-gate-auto`

This skill runs two ways. **Manual (`aios-gate`)** — a human is present, so it processes the whole
working set: a person explicitly approves the `review`-lane and within-TTL `confirm` items and every
`familyoffice`/non-cleared-KB item; `auto-ship` and past-TTL `confirm` items in a cleared KB ship
without pre-approval (surfaced post-hoc) UNLESS the `economic_tripwire` flags them, in which case they
too surface for explicit approval (`manual_ship_action`'s advisory floor, HOLE C). **Scheduled
(`aios-gate-auto`, cron `0 4 * * *`, after ingest)** — NO human is present, so it ships
**only** the `auto-ship` lane ∩ the profile's `gate.auto_ship_kbs` set (`<env_root>/profile/connectors.yaml`;
**default EMPTY — nothing auto-ships until the human opts domains in at setup**) ∩ review-passed slice
and holds everything else for the next manual pass. The code difference: manual calls
**`lane_policy.manual_ship_action`** (tripwire advisory), unattended calls
**`lane_policy.scheduled_ship_action`** (tripwire + a body-presence floor) — **never bare `ship_action`** (step 3).

What actually keeps Paper-Governs material from shipping unattended, in order of strength:
1. **The kb backstop** (primary) — a correctly-labeled `familyoffice` item NEVER auto-ships, full stop.
2. **The fresh-context independent review** (step 4) — still runs before any unattended ship and still
   BLOCKs (`review_passed=False` → `reject`). The schedule does not skip the gate; it removes the human
   *approval*, not the *review*.
3. **The `economic_tripwire`** (best-effort floor) — `scheduled_ship_action` holds an item whose text
   looks economic/ownership/Paper-Governs even if it was **mis-laned** into an opted-in KB. It is a
   recall-biased regex, **not a complete classifier**: it reduces — does not eliminate — mislabel risk;
   content carrying no economic vocabulary is the acknowledged residual (caught only by Sort + the review).

Because the full draft body lives in **staging, not the queue item**, the scheduled skill MUST enrich each
item with a draft excerpt (e.g. set `draft_excerpt`) before calling `scheduled_ship_action`, so the
tripwire sees the body and not just `id`/`rec_reason`/`conflict_key`. **This is now enforced, not just
asked (HOLE A):** `scheduled_ship_action` will NOT ship an item with no scannable body field — a
skipped/failed enrichment deterministically DEFERS the item to the next human pass rather than shipping
past a blind tripwire. All layers are tested in
`${CLAUDE_PLUGIN_ROOT}/engine/tools/tests/test_lane_policy.py`. familyoffice stays human-gated, always.

# Human surface — one review place, not a new UI

Held `review`-lane items render in the **brief's review panel** (the brief is where you
review). Approval is {{ENTITY_NAME}}'s explicit chat command; this skill never executes a review-lane
ship — or a `source:self` skill-edit — without it.

# Guardrails

- Obeys the **Stage Contract** (`${CLAUDE_PLUGIN_ROOT}/engine/pipeline/STAGE-CONTRACT.md`).
- **Never auto-ship a `review`-lane item.** Never ship a self-improvement proposal without approval.
- **One writer per item** (lease); **serialize `conflict_key`s.** This is the clobber fix — not optional.
- **Daily-note merge, never overwrite (clobber fix) — enforced by `ship.py`.** Shipping to an
  existing `{kb}/wiki/journal/<date>.md` **merges**, and always saves a pre-merge copy for revert, so
  no incumbent content is lost either way. Which mechanism it uses is decided by content, not config:
  ingest drafts a merge as the *whole note* re-emitted additively, and `ship.py` writes that draft in
  place (A43) once it covers every incumbent body line; a draft that does **not** cover them is
  instead appended below a delimiter. Ingest lanes a draft whose target already exists as `review`,
  so a human confirms the merge; the tool is the backstop that makes a mis-laned session-record
  harmless.
  **If you edit a staged merge draft before approving, do not touch the incumbent's own lines.**
  Adding lines is always safe. Editing one incumbent line drops the ship off the in-place path onto
  the delimiter path, which appends the whole re-draft below the whole incumbent — a duplicated note
  with two H1s, produced *after* your approval, so you never see it. To correct the incumbent's own
  prose, ship the merge first and edit the note afterwards.
- **Queue mutations commit via `queue_tx.py update`** (targeted — changes only the named items in the single `queue.json`, never prunes; atomic write under the advisory lock). Daily stages never use the bulk `commit` path. Never raw-write the queue file.
- **Undo is git:** every ship is revertible by `id`. A bad auto-ship is `revert {id}`, same as anything.
- Self-modification (`source:self`) is held for approval like a Paper-Governs write — the system
  proposes, it never silently rewrites itself.
