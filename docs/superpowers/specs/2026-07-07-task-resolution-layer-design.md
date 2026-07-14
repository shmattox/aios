# Task Resolution Layer — the per-task cross-system join

**Date:** 2026-07-07
**Status:** designed (build slice = FO economic lane; full layer specced, incrementally delivered)
**Skill/home:** new `resolve` capability in the **aios plugin** (universal product work). Deep-resolve
orchestration saved as `~/.claude/workflows/resolve.js` (sibling to `review-gate.js`); background
sweep runs in the scheduled/native tier.

## Problem — the black box is structural, not sloppiness

No stage in AIOS is responsible for **joining a task to the rest of the system.** Each source is
gathered in its own lane and correlated only loosely by domain; nothing assembles everything known
about *one task's subject* across Trello + Drive + vault + Notion before anyone acts or advises.

Three exhibits from the current system:

1. **`Scheduled/sync-trello/SKILL.md:15`** — an economic Trello figure is tagged
   `legal_status: verbal`, quoted, and cited. The same line says it "must be validated against
   executed Drive docs before it is canonical" — but **nothing ever performs that validation.**
   "Verbal" ends up meaning *"nobody looked,"* not *"no paper exists."*
2. **`skills/brief/references/gather.md:41`** — the brief reads Drive only "as needed — pull an
   executed doc **only to confirm** a Paper-Governs flag." Drive is read to *confirm an existing
   flag*, never to *discover* the source doc for a task that lacks one. Trello's other cards are
   never cross-read for corroboration.
3. **The gather is domain-scoped, not task-scoped** (`gather.md:30`): per domain group it pulls
   tasks (Notion) + playbook pages (vault) + flag-confirmation (Drive). Nothing joins the four
   sources on one task's subject. So `system_voice = "🅾 your system is silent"`
   (`brief/SKILL.md:293`) means *"not found in what we happened to gather,"* not *"searched and
   empty."* A false "silent" is indistinguishable from a true one.

**Through-line:** MOCs, manifests, and KB structure are *navigation* aids — they help someone
already looking in the right place. Nothing is *obligated* to look. That missing obligation is the
black box. It almost certainly affects most tasks, not one.

### Motivating case

"Pay the property insurance." The amount lives on a Bayview Trello card (verbal). The executed
declaration PDF sits in Drive. Today the task is acted on from the Trello number tagged verbal;
the declaration is never found; the system reports the figure "undeclared." **The declaration
exists — nobody searched.**

## Design

Introduce **`resolve`** — one shared capability whose job is: *given a task, assemble and vet
everything the system knows about its subject before anyone acts or advises.* It is the missing
join. Everything else is an entry point into it.

### Architecture — one skill, two entry points (hybrid)

Rejected alternatives: a new pipeline **stage** (wrong axis — resolution is a decision-time act on
tasks, not a gardening step on inbox items; would pay cost on everything), and **baking** the
logic into each consumer (duplicates logic, drifts — that *is* today's problem).

| Entry point | Trigger | Work | Model |
|---|---|---|---|
| **Sweep** (cheap pre-join) | background, **scheduled/native tier** (unattended) | flag unresolved tasks (high-recall) **and pre-scrape their raw evidence** into `state/resolve-cache/` — deterministic fetch I/O | **none** |
| **Deep-resolve** (on-demand) | session-invoked from the brief / action thread | over the **already-warm** cache: subject-match → select governing doc → semantic-align figures → `resolve_verdict.py` → render; **fanned out** across flagged tasks | Opus for fuzzy legs; scripted verdict |

The slow work (network fetch, PDF extraction) happens overnight in the deterministic sweep. The
morning brief does **verification, not gathering** — align cached figures, run the gate, render —
and fans that out. Warm evidence + parallel verify = the fast morning brief.

**Selective, so fast *and* cheap.** The sweep's cheap **delta-check** (did the source change since
scrape? — mirrors the brief's existing Notion delta-check) marks unchanged resolved tasks
`warm/clean`; the brief renders those **straight from cache with zero sub-agents.** Fan-out spawns
only for newly-flagged, stale, or conflicting tasks. Morning cost scales with *what changed*, not
task count.

### The join mechanism — entity-anchored, semantic fallback (self-healing)

Each canonical entity page gains a small, additive **link registry** in frontmatter — the crosswalk
resolution follows. This is the **only** new schema; a page without it falls to semantic search.

```yaml
# 02_FamilyOffice/wiki/entities/bayview-property.md
links:
  drive:   ["<file_id> — 2024 insurance declaration", "<file_id> — purchase HUD"]
  trello:  ["<card_url> — Bayview weekly OPS"]
  notion:  ["<page_id> — Bayview (Assets & Liabilities row)"]
  aliases: ["Bayview", "the Bayview deal", "123 …"]   # subject-matching for a plain-language task
```

- **Entity-anchored first:** a task's subject (+ domain) matches an entity via `aliases`;
  resolution follows that entity's links. Deterministic and cite-able once wired.
- **Semantic fallback:** when a task has no linked entity, or the entity lacks the needed link,
  run a semantic/keyword search across each source, rank hits — and **propose adding the discovered
  link back to the entity page** (routed through `gate`, a vault write). The graph self-heals:
  every resolution makes the next one more deterministic and cheaper.

### The dossier — deep-resolve's output

Structured object attached to the task, cached in `state/resolve-cache/` keyed by task id + a
content hash (invalidates when the task changes):

```
subject:      bayview-property (entity)  |  "unresolved — no entity"
claim:        "property insurance premium = $X/yr"       # the fact under question, semantically typed
evidence:     [ {source: drive,  ref: <file_id>, says: "$X", qty: annual-premium, tier: paper,  executed: true},
                {source: trello, ref: <card>,    says: "$Y", qty: annual-premium, tier: verbal},
                {source: notion, ref: <row>,     says: "$X", qty: annual-premium, tier: operational} ]
verdict:      papered | conflict | verbal-only | silent
canonical:    "$X — cited to Drive:<file_id> (2024 declaration)"   # only when verdict=papered
conflict:     "Trello card says $Y; declaration says $X — Δ$…"     # only when verdict=conflict
provenance:   deference chain applied (Drive > Notion > xlsx > verbal/memory)
```

`verdict` is computed by the **existing deference ladder** — resolution doesn't invent authority,
it *applies the ladder already written down.*

### Determinism split — model orchestrates and comprehends; scripts own the auditable joints

Deep-resolve is a **model-orchestrated agent that routes its authority-bearing decisions through
deterministic tools.** The model comprehends and drives control flow; it **cannot declare `papered`
by fiat** — it hands structured, semantically-typed evidence to the tested gate, and the gate
decides. This is Paper-Governs expressed as code: assemble honestly, but the auto-promote gate is
un-fakeable and inspectable because it is a boolean over structured inputs — *not to remove
judgment, but to make it auditable.*

**Dividing line (stated once):** *does getting it wrong silently produce a confident false verdict,
and does avoiding that require understanding?* If yes → model comprehension feeding a scripted gate,
never a script pretending to understand. If it's mechanical and must be auditable → script.

**Deterministic engine tools (Python, unit-tested, no model):**
- **`resolve_sweep.py`** — scans open tasks; flags unresolved ones **high-recall** (figure present
  OR economic keyword OR stale-crosswalk — never just "has a number," which would miss
  "renew the policy" and silently re-create the black box). Then pre-scrapes flagged tasks' raw
  evidence into `state/resolve-cache/`. A genuinely fuzzy residual ("does this task assert a fact
  needing paper?") may use a light classifier — but the flag is high-recall by design (a missed
  flag = never looked = the original bug).
- **`resolve_fetch.py`** — given an entity's `links`, fetch the referenced records. Deterministic
  I/O. (Fetches; does **not** decide which doc governs — that's the model's relevance call.)
- **`resolve_verdict.py`** — ⭐ the load-bearing one. Takes **semantically-aligned** `evidence[]` and
  applies the deference ladder + the strict clean rule → `verdict` + `canonical`/`conflict` + Δ.
  **The auto-promote authority gate lives here, in tested code — never in a prompt.** Whether an
  economic `verbal→papered` promotion fires is a deterministic boolean; it cannot misfire on a
  model's whim.
- Dossier→brief-card render **extends `brief_render.py`** (lifted verbatim), same as `system_voice`.

**Model judgment — only where irreducibly fuzzy, producing structured inputs the scripts consume:**
- Subject→entity match *when `aliases` don't exact-hit* (exact/alias match is deterministic).
- **Selecting the governing doc** among an entity's links for this claim-type (relevance — the
  insurance task is governed by the *declaration*, not the HUD or tax bill).
- **Semantic typing/alignment of figures before the verdict gate** — is `$4,200/yr` vs `$350/mo`
  a conflict or agreement? Is "coverage limit" the same field as "premium"? A script that
  blind-compares numbers of different `qty` emits confident false verdicts. The gate compares
  **only within matching `qty`**; aligning `qty` is model work.
- Extracting a stated figure from an *unstructured* doc (a scanned declaration page).
- The semantic fallback search + ranking, and all fallback/re-fetch/unit-reconcile control flow.

### The strict "clean" rule (so auto-promote can't misfire)

`verdict = papered` — the only verdict that auto-promotes `verbal→papered` and tells you
(fix-then-tell) — requires **all** of:
- exactly **one** candidate governing doc for the claim,
- it is **executed** and in the **Drive paper tier**,
- its figure **matches** the task's figure **within the same `qty` type**,
- **no** other source contradicts it (within matching `qty`).

Any wobble — two candidate docs, doc≠card, `executed` unknown, a `qty` mismatch, or the only
evidence is Trello/verbal — sets `verdict = conflict` or `verbal-only` and **fails loud in the
brief**, waiting for the human. Economic figures auto-promote **only** under `papered`; everything
softer waits. (This literally applies the fix-then-tell vs fail-loud-and-wait boundary: clean =
mechanical + reconstructable-from-source; conflict/ambiguity = a judgment that waits.)

### Fan-out — two axes (uses the existing `Workflow` machinery)

> **BUILD STATUS (2026-07-09): this deep-resolve fan-out is NOT on disk.** No
> `~/.claude/workflows/resolve.js` exists; what shipped (A31/A34) is the overnight *sweep* that flags
> economic tasks + warms a crosswalk-candidate cache, and the brief's read-only surfacing of that cache
> via `resolve_brief`. Because slice-1 wired resolve into the read-only brief only, `resolve_verdict`'s
> `auto_promote` boolean is currently **consumed by nothing** — it is computed but no caller acts on it
> (re-homing the verdict into the `gate`, where an economic promotion actually happens, is backlog A51).
> The fan-out below remains the design target, not a shipped capability.

Deep-resolve is an orchestration, saved as `~/.claude/workflows/resolve.js`, invoked by the brief
with the set of flagged/changed task ids:

```
pipeline(flaggedTasks,
  task => parallel(task.evidenceCandidates.map(c => fetch+extract+type(c)))   // within-task, barrier
        → align → resolve_verdict.py → dossier)                               // scripted gate at the join
```

- **Across tasks (pipeline):** one resolver per flagged task, concurrent — the morning-brief
  throughput win (whole set completes in ~one task's wall-clock). Independent → pipeline, no barrier.
- **Within a heavy task (parallel + barrier):** one leaf per evidence candidate (extract-this-doc,
  read-this-card), **barrier before the verdict gate** — the gate genuinely needs the full aligned
  evidence set before it can rule. This barrier is *correct*, not lazy.

**Model tiers inside the fan-out** (controls spend): per-source extractor leaves are cheap
(Haiku — "read the premium off this page" is light); the per-task orchestrator that aligns and
decides is **Opus 4.8**; the verdict is scripted (free). Cheap leaves, one smart head, free gate.
Model is a **profile field** (`resolve.sweep_*`, `resolve.deep_model`, `resolve.leaf_model`) — never
hardcoded; same fact-free discipline as the rest of the engine.

### How it kills the false "silent"

Resolution runs *before* the brief grades `system_voice`. The grade reads the dossier's `verdict`:
- `papered` → **Grade 1** — "your system says $X — cited to the 2024 declaration (Drive)."
- `conflict` → **Grade 1, flagged** — "$X per paper, but Trello says $Y — reconcile."
- `silent` → emitted **only after** entity+semantic search genuinely finds nothing. "Silent"
  finally means *searched and empty*.

The sweep's flag also surfaces in the brief header even before resolution
("⚠ 3 economic figures with no paper"), so the gap is *visible* — the brief can no longer imply
"nothing to check here" when it simply hasn't looked.

## Build slice — FO economic lane, end-to-end, on the insurance case

Full layer above is designed; slice 1 builds:

1. Add the `links` registry to **one** entity page (Bayview property) with its real Drive
   declaration file_id, Trello card, Notion row, aliases.
2. Build `resolve_sweep.py` + `resolve_fetch.py` + `resolve_verdict.py` with unit tests — the
   verdict tool gets the most: clean-match, doc≠card conflict, two-candidate conflict, `qty`
   mismatch, verbal-only, silent.
3. Wire deep-resolve into the **brief only** (not sync-trello yet): the property-insurance task
   returns **Grade 1 "your system says $X — cited to the 2024 declaration (Drive)"** or a flagged
   conflict — instead of today's "verbal / silent."
4. Prove self-heal (semantic fallback discovers a missing link → proposes it via `gate`) and
   auto-promote on this one item.

### Acceptance test (the demo to judge)

> Fire the FO brief. The property-insurance task comes back citing the actual declaration PDF with
> the figure resolved **from warm cache** (`qty`-aligned). Seed a conflicting number on a Trello
> card → the same task comes back `conflict`, refuses to auto-promote, and waits. Flag two tasks at
> once → they resolve **concurrently**. All shown in chat, with the Drive file_id cited.

This single test exercises the whole spine: crosswalk → fetch → align → verdict → clean-vs-conflict
gate → render → self-heal → fan-out.

### Deferred to later slices (designed, not built in slice 1)

- `sync-trello` integration (hold → resolve at capture time).
- All-domain sweep + the "N figures with no paper" brief-header flag at scale.
- Semantic-fallback self-heal at scale across the vault.
- Full `resolve.*` profile fields + the scheduled/native sweep registration.

## Discipline / non-negotiables

- The auto-promote gate is **deterministic and tested** — model may assemble evidence but never
  declares `papered` by fiat.
- Real titles / real file_ids — cite every source; never a shorthand.
- Vault/Notion writes (link self-heal, any promotion the human confirms) route through **`gate`**;
  resolution itself is read-and-assemble, `gate` is the only writer.
- Fact-free: models, paths, DB ids read from `profile/` — never hardcoded.
- Sweep is unattended → scheduled/native tier; deep-resolve is session-invoked from the brief.
