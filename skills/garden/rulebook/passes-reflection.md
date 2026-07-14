# F8 — Reflection (pass implementation, aios-adapted)

**Reference (the why):** `anthropic-dreams.md` (sibling, verbatim).
**Applies to:** the **curated layer** = wiki content folders (`knowledge/`, `people/`,
`companies/`, `projects/`, `mocs/` + per-KB deltas like Dev's `decisions/`/`entities/`).
The **session layer** (read as evidence, never written by F8) = `raw/sessions/` +
`wiki/journal/`. `raw/archive/` and `staging/` are excluded.
**Garden steps:** F8.1/F8.4 → Step 1 (Connect); F8.2 → Step 2 (De-bloat); F8.3 → Step 3
(Prune); F8.5 → Step 1.
**Tier: SEMANTIC — every F8 finding, every KB.** `lane: review`, `recommended: hold`. F8 never
auto-runs anywhere; on FamilyOffice the kb backstop holds it twice over.

> Adapted from Benos `passes-reflection.md`: role registry stripped (layers above are fixed by
> the kb-schema), walk-only → propose-only (the gate applies), archive destination is our
> `raw/archive/` convention. **Out of scope, unchanged:** CLAUDE.md/SKILL.md, `_schema/`,
> anything still `awaiting` in the queue.

## How this pass works

Synthesis, not scanning: build cross-file clusters, then judge each cluster. Findings emerge
from the cluster. Every finding ships a concrete fix **proposal** whose `rec_reason` explains
the cluster's overlap pattern — never just "these are similar".

**Session window:** last 30 days by filename date prefix (`YYYY-MM-DD`) or mtime.
**Topic clusters:** group files by shared wikilink targets (≥2), shared tags (≥2), basename
token overlap, or repeated entities (≥3 shared proper nouns). Read each member's first ~1500
chars + headings before judging.

## F8.1 — Contradictions

Two curated pages assert opposing facts (numeric disagreement, decision reversal, principle
inversion, date/owner mismatch on the same entity). **Judgment:** real contradiction (both claim
authority, no supersession marker) vs evolution (newer supersedes → route to F8.3) vs scope
difference (drop). **Proposal:** name the presumptive winner (newer authoritative source) and
draft the loser's rewrite — defer-with-wikilink or remove the contradicted line; the HUMAN picks
the winner at the gate. On Dev, log the resolution as a `decisions/` entry.
**Paper-Governs hard rule:** a contradiction touching ownership/economics is NEVER resolved by
recency — the executed document governs; if neither side cites one, say so in `rec_reason` and
leave both `legal_status: verbal`.

## F8.2 — Merge candidates

≥2 curated pages covering substantially the same concept. **Judgment:** merge (≥60% overlap, no
member has a unique sub-topic worth standalone life) vs cross-link (related-but-distinct → F2.4)
vs keep (distinct audiences). **Proposal:** canonical target = best-linked page; fold unique
content in (refactor-not-append, same discipline as Distill); repoint every inbound link;
retired sources go to `raw/archive/<date>-merged/` — never hard-deleted. Merge/delete legs are
`draftless: true` (exact steps in `rec_reason`); the folded-in canonical page gets a staging
draft. Verify zero new dead links in the proposal itself.

## F8.3 — Stale entries

A curated claim superseded by session-layer evidence (decision keywords: `decided`, `pivot`,
`now we`, `changed to`, `replaced`) ≥7 days old and not since reverted. **Judgment:** stale
(rewrite with the new state, cite the superseding source, old wording to a `## History` section
if it carried decision context) vs still-valid (one-off session, drop) vs active disagreement
(→ F8.1). **Step 3 retention rule:** an entry past the profile TTL whose source files are gone
is a retire candidate — never anything still `awaiting` in the queue.

## F8.4 — Emergent themes

≥3 session-window records converge on a topic with no canonical curated entry. **Judgment:**
durable theme (recurring, concrete) vs one-off chatter vs belongs-in-existing (→ F8.5).
**Proposal:** new `knowledge/<theme-slug>.md` (or `mocs/<theme>-moc.md` for index-shaped
themes): 1-line definition, 3–5 key points, wikilinks back to sources, contract frontmatter
(G7.2). Staging draft + `lane: review`.

## F8.5 — Promotions

A specific passage in a session-layer record reads as durable knowledge (markers: `decided`,
`always/never`, `learned`, `key insight`). **Judgment:** durable + generalizable → promote;
only-makes-sense-in-context → leave; already captured → drop. **Proposal:** destination =
`knowledge/` (durable knowledge), the entity's page (facts about a person/company/project), or
Dev's `decisions/` (decisions). Append with a wikilink back to the source record. The source
record itself is `raw/` — IMMUTABLE: never edit it to insert a stub link (that's the F2.9
violation); journal pages may get the stub link.

## Cross-framework constraints (keep F8 from fighting the others)

| Constraint | What F8 does |
|---|---|
| Never edit CLAUDE.md / SKILL.md / `_schema/` | Out of scope entirely; if a fix would touch one, flag-only in the run note |
| Never create dead wikilinks | F8.2 repoints every inbound link in the same proposal; verify before enqueueing |
| Archived files are exempt from orphan checks | `raw/archive/` is outside the audit walk by design |
| New/edited pages meet the frontmatter contract | G7.2 fields at write time — don't create next week's hygiene findings |
| `raw/` immutable | F8.3/F8.5 read it as evidence, never write it |
| FamilyOffice no-elevation | Carry `source_tier`/`confidence` down; never promote a claim to `confirmed` without a primary/secondary citation; `legal_status` never changes outside the gate |
