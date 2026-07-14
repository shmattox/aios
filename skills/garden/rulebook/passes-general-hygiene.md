# G7 — General Hygiene (pass implementation, aios-adapted)

**Reference (the why):** practitioner field notes; no single canonical framework.
**Applies to:** every wiki page in every mapped KB.
**Garden step:** cross-cutting hygiene (runs with Step 2 De-bloat).
**Tier:** MECHANICAL — the whole pass is the deterministic tier. Oracle:
`engine/tools/garden_hygiene.py` (`dup_h1` + `frontmatter` findings). The model LIFTS the
oracle's finding list verbatim and drafts the fixes; it never re-derives the list from prose.

> Adapted from Benos `passes-general-hygiene.md`. **Dropped on the way in:** G7.1 em-dashes
> (design B3 — rejected outright) and G7.4 project-README hygiene (repo-shaped; our vault wikis
> have no `Projects/*/README.md` convention — repo docs are env-maintenance's beat, not the
> garden's). Kept: G7.2 + G7.3.

## G7.2 — Frontmatter floor

**Rule (kb-schema contract):** every wiki content page carries a frontmatter block with at least
`type:`. The full recommended set is in `engine/kb-schema/README.md` (title, explored,
source_tier, last_reconciled, links; `legal_status`/`papered_source` on economic pages).

**Oracle:** `garden_hygiene.py` → `frontmatter` findings — `missing: ["frontmatter"]` (no block
at all) or `missing: ["type"]` (block without the key). `journal/` and structural files
(`index.md`/`log.md`/README) are exempt by design; `staging/`/`.templates/` never scanned.

**Drafting the fix (model):** write the page's staging draft = original content with the
frontmatter block added/completed. Infer `type` from the page's folder (`people/` → `people`,
`companies/` → `company`, `knowledge/` → `knowledge`, `sources/` → `source`, `projects/` →
`project`, `mocs/` → `moc`; KB deltas per that KB's conventions). Infer `title` from the H1 or
filename. Do NOT invent `source_tier`/`legal_status` values — omit what can't be read off the
page; **never** add `legal_status` to a page that lacks it (that's a Paper-Governs judgment, not
hygiene).

**Lane:** `auto-ship` (mechanical). The kb backstop + tripwire still hold FO/economic pages.

## G7.3 — H1 duplicating filename

**Rule:** don't open a page with `# Title` that duplicates the filename — Obsidian and the
pipeline already render the filename as the title; the H1 is redundant bytes and drift surface.

**Oracle:** `garden_hygiene.py` → `dup_h1` findings (H1 slug == filename stem; structural files
exempt). The deterministic fix body is `garden_hygiene.strip_dup_h1(text, stem)` — the H1 line
plus one following blank line removed, frontmatter and body untouched.

**Drafting the fix (model):** staging draft = `strip_dup_h1` output, verbatim. No judgment.

**Lane:** `auto-ship` (mechanical).

## Proposal shape (both checks)

One queue item per page (batch all of a page's mechanical fixes into ONE draft — a page gets one
staging draft, not one per finding): `stage: awaiting`, `lane: auto-ship`,
`recommended: approve`, `source: garden`, `conflict_key` = the target page, `draft_path` = the
staging draft, `rec_reason` naming the oracle findings it fixes (e.g. "hygiene: dup-H1 removed +
type: frontmatter added — garden_hygiene mechanical tier").
