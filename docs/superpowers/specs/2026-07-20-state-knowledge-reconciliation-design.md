---
title: State ↔ Knowledge Reconciliation — the pipeline's reverse flow
date: 2026-07-20
status: design
scope: umbrella + sub-spec #1
supersedes: the parked "Phase 4 reconciliation" note
---

# State ↔ Knowledge Reconciliation

## Problem

The AIOS pipeline flows knowledge **inbound only**: raw sources → capture → sort → ingest →
gate → wiki. Operational and economic state lives in the **state engine** (`state/domains/<silo>/tables/*`
typed rows, mirrored from the operational cockpit by `domain_mirror.py`). There is **no reverse
flow**. When a state row moves — a loan balance grows, a net-worth figure changes, an ownership
term is amended — the wiki pages that carry a dated economic snapshot or a state-linked fact go
stale. The knowledge layer silently **trails** the state layer.

The failure mode is concrete: a knowledge page carries a dated balance-sheet snapshot (a figure +
an `as_of` date). Months later the underlying position has materially changed, the state row
reflects it, but the wiki still shows the old number. Nothing detects the divergence, so the wiki
quietly becomes wrong. This was recognized long ago and parked as the **"Phase 4 reconciliation"**;
this spec designs it.

## SSOT model (the principles the design enforces)

- **Federated SSOT by fact-type — one home per fact.** Executed paper + the authoritative number →
  Drive. Operational + economic *state* → the **state engine** (`state/domains`). Distilled
  *knowledge* (what/why/who, deal narratives, concepts) → the **wiki**. When homes overlap, a
  deference ladder decides (for a figure: Drive > state engine > wiki).
- **The cockpit is not the SSOT.** The operational surface (whatever cockpit an install uses) is a
  *view*; the reconciler reads `state/domains` — the destination-SSOT typed rows — **never** the
  cockpit directly. This keeps the design correct even as the SSOT migrates off the cockpit.
- **Detector, not mirror.** Reconciliation finds a drifted **non-canonical copy** and proposes a
  gated correction *toward* its canonical home. It is never a symmetric two-way sync — syncing the
  same fact both ways between two stores is the duplication anti-pattern that *causes* trailing.
- **Paper-Governs holds.** An economic value is validated (against Drive / the statement) and
  routed through the gate at ship time. The detector **proposes**; it never auto-copies a figure,
  and never writes an economic term from a memory value.

## Architecture (umbrella)

One new engine tool — **`reconcile_state_knowledge.py`** — run **nightly inside the brief-cache
gather**, in the same slot as `standing_checks.py` and `brainstorm_packets.py`. No new scheduled
task, no new surface. It is a **read-only detector**: it reads `state/domains` rows and wiki pages,
computes drift deterministically, and **emits gated proposals** into the single queue. Those
proposals surface in the brief's existing review panel and ship through the **gate** — the sole
writer.

Two detector passes, each pushing a drifted copy toward its canonical home:

- **Pass A — state → wiki (refresh).** For each wiki page carrying a tracked economic snapshot (see
  the anchor contract below), compare against the current `state/domains` row. On drift, draft a
  **wiki-refresh proposal** (a review-lane queue item whose staged draft is the corrected page).
- **Pass B — wiki → state (propose structure).** For each wiki entity/person page with **no**
  corresponding `state/domains` row, emit a **state-row proposal** (create the typed row).
  Knowledge proposes *structure*; it never writes an economic term into state.

Neither pass ever auto-copies an economic value; both emit gated proposals; a cleared/rejected
proposal is remembered, not re-litigated nightly.

### Reuse (adopt-and-extend — see §Ecosystem-check)

| Reused component | Role in this design |
|---|---|
| `domain_mirror.py` | already mirrors the operational tables (assets / networth / prices / entities / people) into `state/domains` typed rows — the canonical side of the comparison |
| `state_validate.py` | validates any proposed new state row against the silo `schema.yaml` |
| `queue_tx.py` / `ship.py` / gate | the proposal enqueue + independent-review + ship path (unchanged) |
| A96 proposal lane (`proposal_summary`, `proposal_dedupe_history`, `kind:proposal`) | the emission + dedup surface — reconciliation is just another proposal producer |
| `standing_checks.py` harness pattern | the nightly-from-gather, zero-LLM, degrade-silent runner shape |

The detector's **comparison is deterministic (zero-LLM)**. Only the *drafted refresh prose* in Pass
A is model-authored — and that is exactly what the gate's independent review inspects.

## Sub-spec #1 — the `state → wiki` economic-figure detector (Pass A, v1)

The one hard problem: **how does the detector know a figure in prose corresponds to a specific state
field** without fragile extraction? Answer: **explicit anchors, never NLP.**

### The snapshot-anchor contract

A wiki page that carries a *tracked* economic snapshot declares it in frontmatter:

```yaml
snapshots:
  - state_key: <silo>/assets/<asset-slug>   # resolves to a state/domains typed row
    field: current_balance                  # the row field the prose figure mirrors
    value: 0                                 # what the prose currently shows
    as_of: 2026-01-01                        # the snapshot date in the prose
    track: true                              # false = deliberate fixed/historical mark; never flagged
```

Only anchored, `track: true` snapshots are watched. The `track: false` escape is load-bearing: it
protects **deliberate** marks a page keeps on purpose (e.g. a conservative lender spot vs. an
internal mark held side-by-side, or a point-in-time historical figure). The detector never guesses
which number in a paragraph is canonical — an un-anchored figure is invisible to it.

### The comparison (deterministic)

For each anchor: resolve the `state_key` row, read `field`. Flag drift when **either**:
- `abs(state_value − anchor.value) / anchor.value > value_threshold` (default 2%, plus an absolute
  floor to ignore rounding), **or**
- `anchor.as_of` is older than `stale_days` (default 30) **and** the state row's own last-updated
  stamp is newer than `anchor.as_of`.

Both thresholds are profile knobs (`reconcile.value_threshold`, `reconcile.stale_days`), never
hardcoded.

### What it emits

A **wiki-refresh proposal** on the `review` lane: a staged corrected copy of the page with the
snapshot line's figure + `as_of` refreshed to the state value, plus a one-line diff summary in
`rec_reason`. It rides the existing brief review panel → gate. **Sensitive/economic KBs stay
human-gated** (the gate's kb backstop is unchanged), and the gate's independent review still
validates the figure against Drive / the source statement at ship. The detector proposes *from the
state row* (itself Drive-anchored via `domain_mirror`); it never asserts a papered number.

### Dedup — no nightly nagging

`dedupe_key = (page, state_key, target_value)`. A rejected refresh ("that snapshot is an
intentional historical mark") is remembered via `proposal_dedupe_history` and not re-proposed. But
if the state value moves *again*, the new value is a new key → re-proposed. Steady-state (nothing
drifted) emits nothing.

### Scope guard (YAGNI)

v1 anchors only the handful of economic snapshots that actually move. **No auto-discovery** of
un-anchored figures, no prose scanning — that is explicitly deferred. Anchoring a snapshot is an
opt-in, one-line-per-figure contract.

## Decomposition — future sub-specs (become seeds)

2. **entity/people factual reconcile** — drift between `state/domains` entity/person rows and the
   wiki `legal_status` / ownership % / `papered_source` / `last_verified` fields (audit-grade).
3. **wiki → state new-knowledge proposer** (Pass B) — distilled entities/relationships with no
   state row → gated state-row proposals.
4. **cross-silo generalization** — extend both passes across every silo and every typed table.

## Non-goals

- No symmetric two-way sync (the anti-pattern this whole design avoids).
- No real-time / event-hook firing — nightly-from-gather is sufficient for figures that move over days.
- No auto-copy of economic values into the wiki, ever — every economic write is gated + Drive-validated.
- No NLP / regex figure extraction — anchors only.
- No new UI surface — the brief review panel is the surface.

## Testing

Unit tests under `engine/tools/tests/test_reconcile_state_knowledge.py`:
- anchor parser (valid / malformed / missing `track`);
- drift logic: value-delta above/below threshold, staleness with/without a newer state row, the
  absolute floor;
- `track: false` guard → zero proposals;
- dedup: a rejected value stays suppressed; a *new* value re-proposes;
- emission shape: a drift produces exactly one well-formed `review`-lane proposal with a resolvable
  `draft_path`.
Fixtures: a stale-anchor page + a moved state row → one proposal; a matching pair → zero; a
`track: false` page → zero.

## §Ecosystem-check

Executed 2026-07-20 (real tool calls this session, not reconstructed).

**Leg 1 — Anthropic-first (native capabilities / anthropics-skills / official plugins).**
```
# The runtime mechanism this needs — a nightly, zero-LLM, degrade-silent check that fires from the
# existing gather — is a NATIVE pattern already in use (standing_checks.py / brainstorm_packets.py,
# run inside the brief-cache build). No native capability or first-party skill provides a
# state<->knowledge-store reconciler; the native contribution is the harness shape, which we reuse.
```
Result: **reuse the native nightly-from-gather harness pattern**; no first-party skill supplies the reconciler itself.

**Leg 2 — Public marketplace.**
```
$ npx -y skills find "state knowledge base reconciliation drift detector"
  borghei/claude-skills@doc-drift-detector   (78 installs)
  snyk/studio-recipes@drift-detector         (101 installs)
  davidlee/doctrine@close                    (72 installs)
```
Result: the two "drift-detector" hits target **code/config-vs-doc drift**, not a federated-SSOT,
Paper-Governs-gated, typed-state ↔ knowledge-wiki reconcile. **Reference-only** (worth a glance for
diff-presentation ideas); neither fits the fact-type/gate model. `doctrine@close` is unrelated.

**Leg 3 — Our own skills / tools (the richest, most-skipped leg).**
```
$ ls engine/tools/ | grep -iE "domain|state|queue|ship|proposal|standing|garden|settle|writeback"
  domain_mirror.py  state_validate.py  queue_tx.py  ship.py  standing_checks.py
  notion_writeback.py  brief_session.py  settle_reconcile.py  garden_*.py
$ grep -rliE "reconcile.*wiki|snapshot.*drift|state.*drift" engine/tools/
  (no existing state<->wiki reconciler)
$ grep -n "proposal_dedupe_history\|kind.*proposal\|proposal_summary" engine/tools/brief_session.py
  def proposal_summary(...)   # A96 'Sync proposes' panel
  ... it.get("kind") == "proposal" and it.get("stage") == "awaiting"
```
Result: **heavy reuse** of the pipeline plumbing (mirror, validate, queue/ship/gate, A96 proposals,
standing-checks harness). **No existing reconciler** — the detector + anchor contract are the thin
net-new differentiator. This is the correct custom-build boundary.

**Leg 4 — Full-service platforms.**
```
# Reverse-ETL / data-reconciliation SaaS (Hightouch, Census) and data-validation frameworks
# (Great Expectations) are conceptually adjacent — detect + sync divergence between stores.
```
Result: **rejected.** Wrong trust model (sensitive economic state cannot leave for external SaaS),
wrong substrate (local markdown + typed rows, not a warehouse), and they'd *mirror*, not gate —
violating Paper-Governs. Massive overkill for a nightly local detector.

### Verdict

| Capability | Decision | Source |
|---|---|---|
| Nightly-from-gather runner | **Reuse** | native pattern (standing_checks) — Leg 1/3 |
| Emission + dedup surface | **Reuse** | A96 proposal lane — Leg 3 |
| State-row mirror / schema | **Reuse** | domain_mirror / state_validate — Leg 3 |
| Enqueue / review / ship | **Reuse** | queue_tx / gate / ship — Leg 3 |
| The detector + anchor contract | **Custom-build (thin)** | no ecosystem fit — Legs 2/3/4 |
