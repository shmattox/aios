---
name: ingest
description: Stage 3 (Phase A) — draft wiki entries from sorted items into staging, self-verify, assign lane + recommended outcome, set awaiting; drafting only, never ships.
---

**§0 Resolve the install first:** `${CLAUDE_PLUGIN_ROOT}/engine/pipeline/RESOLVE-INSTALL.md` — markers first (walk up for `state/`+`profile/`), config second; only when BOTH fail is the install absent, and even then STOP with guidance, never auto-`/aios:setup`.

You are Stage 3 — **Phase A drafting** (`${CLAUDE_PLUGIN_ROOT}/engine/pipeline/PIPELINE.md`). Turn `sorted` raw items into
review-ready wiki drafts. You draft and self-verify; you **never write the wiki** — that's
`gate` / Phase B.

# Run (per KB, fenced pass — load that KB's own schema)

**Scheduled draft cap:** up to 25 `sorted` items per run, oldest first — the draft pool is the
items sorted this run + pre-existing `sorted` items; the rest stay `sorted` for the next run.
(A deploy body's §0 constants may set a tighter cap; the body's constant wins.)

1. **Draft.** For each `sorted` item, read the raw at `<vault>/<payload_path>` (vault-relative) and
   draft the wiki entry to `<BASE>/wiki/staging/{slug}.md` (`<BASE>` = `<vault>/<vault.live_kb_map[kb]>`;
   create dirs as needed) using the KB's frontmatter contract (`${CLAUDE_PLUGIN_ROOT}/engine/kb-schema/`):
   `title,type,explored:false,source_tier,legal_status` (**economic/ownership stays
   `legal_status: verbal` until executed paper — Paper-Governs**), `links,last_reconciled`; one-line
   Summary + short Narrative + Open threads. Distill — entity / concept / topic, wikilinks,
   `source_tier`; don't copy the raw wholesale.

   **A56 — classify + draft to depth (source-type items).** Before drafting a `type: source` stub,
   judge its **`distill_class`** and set it in the stub frontmatter (and mirror it into the queue
   item's `rec_reason`):
   - **`reference`** — a link / artifact / bookmark / entity-fact whose value is the *pointer*.
     Draft today's thin stub (one-line Summary + short Narrative + Open threads). Unchanged.
   - **`concept`** — a *transferable operational idea* ("how to operate off this") whose value is the
     *concept*. Draft a **deep** stub — still `type: source`, still under `wiki/sources/` (funnel
     intact) — carrying these H2 sections so the idea survives to synthesis:
     - `## Core idea` — the transferable concept IN FULL (not a one-liner).
     - `## How to apply` — how one would operate off this in this vault's context.
     - `## Proposed target` — the `knowledge/` page this belongs in (an existing slug, else a
       proposed new `knowledge/<slug>`), plus candidate neighbour pages to touch (the fan-out seed).
     - `## Open threads` — what's unresolved.
   When unsure, prefer `reference` (a mis-called concept wastes synthesis budget; the garden's F2.8
   metric will surface systematic under-classing). Binary only — an entity-fact is `reference`.
   **Session-record case:** a `type: session-record` raw (conflict_key `{kb}/wiki/journal/<date>.md`,
   slug=`<date>`) drafts/UPDATEs that day's **daily note** (`type: journal`) — fold its
   Focus/Outcome/Why into the narrative (one entry per session), not an entity/source page.
   **Clobber guard — MERGE, never overwrite:** if that daily note **already exists** (drafted earlier
   this run OR a pre-existing note on disk, i.e. substantive content from another producer), draft the
   addition as a *merge* — re-emit the **whole note**, folding this session's entries into the note's
   own H2 sections (`## What We Built`, `## Key Decisions`, `## Open Threads`, …) where they belong.
   **Lane (A99):** a merge you produced by ADDING lines only — every incumbent line preserved
   byte-identical, i.e. a proven in-place **superset** (the same A43 predicate `ship.py` uses to take
   its safe replace path) — is additive and safe, so lane it **per the KB's normal clearance** (the
   lane the item would get absent the merge), `recommended: approve`
   (`rec_reason: "additive daily-note merge (superset)"`). Set `lane: review` + `recommended: hold`
   (`rec_reason: "daily-note merge alters an incumbent line — confirm"`) ONLY when you could not keep
   every incumbent line byte-identical (you altered one — the append-duplicate hazard); the A86
   ship-side guard also holds that case. When the target is **absent** (the pipeline is the sole
   producer for that date) it stays `auto-ship` as before.
   **The draft you stage IS the note that ships.** `ship.py` writes your draft to the note — the gate
   is a shipper, not an editor, and folds nothing on approval. So never stage a delimiter block, a
   `## Merged sessions — pending gate confirmation` heading, or an HTML comment telling a later reader
   to move the entries into their real sections: nothing executes it, so it ships as permanent prose
   claiming approved content is unapproved (A83). Emit only what a human should read *after* approval.
   **Merge by ADDING lines only.** Every incumbent line must survive byte-identical: never edit,
   reflow, or delete one (adding lines between them is what a fold *is*; reordering whole lines is
   safe — ship compares sets — but any character-level edit is not). This is load-bearing, not style:
   ship replaces the note with your draft only while the draft covers every incumbent line (its A43
   superset check). Edit one character of one incumbent line and it instead **appends your whole
   re-draft onto the whole incumbent** — a duplicated note with two H1s. So if your fold leaves the
   incumbent's own prose stale (a session count, a summary line that no longer covers the new
   entries), **do not rewrite it** — say so in the entry you add, and leave the reconcile to a later
   pass rather than editing the line here. A stale count is cheap; a duplicated note is not.
   The superset check compares **bodies only**, so it does not protect the incumbent's frontmatter —
   and the replace path writes your draft's frontmatter verbatim. Re-emit the incumbent's frontmatter
   too (its `tags`/`aliases`/`links` included), or a minimal one silently drops those fields.
2. **Assign the lane + ballot** on the queue item:
   - `lane`: per **`QUEUE.md §Lanes`** (auto-ship / confirm / review; escalation to review always
     overrides the KB default). Don't re-enumerate the lane rules here — that list is canonical there.
     **Finalize through the profile `review_gate`:** resolve the item's gate from the profile
     (`lane_policy.resolve_review_gate(kb, …)`) and pass the risk-based lane through
     `lane_policy.gate_to_lane(gate, proposed_lane)` — a KB whose `review_gate` is `full` lands on
     `review` regardless; `collapsed` keeps the risk lane. (Deterministic, tested — see `"${CLAUDE_PLUGIN_ROOT}/engine/tools/lane_policy.py"`.)
   - `recommended`: `approve | hold | reject` + a one-line `rec_reason` (the pre-decided ballot).
   - `first_drafted_utc`: set to **now (UTC ISO)** when first drafted; preserve verbatim across
     carry-forward. (The confirm-lane TTL clocks from this stamp — an unset stamp reads as
     infinitely old and would ship a confirm item at the very next unattended gate.)
3. Set `draft_path` = the **vault-relative** staging path you just wrote
   (`<live_kb_map[kb]>/wiki/staging/{slug}.md`), then `stage: awaiting` (the draft is written; it's
   now queued for review). `queue_tx` REFUSES any item entering `awaiting` without a `draft_path` —
   the drafted-before-awaiting invariant: no draft on disk, no advance.
4. **Self-VERIFY + commit.** Re-read each draft; confirm every drafted item has its
   staging file and the touched items validate. Commit ONLY the items you changed — collect every
   touched item into a JSON list at `<env_root>/state/queue.json.changes.json`, then:
   ```
   python "${CLAUDE_PLUGIN_ROOT}/engine/tools/queue_tx.py" update "<env_root>/state/queue.json" "<env_root>/state/queue.json.changes.json"
   ```
   `update` requires every id to exist, changes only the named items in the single `state/queue.json`,
   validates the full set, and lands it atomically under the advisory lock — never a raw whole-file
   write. Non-zero exit → the change-set was REJECTED and the queue is untouched; fail loud (STOP,
   report ⚠) — never leave bad state.

# Discipline

- Obeys the **Stage Contract** (`${CLAUDE_PLUGIN_ROOT}/engine/pipeline/STAGE-CONTRACT.md`).
- Stage-specific: Phase A only — drafting + self-check. **No wiki / Notion writes.** Cadence: overnight (~02:00), after Sort.
