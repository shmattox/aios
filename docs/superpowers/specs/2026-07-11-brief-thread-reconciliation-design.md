<!-- sanitize:allow-file — worked examples use synthetic/anonymized ids (A79) -->
# Brief thread-reconciliation — design

**Date:** 2026-07-11
**Status:** approved (design), pre-implementation
**Repo:** aios (brief engine)

## Problem

The daily brief re-surfaces tasks **cold** — with their stale Notion narrative — even when
prior-session work is accurately recorded in `state/threads/`. Three live examples from the
2026-07-11 brief:

| Task | Thread (durable, current) | Brief showed |
|---|---|---|
| Acme loan extension (OI-997) | `acme-loan-extension.md`, updated 07-11 — "DocuSigned; awaiting Sam's signature", `status: parked` | "Fully drafted, **awaiting send**, 28d overdue" |
| Metropolis property tax (FO-DEMO1 / OI-1016) | `OI-1027.md` — "2024 tax PAID 7/7 → Done; 2025 half rolled into refi (OI-1016)" | "~$72K, **Not Started**, resolve before 7/22" |
| NOTICE-A Northwind (OI-1000) | `OI-1000.md` — "awaiting mailed letter; next: pull IRS transcript", `status: open` | re-surfaced cold, no history |

### Root cause (verified)

The thread→item reconciliation is a **stub**:

- `brief-cache.json` items already carry a `thread_id` field — but it is **consumed by nothing**
  (`grep thread_id skills/brief engine/tools` → zero readers). The renderer never looks at it.
- The field is populated by **unreliable ad-hoc model judgment** at gather time: OI-997 and OI-1015
  got linked; OI-1000 (thread literally named `OI-1000.md`) and OI-1027 (Metropolis tax) got **no link**,
  despite exact-id matches.
- The gather procedure (`gather.md`) contains **zero** mentions of "thread"; no engine tool reads
  `state/threads/` during a gather.
- `settle_reconcile.py` exists but does **not** read `state/threads/` and produced nothing (today's
  cache: `auto_healed: 0, candidates: 0`).

**The capture layer works** (threads are correct and current). The **read-back layer is missing** —
nothing deterministically joins threads to items, and nothing renders the join.

Not the fix: the walk-ledger resume/"start fresh" behavior is orthogonal — the cold-resurface is a
gather/render defect upstream of the walk, unaffected by how the walk resumes.

## Design

Three parts, minimal blast radius.

### 1. `engine/tools/brief_threads.py` — deterministic populator (`annotate` op)

Reads every `state/threads/*.md` frontmatter: `id`, `item`, `conflict_key`, `domain`, `status`,
`next_action`; `updated_utc` = file mtime (UTC). For each cache item (`needs_you` + every station),
join to a thread by a **deterministic rule, first match wins**:

1. the item's OI-id (e.g. `OI-1000`, from its `id`) matches an OI-id referenced anywhere in the
   thread's `id` / `conflict_key` / `item` / `next_action` (regex `OI-\d+`, case-insensitive), **or**
2. the item's `conflict_key` equals the thread's `conflict_key` (both non-empty).

On match, write an `in_motion` object onto the item:

```json
"in_motion": {
  "thread_id": "acme-loan-extension",
  "status": "parked",
  "next_action": "…",
  "updated_utc": "2026-07-11T08:59:00Z",
  "court": "others"
}
```

`court` = `"you"` when thread `status == "open"`, else `"others"` (`parked`/`resolved`/`reverted`/
anything non-open). The scalar `thread_id` field is also set (back-compat mirror). No match → item
untouched (no `in_motion`). Cache re-written atomically (write temp → re-read/parse → replace).
Multiple items may share a thread (OI-978 + OI-997 both → acme); a thread with no matching item is a
no-op. Operates on any cache-shaped JSON (the live-gather temp file included).

### 2. `engine/tools/brief_render.py` — consumer

- `render_overview(cache, limit)` partitions `needs_you` by `in_motion.court`:
  - `court != "others"` (i.e. `"you"`, or no `in_motion`) → **⚡ Act** list (existing render). When an
    item has an `in_motion` with a `next_action`, its row shows a `↻ {next_action}` reframe line
    **instead of** leading with the stale urgency narrative.
  - `court == "others"` → routed to `render_in_motion`.
- New `render_in_motion(cache)` → **⏳ In motion — waiting on others (not your move)** track: compact
  one-liners `· {title} — {next_action}` (no A/B buttons — nothing to decide). Empty → one clean line
  (`⏳ In motion: nothing waiting`), never an empty panel (matches `render_settle`).
- `render_card` / `render_overview_row` append a `↻ In motion — {next_action}` line whenever the item
  carries `in_motion` (both courts), so station cards reframe too.
- New CLI op `in-motion <cache.json>` → `render_in_motion`; `overview` now emits Act-only.

Reframe precedence: when `in_motion.next_action` is present, it is the authoritative "what's true now"
line; the cached `urgency`/narrative still renders but no longer stands alone as the headline.

### 3. Gather wiring — `skills/brief/references/gather.md` + `skills/brief/SKILL.md`

- Cache-write tail: run `brief_threads.py annotate <cache.json> <env_root>/state/threads` **before**
  `validate_cache`, so `in_motion` is populated deterministically (replacing the ad-hoc `thread_id`
  guesswork).
- Cache contract documents the `in_motion` object (optional per item; written by `annotate`).
- Act render step calls `overview` **and** `in-motion` and lays them out as the two sections.

## Out of scope (YAGNI)

- No changes to `settle_reconcile.py` (adjacent, evidence-based; not this bug).
- No auto-flipping Notion task status (the Acme status is gated on signature; OI-1027 already Done).
- No walk-ledger/resume changes.

## Testing (TDD)

- **`engine/tools/tests/test_brief_threads.py`** (new): exact-OI-id join; multi-OI `next_action`
  (Acme "resolve OI-978/OI-997" → both items link); `conflict_key` join; no-match leaves item clean;
  `court` from status (`open`→you, `parked`/`resolved`→others); atomic re-write preserves other
  fields; a thread with no matching item is a no-op. Fixtures mirror the three real threads.
- **`engine/tools/tests/test_brief_render.py`** (extend): `render_overview` partitions Act vs
  in-motion by court; Act open-thread row carries the `↻` reframe line; `render_in_motion` empty →
  one clean line; `render_in_motion` non-empty → one line per waiting item; `render_card` shows `↻`
  when `in_motion` present, unchanged when absent.

## Verification (before ship)

Run `brief_threads.py annotate` against **today's real cache + threads** and confirm court routing
matches each thread's `status`:
- **Acme (OI-997)** — thread `parked` → ⏳ In-motion (`court: others`), reframed to "cover email
  SENT… awaiting Sam's signature".
- **NOTICE-A (OI-1000)** — thread `open` → Act (`court: you`), reframed to "pull the IRS account
  transcript". Links to its OWN `OI-1000` thread, not the parked `OI-1002` that merely mentions it
  (exact-id ownership).
- **Metropolis tax (OI-1027)** — thread `status: open` (the payment is done but the Meridian-receipt /
  executed-paper close-out is still your move), so it belongs in **Act** reframed to "send Meridian
  receipt; 2025 half into refi (OI-1016)", NOT In-motion. Its cache item id (`FO-DEMO1`) shares no
  key with `OI-1027`, so it links only once the gather sets `thread_id` (the gather-judgment path).

Then the render VERIFY (`validate_cache`) stays green.

**Join ownership (from review):** an `OI-N` thread owns only its own id; a slug-id thread owns every
OI it references. `court` has three buckets — `you` (open) / `others` (parked) / `done`
(resolved/reverted, dropped from Act and acknowledged as cleared). The tool never persists a derived
`thread_id` (only the gather authors it), so a corrected join rule is never overridden by a stale
sticky value.

## §Ecosystem-check

The capability operates entirely on **private internal state** (`state/threads/*.md` — our bespoke
action-thread format — and `state/brief-cache.json` — our cache). It wires up an existing internal
schema field (`thread_id`) to its own consumer; there is no external capability to shop.

**Leg 1 — Anthropic-first (native / anthropics/skills):** N/A. No native Claude Code primitive
reconciles bespoke on-disk action-thread files against a private brief cache.

**Leg 3 — our own skills/tools (the load-bearing leg, executed this session):**
```
$ grep -rn "thread_id" skills/brief engine/tools | grep -v test
(zero readers — thread_id is populated ad-hoc but consumed nowhere)
$ ls engine/tools/ | grep -i settle    → settle_reconcile.py
$ grep -ln "threads" engine/tools/settle_reconcile.py
(settle_reconcile does NOT read state/threads)
$ grep -c "thread" skills/brief/references/gather.md   → 0
```
Result: the reconciliation is half-built in our own engine. Reuse is maximal — the `thread_id`
schema field, `brief_render.py`, the cache contract, and the `render_settle` empty-panel pattern are
all reused; net-new is one small join tool (`brief_threads.py`). `settle_reconcile.py` is adjacent
(evidence→transition) but reads a different source and is deliberately left untouched.

**Leg 2 — public marketplace:** N/A. No published skill operates on this env's private
`state/threads/` + brief-cache contract.

**Leg 4 — full-service platforms:** N/A. Internal state-file reconciliation; no external service applies.

**Verdict:** build the thin join tool; reuse all existing brief machinery. Custom code is minimal and
confined to the differentiator (thread↔item join over our own state).
