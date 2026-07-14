# F9 — Architecture & Discoverability (pass implementation, aios-adapted subset)

**Reference (the why):** `anthropic-architecture.md` (sibling, verbatim).
**Applies to:** every mapped KB's wiki.
**Garden step:** cross-cutting hygiene (F9.2 mechanical leg) + Step 2 De-bloat (F9.5).

> Adapted from Benos `passes-architecture.md` — the **index/orphan subset** only (design B3).
> Our conventions are FIXED, not discovered: the folder-index is `wiki/index.md` (one per KB;
> `log.md` is append-only history, never navigation), routing is the KB's `CLAUDE.md`, layers
> come from the kb-schema. There is no `Plot.md` and no role registry. **Left behind with the
> shell:** F9.0 (role-gap interviews), F9.1 (routing-table truthfulness — the KB `CLAUDE.md` is
> env-maintenance's beat), F9.4 (semantic path allocation — folder moves are big-blast-radius
> churn our staged pipeline doesn't need), F9.6 (reorg proposals — same), F9.7 (CLAUDE.md
> fitness — explicitly env-maintenance, never the vault garden).

## F9.2 — Index presence and freshness

**Rule (kb-schema maintenance):** `index.md` stays current — an indexed page is minimally
connected; "the index never goes stale."

**Tier: MECHANICAL.** Oracle: `garden_hygiene.py` → `has_index` + `index_missing` (content
pages unreachable from `wiki/index.md`; `journal/` + `sources/` exempt — episodic / transient).

**Drafting the fix (model) — the tier boundary is "single correct output", applied strictly:**
- `index_missing` entries → one proposal per KB (batch): staging draft of `index.md` with the
  missing lines added under their natural sections as the BARE `garden_hygiene.index_line(...)`
  output (`- [[path]]`), verbatim — that alone is deterministic, so that alone rides
  `lane: auto-ship`. A model-authored 1-line description makes the fix judgment → the whole
  proposal drops to `lane: review` (descriptions are nice; ship them gated or not at all).
- `has_index: false` → ONE proposal: create `wiki/index.md` from the page inventory. Creation
  chooses grouping and wording → `lane: review`, always.
- **Removing or reorganizing** existing index content is never mechanical — if the index also
  lists dead pages, that's a separate `review`-lane proposal.

## F9.3 — Discoverability (navigation orphans)

**Rule:** every content page should be reachable root-first: KB `CLAUDE.md` → `wiki/index.md` →
page (≤3 hops via wikilinks).

**Tier: SEMANTIC** (overlap triage is judgment). A wikilink-orphan (`garden_audit.py`) and a
navigation-orphan are distinct concerns — a page can be deep-linked from a sibling yet invisible
from the index, or indexed yet orphaned from content pages. Work the audit's orphan list with
BOTH lenses (the F2.3 route already covers most of it); flag any content page whose only inbound
link is another leaf (connected but not discoverable) for an index or MOC line.

**Proposal:** the index/MOC addition (mechanical leg above) or the F2.3 route; `lane: review`
when it's more than a bare index line.

## F9.5 — Folder-purpose duplication

**Rule:** no two folders should hold substantially the same kind of thing.

**Trigger:** per-KB deltas drifting into base-schema territory — e.g. a KB growing an
`entities/` next to `people/`+`companies/` (Dev's is a *declared* delta — check the KB's
`CLAUDE.md` before flagging), an `insights/` next to `knowledge/` (the legacy split that
already folded), or >30% of two folders' pages covering the same entities.

**Judgment:** true duplication → propose the fold (file-level merges route as F8.2 proposals;
the folder itself empties and is removed by hand at the gate) vs declared delta (KB `CLAUDE.md`
documents it → drop) vs adjacent-but-distinct (tighten each folder's line in `index.md`).

**Proposal:** `draftless: true` with the exact fold plan in `rec_reason`; `lane: review`.
Folder-level moves are the biggest blast radius the garden touches — prefer purpose
clarification over migration when either resolves it.
