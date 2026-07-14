# F2 — Karpathy LLM Wiki (pass implementation, aios-adapted)

**Reference (the why):** `karpathy-llm-wiki.md` (sibling, verbatim).
**Applies to:** the wiki content layer of every mapped KB.
**Garden step:** Step 1 (Connect), plus the stub/digestion checks feeding Steps 2 and 4.
**Tier:** SEMANTIC unless a check says otherwise — wikilink *inference* is always a judgment
call → `lane: review`.

> Adapted from Benos `passes-karpathy-wiki.md`. **Deterministic substrate:** F2.2 (dead links)
> and F2.3 (orphans) are computed by `garden_audit.py` — the model works those lists, it never
> re-scans for them; the unique-typo subset of dead links is already handled mechanically by
> `garden_hygiene.py` repoints. **Dropped:** F2.1/F2.6 (schema-doc completeness + routing
> compliance — our schema is *declared* in `engine/kb-schema/README.md` + each KB's `CLAUDE.md`;
> auditing the declaration is env-maintenance's job, and role-discovery was stripped by design).

## How this pass works

Each check pairs a **trigger** (cheap candidate surfacing — usually a tool report) with **agent
judgment** (read context, decide, write case-specific reasoning). Every finding becomes a
proposal with `reasoning` in its `rec_reason`; nothing is applied here.

## F2.2 — Dead wikilinks (working the audit list)

**Trigger:** `garden_audit.py` `dead_links`, MINUS the pairs `garden_hygiene.py` already claimed
as mechanical repoints.

**Judgment per remaining dead link:** read the linking sentence. Choose: (a) repoint to the page
the sentence actually means (read the top candidates briefly — closest stems, shared folder,
shared topic); (b) the target *should exist* — route to F8.4 (emergent theme) if session
evidence supports creating it; (c) remove the link, keep the text. Skip syntax demonstrations
(the audit already strips code fences, but a prose example like "write `[[target]]`" can leak).

**Proposal:** staging draft of the linking page with the fix applied; `lane: review`;
`rec_reason` = why this repoint over the alternatives.

## F2.3 — Orphan pages (working the audit list)

**Trigger:** `garden_audit.py` `orphans` (journal + structural already exempt).

**Judgment per orphan:** read the page. Is it (a) index-worthy — propose the index line +
any natural wikilinks from related pages (the common case; the mechanical `index_missing`
finding may already cover the index leg — don't double-propose); (b) mergeable — no standalone
signal, fold into its natural home (route to F8.2 shape); (c) prunable — stale AND sourceless
(route to F8.3 / Step 3 rules, never anything still `awaiting` in the queue)?

**Proposal:** per the route; `lane: review` for anything beyond the bare index line.

## F2.4 — Missing cross-references

**Trigger:** entity-shaped page names (`people/`, `companies/`, `projects/`, Dev's `entities/`)
appearing as plain text in other pages, outside `[[...]]`, code, or frontmatter.

**Judgment:** read the sentence. Entity reference → link it; word-coincidence or quoted speech
where the name is incidental → skip. Never link headings, frontmatter values, or a page to
itself. Honor the star topology: domain pages link their hub, not every sibling.

**Proposal:** staging draft with the link(s) added, one item per page; `lane: review`.

## F2.5 — Same-role duplicates

**Trigger:** near-duplicate basenames (edit distance ≤ 3) anywhere, plus name clusters inside
`people/`, `companies/`, `knowledge/` (e.g. a person under two spellings, a company under
short + legal name).

**Judgment:** READ BOTH pages. Truly overlapping → consolidate (canonical = the better-linked
name; fold unique content in; repoint inbound links) — this is an F8.2-shaped merge proposal.
Complementary → differentiate (tighten each page's scope line) or cross-link instead.

**Proposal:** merge proposals are `draftless: true` (the gate writes pages, it doesn't
merge/delete — describe the exact merge in `rec_reason`); scope-tightening edits get staging
drafts. `lane: review` always.

## F2.7 — Stub notes

**Trigger:** content pages < 200 bytes after frontmatter, or body is just an H1 / `TODO` /
placeholder. (`explored: false` frontmatter is the declared-stub marker — those are known WIP,
lower priority, not defects.)

**Judgment:** salvageable (the vault has material to fill it — propose the fleshed-out draft) /
redundant (fold into its home — F8.2 shape) / genuinely pending (leave, it's declared).

**Proposal:** per the route; `lane: review`.

## F2.8 — Undigested sources

**Trigger:** `type: source` stubs in `wiki/sources/` older than the profile TTL that Distill
(Step 4) hasn't drained, and `raw/` records with no downstream wiki touch.

**Judgment:** this is Distill's queue, not a separate fix — surface the backlog in the run note
and let Step 4 process them (one stub at a time, merge-completeness, provenance gate). A source
that produced only a summary page with no entity/project fan-out is worth flagging: name the
pages that look like they need the update.

**Proposal:** none directly — feeds Step 4 + the run note. **(A56)** The "one stub at a time" cap is
retired: Step 4 now distills the whole `concept`-class batch nightly via `select_distill_batch`, and
`distill_run_metrics` emits the undigested tripwire (mean fan-out per concept distill; 1.0 = this
failure mode). A `concept` stub is fenced from noise-retire until synthesis has been attempted.

## F2.9 — Raw source modification

**Rule:** `raw/` is immutable canon.

**Trigger:** anything under `raw/` (except `raw/archive/` moves the gate itself made) modified
by a pipeline run — check the context log and, where the host env has git, the recent diff.

**Judgment:** content rewrite vs metadata addition — both violations, different urgency.

**Proposal:** `lane: review`, severity-fail wording in `rec_reason` ("revert; raw is immutable
canon"). Never auto-fix.
