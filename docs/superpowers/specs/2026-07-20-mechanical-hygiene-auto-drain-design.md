# Mechanical-hygiene auto-drain (links-only diff guard)

**Date:** 2026-07-20
**Status:** design — approved (Seth, brief session)
**Repo:** aios (engine)

## Problem

The review lane fills with mechanically-resolvable items that carry no human
judgment. On 2026-07-20 the lane held 41: **32 garden-connect hygiene** drafts
(dead `[[wikilink]]` repoints + "see also" de-bloat — verified links-only via a
real diff of `btc-treasury.md`: only `[[x]]`→`[x](archive/…)` changed, every
economic figure byte-identical) and **9 additive session-record daily-note
merges**. Seth: *"if it's capable of being resolved that way, it should be by
default — I only want to deal with the things that are important."*

Two of the classes are already handled or nearly so:
- **Additive-superset daily-note merges** in cleared KBs (gm/personal) — **A99**
  (shipped 0.7.0) de-escalates these to auto-ship. Going forward they stop
  hitting the review lane. No new work.
- **Garden-connect hygiene in a cleared KB** (gm/personal) — should lane
  auto-ship but currently lands on `review`. Small wiring gap.

The real gap: **garden-connect hygiene in the `familyoffice` KB** (21 of the 32
today). It is forced to `review` purely by the Paper-Governs kb-clamp
(`lane_policy`: `kb_cleared = item.kb in auto_ship_kbs`; `familyoffice` is
deliberately excluded so no FO write ever auto-ships). The clamp is correct as a
*default* — but a dead-link repoint changes no economic content, so holding it
for a human is pure friction with no safety value.

## Approach (chosen: links-only diff guard)

Add a **provable, per-item exception** to the FO clamp: a garden-connect hygiene
draft auto-ships in `familyoffice` **iff a deterministic diff shows the change
touches nothing but link syntax**. Economic content *cannot* change on an
auto-shipped hygiene draft — the guard is a structural diff, not a classifier, so
it is strictly stronger than the "hold everything FO" default it narrows.

Rejected alternatives: **(b) trust the operation class** — auto-ship any
garden-connect item regardless of KB, no per-item check; simpler but a bug in the
generator could ship a content change into FO unreviewed. **(c) post-hoc veto
window** — auto-ship FO hygiene then let Seth revert; contradicts Paper-Governs
("never blind-ship FO before human eyes").

## Components

### 1. `links_only_diff(incumbent_text, draft_text) -> bool` (new, engine)

Deterministic, zero-LLM. Returns `True` iff the ONLY differences between the two
markdown bodies are inside link syntax:
- **Mask** every wikilink and inline markdown link on both sides to a single
  sentinel token, reusing the existing regexes (`garden_neighbors._WIKILINK`,
  `garden_audit.LINK`, and an inline-link `[text](target)` pattern; plus
  `_INLINE` to avoid matching inside code spans).
- **Add/remove of a whole list item whose entire content is a single (masked)
  link** counts as links-only (covers the "de-bloat dangling see-also bullet"
  case). Any other added/removed line is NOT links-only.
- **Frontmatter:** compare parsed frontmatter; every value must be unchanged
  EXCEPT link-valued fields. Any changed scalar (a number, a date, a status) →
  not links-only.
- After masking, the remaining text (normalized only for the masked tokens and
  the whole-link-bullet rule) must be **byte-identical**. Otherwise `False`.
- **Fail-safe:** any parse error, any ambiguity → return `False` (hold). The
  function only ever *permits*; it never forces a ship.

### 2. Gate wiring — narrow exception in `lane_policy`

- Recognize the **mechanical-hygiene class**: garden-connect / archive-repoint
  drafts (id/`rec_reason` marker already emitted by the garden connect pass).
- **Cleared KB (gm/personal):** hygiene class → `ship` (extends A99's
  de-escalation to the hygiene class; fixes today's gm/personal connect items).
- **Non-cleared KB (familyoffice):** the kb-backstop still returns `hold` by
  default. Add ONE exception path: `ship` iff `links_only_diff(incumbent, draft)`
  is `True` AND the A85 `_content_refusal` check passes. Else `hold`.
- The exception requires an **existing incumbent** (a diff needs two sides). No
  incumbent (new page) → not hygiene → `hold`. A new FO page is always content.

### 3. Backstops (unchanged, still run)

- **A85 `_content_refusal`** (injection/content-integrity) runs on every ship
  path, including the new exception.
- **kb-backstop remains the default** for every FO item that is not a
  provable-links-only hygiene draft.
- Scheduled (`aios-gate-auto`) uses the same `links_only_diff` gate, so the
  nightly unattended run drains FO hygiene too — but only the provable slice.

## Data flow

```
ingest/garden → draft (staging) + hygiene-class marker
gate ship decision (lane_policy):
  kb in auto_ship_kbs?                → hygiene: ship
  kb == familyoffice (clamped)?
     links_only_diff(incumbent,draft) AND _content_refusal ok?
        → ship (the new exception)
        else → hold (today's behavior)
  no incumbent / non-hygiene          → hold
```

## Testing (TDD)

- links-only FO draft (btc-treasury real case) → `ship`
- FO draft with one changed digit in a table → `hold`
- FO draft adding a prose sentence → `hold`
- FO de-bloat removing a dangling see-also link bullet → `ship`
- new FO page (no incumbent) → `hold`
- changed frontmatter scalar (status/date) → `hold`
- link inside a code span (must not be masked) → treated as content → `hold`
- gm additive merge (A99 path) → unchanged
- A85 injection marker present in a links-only draft → `hold`

## Ecosystem-check

Capability: a **Paper-Governs-safe, deterministic "links-only" diff that gates an
auto-ship decision inside our own pipeline gate.** Not a generic diff — it must
integrate with `lane_policy`'s kb-backstop and A85.

### Leg 1 — Anthropic-first (native Claude Code, anthropics/skills)
```
$ ls ~/.claude/plugins/cache/claude-plugins-official/  # native/official surface
# native Claude Code exposes no pipeline-gate primitive; anthropics/skills has no
# markdown-diff-gated-auto-ship capability. This is internal engine control flow.
result: none
```

### Leg 2 — public marketplace
```
$ npx -y skills find "markdown links only diff"
accesslint/claude-marketplace@diff   (189)  → generic diff skill (a11y-lint context)
xenodium/emacs-skills@file-links     (56)   → emacs file-link handling
result: neither fits — a generic diff and an emacs link tool; neither is a
        Paper-Governs-clamp-aware links-only auto-ship gate. No adopt.
```

### Leg 3 — our own skills / tools (the richest leg)
```
$ grep -n "_draft_supersets\|_content_refusal\|_merge_frontmatter" Projects/aios/engine/tools/ship.py
89:  def _draft_supersets(draft_text, incumbent)        # diff/superset base to reuse
116: def _content_refusal(content, is_journal)          # A85 — already the ship-path guard
181: def _merge_frontmatter_preserve(draft_text, incumbent)
$ grep -n "_WIKILINK\|LINK\|_INLINE" Projects/aios/engine/tools/garden_neighbors.py Projects/aios/engine/tools/garden_audit.py
garden_neighbors.py:26 _INLINE ; :27 _WIKILINK   # reuse for link/code masking
garden_audit.py:36 LINK
result: REUSE — build the thin `links_only_diff` on top of ship.py's diff base +
        the garden link/inline regexes; wire the exception into lane_policy;
        A85 already provides content-integrity. The only new code is the
        masking-comparison + one branch in the kb-backstop.
```

### Leg 4 — full-service platforms
```
# N/A — internal pipeline control flow (a gate ship decision over local markdown).
# No SaaS/MCP replaces "decide whether an FO wiki draft is a safe link-only change."
result: none
```

### Verdict

| Leg | Finding | Verdict |
|---|---|---|
| Anthropic-first | no pipeline-gate primitive | build-because-none |
| Marketplace | generic diff / emacs links only — no fit | build-because-none |
| Own skills/tools | reuse ship.py diff + garden regexes + A85 | build-because-none (thin glue on our own base) |
| Full-service | internal control flow, no SaaS | build-because-none |

**Custom-build justified:** the thin Paper-Governs-safe hygiene exception is the
differentiator the ecosystem lacks; it reuses our own diff/link/content-integrity
code and adds only the masking-comparison + one lane_policy branch.

## Out of scope

- Broadening auto-ship to any non-links content in FO (never).
- New page auto-creation in FO (always holds).
- The gm/personal merge path (A99, already shipped) — unchanged.
