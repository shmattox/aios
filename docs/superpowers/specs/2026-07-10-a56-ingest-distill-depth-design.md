---
type: spec
project: aios
item: A56
date: 2026-07-10
status: approved-design
title: Ingest/garden distill DEPTH — integrate the concept, don't just summarize
tags: [aios, ingest, garden, distill, karpathy-wiki, spec]
---

# A56 — Ingest/garden distill DEPTH

> **"Integrate the concept, don't just summarize the source."**
> Finish the implementation of a principle we already vendored (Karpathy LLM-wiki: ingest-time
> synthesis + the 10–15 page fan-out) but only partially built.

## Problem

Today a captured idea-source becomes a shallow `type: source` summary stub (title + one-line
Summary + short Narrative + Open threads) in `wiki/sources/`, and the nightly garden distills
**~1 stub/night** by folding one into a `knowledge/` page. A large class of captures are *smart
operational ideas* whose value is the **concept** ("how to operate off this"), not the artifact —
and the current depth often leaves them as stubs that later read as retire-noise.

**Direct evidence (env-ops H39, 2026-07-10):** a 310-stub `sources/` drain retired 217; the
fan-out classifier judged ~0 distill-worthy, yet Seth manually found a couple that carried real
operational concepts that *should* have become KB knowledge. The leak is **diffuse** — an idea can
be weakened at ingest (flattened into a thin stub), starved in the `sources/` pool (never reached
by the ~1/night distill), or mis-judged at the retire decision (called noise). Seth's framing:
"good ideas end up in the retire pile — stop losing them, wherever the leak is."

**Signal that depth is insufficient** (A56, and Karpathy's own auditable signal): high capture
volume + near-zero knowledge-page growth + rising `sources/` retire-rate = single-summary,
undigested ingests.

## Ecosystem check (run live 2026-07-10, per plan-then-shop)

Three legs, actually executed this session — not reasoned from memory:

| Leg | Query run | Result |
|---|---|---|
| **Anthropic-first** | native Claude Code skills + `anthropics/skills` inventory | No distill/KB-synthesis skill (frontend-design + document-processing only). None fit. |
| **Marketplace** | `npx skills find "knowledge base distill synthesis wiki"` | **"No skills found."** Empty (expected for this niche). Also `find-skills` "distill source notes into a KB" → no match. |
| **Our own (richest)** | inventory of `Projects/aios/skills/**` + `engine/tools/*.py` + garden rulebook | **Heavy reuse.** See below. |

**Own-assets that this design reuses rather than rebuilds:**
- `skills/garden/rulebook/karpathy-llm-wiki.md` — the vendored *why* layer. **Already states A56's
  principle**: "ingest-time synthesis beats query-time RAG"; Hard-rule #2 "every ingested source
  produces multiple downstream edits, not one note"; the **10–15 page fan-out** ("if a source
  produces only one page, it wasn't fully digested"); the **"undigested sources" auditable signal**.
- `skills/garden/rulebook/passes-karpathy-wiki.md` — **F2.8 Undigested sources** (today: "surface
  the backlog, let Step 4 process one stub at a time" — the exact throughput bottleneck A56 names)
  and **F2.7 Stub notes**. A56 finishes both.
- `engine/tools/garden_distill.py` — the deterministic distill *envelope* (enumerate / provenance /
  proposal / relink+archive). Extended in place, not replaced.
- The **merge-completeness self-check** (`extract_checklist`) and `garden_audit.py` (orphan/dead-link
  oracle) — reused as-is.

**Conclusion:** A56 is *not* net-new invention. It is completing an already-adopted principle. This
keeps it inside the retire-don't-improve stance even at ambition level C — we are deepening one
existing capability, not adding a parallel surface.

## Design principle

Preserve the **funnel**: every idea still flows capture → `sorted` → ingest → `sources/` stub →
nightly distill → gate → retire. One new signal — a **classification decided once at ingest** —
threads all three depth legs and doubles as the retire-fence. No new plumbing; depth +
classification + throughput on the existing path.

### The spine — `distill_class`

A new stub frontmatter field, set by ingest, canonical on the stub (read by `garden_distill`):

- `distill_class: concept` — a transferable operational idea; value = the concept. Gets deep
  ingest, enhanced (fan-out) distill, priority throughput, and the retire-fence.
- `distill_class: reference` — a link / artifact / entity-fact; value = the pointer. Keeps today's
  shallow stub + cheap fold-or-retire path.

Classification is **model judgment** in the ingest skill (fact-free, consistent with the two-tier
mechanical-oracle + semantic-judgment pattern), mirrored onto the queue item's `rec_reason` for
observability. Binary for MVP; `entity-fact` is treated as `reference` (entities already have a
home). Refine the taxonomy only if the corpus run shows a third class earning its keep.

## Leg 1 — Ingest depth (`skills/ingest`, Phase A)

Per `sorted` raw, ingest classifies, then drafts to depth:
- `reference` → today's shallow `source` stub, unchanged. `distill_class: reference`.
- `concept` → a **deep** stub — still `type: source`, still in `sources/` (funnel intact) — with a
  structured operational-concept capture:
  - **Core idea** — the transferable concept in full (not a one-liner).
  - **How-to-apply** — how one would operate off this in Seth's context.
  - **Proposed target** — the `knowledge/` page this belongs in (existing slug, or a proposed new
    one) + candidate neighbor pages to touch (the fan-out seed).
  - **Open threads.**
  - `distill_class: concept`.

Cap stays ≤25/run. Deep concept drafts are heavier; if a run is concept-dense it drafts fewer raws
and carries the rest `sorted` (oldest-first), same as today. **Never writes wiki** — still a
staging draft only; the concept-target lands as a *proposal seed* on the stub, not a live page.

## Leg 2 — Enhanced distill / synthesis (`skills/garden` step 4 + `garden_distill.py`)

For a `distill_class: concept` stub, distill runs in **synthesis mode** = Karpathy's fan-out:
1. Read the proposed target `knowledge/` page **and its linked neighbors** (not just the target).
2. **Integrate** the concept into the KB's existing conceptual structure — refine prose, add a
   subsection, and add the cross-links that connect it — rather than transferring bullets. A
   genuine concept distill typically **touches more than one page** (the anti-single-summary rule).
3. Keep the **merge-completeness self-check** (every durable point from the stub checklist lands).
4. Keep **FamilyOffice no-elevation** (carry `source_tier`/`confidence`; no unpapered promotion).

`reference` stubs keep the existing shallow fold-or-retire path — cheap, unchanged.

Every synthesis edit is still a `lane: review` proposal through the gate (multi-page edits =
multiple staging drafts under one distill batch). Nothing auto-applies.

## Leg 3 — Throughput (value-weighted, replaces "1/night MVP")

Garden step 4's "one stub at a time (MVP scope)" is replaced by a value-weighted budget:
- Distill **all `concept` stubs** each night, up to a safety cap **K** (config, default e.g. 8);
  overflow carries oldest-first to the next night.
- `reference` stubs drain the **cheap path** (shallow fold or straight retire) with no per-night cap
  beyond runtime — they don't compete for the synthesis budget.

Budget goes where the value is: concept stubs are rarer and worth the depth; reference stubs must
not starve them (the H39 failure mode).

## The funnel fence (the diffuse-leak plug)

A `distill_class: concept` stub can **never retire as noise** without an enhanced-distill attempt
first. Enforced by a deterministic helper, `assert_noise_retire_allowed(stub_fm)`, that the garden
**de-bloat / prune steps (2–3)** call before proposing any stub for retire-as-noise: it raises on a
`concept` stub unless the stub carries the explicit `distill_attempted: no-durable-concept` marker
a synthesis pass writes when it finds nothing durable. It is deliberately **not** wired into
`retire()` itself — `retire()` is the *distill*-retire path (a knowledge page has already shipped,
so the husk should retire); the fence only guards the *noise*-retire paths that would otherwise
bypass synthesis (the H39 leak). Enforcement level is the garden-step prose gate calling the helper,
backstopped by the review gate (every noise-retire is still a `lane: review` gated proposal, never
auto-applied). This reconciles the design intent above with the shipped enforcement level.
(`reference` stubs retire freely, as today.)

## Validation — the 217-stub batch reprocessor (go/no-go)

A one-shot **batch reprocessor** — `garden_distill.py backfill <archive_dir>` mode (thin, reuses
the enumerate + synthesis envelope) — pointed at the H39 archive
`SecondBrain/*/raw/archive/wiki-sources-retired-2026-07-10/`:
1. Classify all 217 (`concept` vs `reference`).
2. Run enhanced-distill on the `concept`-class ones.
3. Emit **gated proposals + a report**: concept/reference counts, candidate `knowledge/` pages,
   pages touched per concept (the fan-out number).

Double duty: it is both the **acceptance test** (does the depth produce KB-worthy knowledge?) and a
real **recovery of the ideas H39 retired**. Nothing auto-applies — Seth reviews the proposals in the
gate. **This is the go/no-go gate**: the live nightly path (legs 1–3 wired into the scheduled run)
ships only after this evidence shows the depth pays off.

## Signal metric — the undigested-source tripwire

Wire A56's tripwire = Karpathy's "single-summary ingest" signal = pass **F2.8**. One line in the
garden run-note / context-log: **concept-captures in vs `knowledge/` page growth vs `sources/`
retire-rate**, plus **mean pages-touched per concept distill** (the fan-out count; 1 = undigested).
No new surface — a metric line, watchable in the brief's health panel. If depth works, knowledge
growth tracks concept-capture volume and fan-out stays > 1.

## Units (isolated; build in order — classification is the spine everything depends on)

1. **Classification** — `distill_class` frontmatter contract (`engine/kb-schema/`) + the ingest
   judgment. Independently testable: given a raw, asserts a class + reason.
2. **Ingest depth** — the deep concept-stub template in `skills/ingest/SKILL.md`. Testable: a
   `concept` raw yields a stub carrying core-idea / how-to-apply / proposed-target / threads.
3. **Enhanced distill + funnel fence** — `garden_distill.py` synthesis-mode envelope, retire fence,
   budget allocation; `skills/garden/SKILL.md` step-4 rewrite (+ F2.7/F2.8 rulebook touch-ups).
   Unit tests: `assert_noise_retire_allowed` raises on an un-attempted concept stub; budget caps at
   K + carries overflow; merge-completeness still enforced.
4. **Batch reprocessor** — `garden_distill.py backfill` over the 217; gate-review output → go/no-go.
5. **Metric line** — the F2.8 tripwire in the run-note/context-log.

## Acceptance

- Units 1–3 land with tests green (classification, deep-stub template, enhanced-distill envelope +
  fence + budget) — shown.
- The **batch reprocessor** runs over the 217 H39-archived stubs and emits gated proposals + report;
  reviewed in the gate, the enhanced-distill output is knowledge Seth judges **genuinely worth
  keeping** (recovering ≥ some of the H39 concept-losses) — the go/no-go evidence.
- The live nightly path is wired **only after** that evidence lands; the F2.8 tripwire metric is
  live in the run-note.
- Funnel + gate preserved throughout: no auto-applied wiki writes; FamilyOffice no-elevation held.

## Non-goals / YAGNI

- No direct-to-`knowledge/` ingest (funnel preserved — option A, not B).
- No richer-than-binary classification unless the corpus run proves a third class earns its keep.
- No change to the review gate, Paper-Governs, or the auto-ship boundary.
- No RAG / vector store — Karpathy's whole point is ingest-time synthesis over retrieval.
- Not a new observability surface — the metric is one existing-run-note line (respects H51 lens).

## Risks / open questions (resolve in planning)

- **Concept-drafting cost at ingest** — deep drafts are heavier; confirm the ≤25/run cap + carry
  behavior keeps a concept-dense night bounded. Consider a separate `concept` sub-cap if needed.
- **Classifier precision** — a `reference` mis-called `concept` wastes synthesis budget; a `concept`
  mis-called `reference` re-opens the leak. The corpus run measures both error directions before
  live wiring.
- **Fan-out vs FamilyOffice discipline** — multi-page synthesis must still honor no-elevation and
  one-home-per-fact; the gate's fresh-context audit leg is the backstop.
- **K (nightly synthesis cap)** — pick the default from the corpus run's observed concept density.
