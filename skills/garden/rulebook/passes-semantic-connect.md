# F-SC — Semantic Connect (pass implementation)

**Tier: SEMANTIC — every finding, every KB.** `lane: review`, `recommended: hold`.
Never auto-ships on any KB.

## Input
`garden_neighbors.py --json` reports, per KB, each orphan/weakly-linked page's within-KB
nearest-by-meaning candidates: `{kb: {target_rel: [{neighbor, score}, ...]}}`. The tool is a
mechanical ORACLE (a local embedding index) — its list is *candidates*, not decisions. If the tool
prints `SKIP: ...` (embedder unavailable), this pass does not run this cycle; the lexical connect
pass (audit orphans/dead-links + F2/F8) runs unchanged.

## How this pass works
For each `(target -> candidate, score)`, read the target page and the candidate page, then judge:
- **Real relationship?** Propose a wikilink only if the two pages are genuinely related — the score
  is a similarity prior, not proof. A high score between a person page and an unrelated concept that
  merely shares register is a false positive; drop it.
- **Direction + star-topology.** Link the way the KB's architecture wants it: domain entries link
  to their hub/index page, not sideways to each other (the star-topology rule). Usually the edit is
  on the *target* (the under-connected page), pointing at its hub or its true topical neighbour.
- **Within-KB only (v1).** Candidates are already within-KB; never propose a cross-KB link here.
- **Paper-Governs.** A link that would assert an ownership/economic relationship is held for the
  human at the gate; never let a similarity score imply a papered fact.

## Proposal
A `lane: review`, `recommended: hold` connect proposal (the F9.2 / step-6 connect shape): write the
target page's staging draft with the added wikilink(s), `rec_reason` naming the candidate + why the
relationship is real (never just "high similarity"). Verify no new dead links. Record in the run
note: candidates surfaced vs. links proposed — a persistent "many candidates, zero proposals" means
the floor is mis-tuned or this pass is being skipped.

## Cross-framework constraints
| Constraint | What F-SC does |
|---|---|
| Embeddings are an input, never an auto-link | Only `lane: review` proposals; never `auto-ship` |
| Never create dead wikilinks | Verify the proposed link resolves before enqueueing |
| Never edit CLAUDE.md / SKILL.md / `_schema/` | Out of scope; flag-only in the run note |
| FamilyOffice audit-grade | Local embeddings only (enforced by the tool); Paper-Governs holds |
