# Capture worthiness floor — stop over-drafting; route low-signal captures to searchable raw

**Date:** 2026-07-18
**Status:** design-approved (Seth, 2026-07-18 seed-walk brainstorm)
**Backlog:** aios **A89**
**Pairs with:** A99 (A99 stops over-*escalating* what's drafted; A89 stops over-*drafting* in the first place)

One-line: ingest drafts a full KB page from nearly every sorted item, so trivia accrues on the review
lane. Add a **zero-LLM deterministic worthiness floor at Sort**: below-bar, non-Paper-Governs captures
route to a terminal `reference` disposition — they stay in searchable `raw/`, never drafted, never gated.

---

## 1. Problem

Ingest drafts nearly every sorted economic/social item, so low-signal trivia becomes a full `review`-lane
draft the human must walk (illustrative: a sub-$N vendor charge and a failed micro-charge each became
standalone KB pages; a single tertiary social bookmark auto-drafts a Personal page). The felt "flood" was
mostly a one-time KB-priming hump (steady-state review inflow is ~1/day, verified 2026-07-14), so this is
a **proportionate trim, not urgent** — but it shrinks the gate queue at the source and is validated by
external evidence:

- **Cerebras "bursting" (findings 2026-07-18):** content earns extra distillation/indexing ONLY by
  clearing a cheap deterministic bar (rare-token IDF ≥ 4.0, ≥ 200 chars, or social-proof). Everything
  below the bar stays full-text-searchable as raw — "drop" is a routing to a cheaper tier, not a loss.
- **Raw-as-searchable-tier (enacted 2026-07-18):** our `raw/` is already grep-able markdown, so a
  floored item is retrievable on demand with zero build.

---

## 2. Design

### 2.1 The floor (Sort stage, zero-LLM deterministic)

Sort is where disposition is decided (before ingest drafts). Add a deterministic scoring leg. For a
captured item, compute cheap signals — no model call:

- **length** (char count of the body);
- **`source_tier`** (frontmatter — `primary|secondary|tertiary`, already in the schema);
- **`$`-magnitude** (regex over the body for economic items — the largest currency amount);
- **entity/paper-linkage** (does it name a known entity / carry a `papered_source`).

**Below-bar** iff: `length < LEN_FLOOR` **AND** `source_tier == tertiary` **AND**
(`not economic` **OR** `$ < DOLLAR_FLOOR`) **AND** not entity/paper-linked.

A below-bar item is routed to a terminal **`reference`** disposition: Sort sets `stage: reference`
(terminal) instead of `sorted`, so ingest never drafts it. It stays in `raw/` — searchable on demand,
never queued, never gated.

Above-bar → normal `sorted` → ingest drafts exactly as today. **The default posture is permissive**
(floor little): retaining a false-positive draft is cheaper than dropping a genuine one — the same
principle the marketplace `relevance-coarse-filter` states (§Ecosystem-check).

### 2.2 Fixed invariant — never floor Paper-Governs

The floor **only ever touches items that are BOTH below-bar AND not Paper-Governs**. Any of these makes
an item unfloorable, always drafted + held:
- Sort's economic flag fires (reuses the A99 econ-detection path);
- `$ ≥ DOLLAR_FLOOR`;
- the item names a known entity or carries a `papered_source` link.

So a trivial sub-$N charge floors, but a deal paper / term sheet / lending event never does. This is the
same safety spine as A99: the floor is subordinate to the Paper-Governs backstop.

### 2.3 Thresholds are profile knobs

`LEN_FLOOR` (default ~200 chars, from Cerebras's ≥200) and `DOLLAR_FLOOR` (conservative default; Seth
tunes after seeing what it floors) live in `profile` — fact-free engine, instance sets values. Ship
conservative and widen with evidence. An unset/zero threshold disables the floor (safe default: floor
nothing until a value is set).

### 2.4 No silent caps — the floor is visible

A floored item is a *dropped draft*; silent dropping reads as "covered everything." So:
- Sort **logs** each floored item (`context-log` line: id, signals, the failing bar);
- a **pipeline-health count** surfaces "N captures floored to raw today" in the standup/brief health
  lines (no new surface — H51), so Seth can see the floor working and catch over-flooring.

### 2.5 Terminal-state contract

`reference` is a **terminal lifecycle exit** (like `retired`/`rejected`): reconcile and `gate_metrics`
treat it as a valid end state, not a missing draft. A `reference` item can be re-opened
(`rewind` to `captured`) if it later proves worth drafting — the raw is untouched, so nothing is lost.

### 2.6 Out of scope (v2)

Corpus-IDF rarity (rare-token IDF ≥ threshold) is the Cerebras signal we don't build now — it needs a
maintained corpus IDF index. Noted as a v2 enhancement; the deterministic length/tier/`$` trio catches
the named cases without it.

---

## 3. Data flow & touchpoints

```
capture → SORT ─────────────────────────────► ingest → gate
            │ NEW: worthiness floor (zero-LLM)
            ├─ signals: length · source_tier · $-magnitude · entity/paper-link
            ├─ Paper-Governs? (econ-flag / $≥floor / paper-linked) ── yes ──► sorted (draft as today)
            └─ below-bar & not-PG ──► stage: reference (terminal) ──► stays in raw/, searchable
                                       + context-log line + health count "N floored today"
```

**aios engine:** `sort.py` (the floor + signal extraction); the `reference` terminal state in the stage
contract; `gate_metrics`/reconcile recognizing it; the health-count render. Thresholds → `profile`. No
new vault surface (floored items are the raw that already exists).

---

## 4. Testing

- a short tertiary non-economic capture → `stage: reference`, not drafted (test shown);
- a short capture that is econ-flagged → **NOT** floored (drafted + held — the invariant);
- a short capture naming a known entity / with `papered_source` → NOT floored;
- a sub-`DOLLAR_FLOOR` standalone charge → floored; a `≥ DOLLAR_FLOOR` charge → held;
- an above-`LEN_FLOOR` item → drafted as today (regression: default behavior unchanged for real content);
- `DOLLAR_FLOOR`/`LEN_FLOOR` unset → floor disabled, everything drafts (safe default shown);
- a floored item is re-openable via `rewind` to `captured` (nothing lost);
- reconcile/`gate_metrics` count a `reference` item as a terminal exit, not a missing ship;
- the health line reports the floored count; full suite green; fresh-context review zero CRITICAL.

---

## Ecosystem check

Run live in-session on 2026-07-18. Every command executed; results pasted from real output. Capability
shopped: a zero-LLM signal-gate that routes low-signal captures to searchable-raw in a markdown pipeline.

### Leg 1 — Anthropic-first

```
$ grep -rliE "worthiness|signal.?gate|content.?scor|capture.?(triage|filter)|idf" \
    ~/.claude/plugins/cache/claude-plugins-official/
(no matches)
```

No capture-scoring / signal-gate in `claude-plugins-official`.

### Leg 2 — public marketplace

```
$ npx -y skills find "content triage signal filter"
elvisun/newsjack@relevance-coarse-filter   376 installs
posthog/skills@inbox-exploration           147 installs
joelhooks/joelclaw@email-triage             51 installs
$ npx -y skills find "note capture worthiness scoring"
yuque/…@yuque-personal-daily-capture       220 installs   (capture creation, not scoring)
atlassian/…@capture-tasks-from-meeting-notes 152 installs (task extraction, not scoring)

$ WebFetch skills.sh/elvisun/newsjack/relevance-coarse-filter
→ "a gating mechanism in a newsjacking pipeline … a reusable, headless component … rule-based …
   filters NEWS signals … permissive: 'lean toward keeping things, retaining false positives is
   cheaper than missing genuine leads.'"
```

The closest candidate (`relevance-coarse-filter`, verified) is a **headless rule-based pipeline gate** —
right *architecture* — but it filters **news signals** (wrong domain) and is delivered as a SKILL
applying judgment criteria (an LLM applies prose rules), not a tested zero-LLM Python floor. Reference-
only. Its stated posture, however — *permissive, cheaper to keep false positives than miss real ones* —
directly validates A89's conservative-default design (§2.1). The inbox/email/meeting triage skills are
different domains and agent-behavior. None adoptable as the Sort-stage floor.

### Leg 3 — our own skills and tools

```
$ ls engine/tools/sort.py ; grep -c source_tier ../../SecondBrain/_schema/frontmatter-contracts.md
engine/tools/sort.py
1
$ ls ../../docs/superpowers/findings/2026-07-18-cerebras-kb-review.md
../../docs/superpowers/findings/2026-07-18-cerebras-kb-review.md
```

Strong reuse — the floor **extends `sort.py`** (the existing deterministic disposition router), gates on
`source_tier` (already in the frontmatter schema) + the A99 econ-detection path, and implements the
design the Cerebras findings doc already specified for A89 ("a zero/cheap-LLM scoring leg in sort/ingest
that routes low-signal captures straight to archive-searchable"). The raw-as-tier policy (enacted
2026-07-18) means "drop" needs no new storage. adapt-own.

### Leg 4 — full-service platforms

```
$ external service to score our own private markdown captures for worthiness?
none — a hosted content-scoring API is a network call over private capture content in a local, fact-free,
zero-LLM path; wrong tool and wrong posture (no external telemetry).
```

Build-because-posture.

### Verdict

| Leg | Best candidate | Verdict |
|---|---|---|
| Anthropic-first | (none) | build-because-none |
| Marketplace | `newsjack@relevance-coarse-filter` (376) | reference-only (news domain, SKILL-not-library); posture validates ours |
| Own skills/tools | `sort.py` router + `source_tier` schema + A99 econ-path + Cerebras design | **adapt-own** |
| Full-service | hosted content-scoring APIs | build-because-posture |

**Conclusion:** extend `sort.py` with a thin zero-LLM floor. The differentiator the ecosystem doesn't
cover is a *deterministic, Paper-Governs-subordinate worthiness floor at our Sort stage* that routes
below-bar captures to our already-searchable raw tier — permissive by default, visible via a health count.
