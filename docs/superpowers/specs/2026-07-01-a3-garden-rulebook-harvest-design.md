---
title: "A3 — Garden Rulebook Harvest (Benos os-optimizer → garden Steps 1–3)"
date: 2026-07-01
status: design-approved
provenance: >
  Authored 2026-07-01 as harness docs/2026-07-01-h21-garden-rulebook-harvest-design.md.
  The harness repo (shmattox/harness) was deleted after the A8 cutover, taking the doc with it;
  recovered verbatim 2026-07-05 from the authoring session transcript and re-homed here (aios is
  the go-forward engine repo where A3 lands). Repo-name references to "harness" below read as
  "aios" — the design is otherwise unchanged.
feeds: "Projects/aios/BACKLOG.md → A3"
---

# H21/A3 — Garden Rulebook Harvest

> **Design (approved, pre-plan).** Next step is `superpowers:writing-plans`. **Build order: H21 runs AFTER H22** (per the parent design's D3 — H22 rips the FUSE scar tissue first). This doc is the H21 half. *(2026-07-05 note: H22 shipped as aios A4; the Connect audit-first leg shipped separately as `garden_audit.py`, commit `e25cac2`. What remains is the rulebook harvest below.)*

## Context / provenance

The 2026-06-22 `ANALYSIS-BenAI-vs-Mattox-OS.md` verdict on Benos `os-optimizer` was **"MERGE — highest priority: port the framework set + role-discovery into `garden`, adapted to our multi-repo three-node reality."** `ROADMAP-what-we-take-from-Ben.md` thread 1 left one decision open: *extend `doc-refresh` vs a new `garden` pass*. This design **resolves it (garden) and refines the integration** per the 2026-07-01 brainstorm.

Parent design (both workstreams + the four cross-cutting decisions D1–D4): `SecondBrain/03_Dev/wiki/staging/hermes/specs/2026-07-01-karpathy-loop-simplification-benos-harvest-design.md`.

## The reframe that shaped this design

`os-optimizer` (921-line SKILL) splits into two separable halves:

- **A rulebook** — 9 framework *reference* docs (the *why*) + *pass* files (the *how*). The hard-to-rebuild judgment content; the karpathy-lint knowledge H21 wants.
- **An interactive shell** — role-discovery → per-finding `AskUserQuestion` *walk* → **default "bulk-apply, no escapes"** → HTML dashboard. Built for a solo human walking findings live and auto-applying them.

That shell is the **inverse of our moat**: our garden runs *unattended* and must *propose through the gate*, holding anything economic/Paper-Governs. Forking the whole engine would import the exact autonomy the moat rejects, then spend the integration neutralizing it — new scar tissue.

**And the runner we need already exists.** The harness `garden` skill (Stage 5) is already built with the right shape:
- **Step 4** (mechanical litter sweep) — built (`tools/garden_sweep.py`).
- **Step 5** (propose-don't-apply: enqueue `source:self` → the existing `gate`) — built.
- **Steps 1–3** (Connect / De-bloat / Prune) — **pseudocode stubs.** ← where H21 lands.

So "fork os-optimizer" reduces to: **harvest the rulebook and use it to fill garden Steps 1–3.** No new stage, no new runner, no gate surgery.

## Decisions (brainstorm 2026-07-01)

| # | Decision | Chosen |
|---|---|---|
| B1 | Fork depth | **Rulebook only** — vendor framework reference + pass files; leave the interactive shell behind. |
| B2 | Scope | **Garden-only for v1** — ingest (Stage 3 per-item drafting) is a proven custom workflow; not touched. Ingest fork is a possible later item. |
| B3 | Framework tier | **Core: F2 + F8 + G7-mechanical + F9-index.** (F3 em-dash rejected — Seth uses em-dashes. F1/F4/F5/F6 → v2.) |
| B4 | Lane mapping | **Tiered** — mechanical fixes auto-ship on Dev/Personal; all semantic + all FamilyOffice held for review. |

Deliberate departure from the 2026-06-22 analysis: **role-discovery is skipped.** Role-discovery earns its keep on *unknown* vaults; ours is known and **declared** in each KB's `CLAUDE.md` + `_schema/frontmatter-contracts.md` + the harness profile. Discovering it would re-derive what we already assert and fight the fact-free Stage Contract. Garden targets our schema folders directly.

## Architecture — populate garden Steps 1–3

`garden` reads the live vault per KB (paths via profile/`connectors.yaml`, fact-free), produces proposals internally, and enqueues each as a queue item through the **already-built** Step 5 routing. Nothing downstream changes.

### Framework → garden-step mapping (Core tier)

| Garden step | Frameworks | What it produces |
|---|---|---|
| **1. Connect** | F2 (wikilinks, orphans, schema) + F8 (emergent concepts/entities-to-merge, contradictions) | Missing-wikilink proposals; new-page-for-emergent-concept proposals; entity-merge proposals; contradiction-resolution proposals. |
| **2. De-bloat** | G7 (dup-H1, frontmatter) + F9.5 (folder duplication) + F8.2 (duplicate-fact merge) | One-home-per-fact merges; stub-only-page flags; folder-duplication flags. |
| **3. Prune stale** | F8.3 (stale entries) | Rewrite/retire proposals for entries past TTL whose source files are gone (never anything still `awaiting` in the queue). |
| **(cross-cutting hygiene)** | G7 (broken-link repoint, frontmatter completeness) + F9 (index freshness, navigation orphans) | Mechanical repairs — the auto-ship tier (B4). |

### Lane mapping (B4) — how each proposal is enqueued

Enqueue via `tools/queue_tx.py add`, `source: self`, `conflict_key` = target page. `lane_policy.ship_action()` then routes — **no gate/lane_policy code changes**:

- **Mechanical** → `lane: auto-ship` — G7 frontmatter-completeness / dup-H1 / broken-link repoint, F9 index refresh. Auto-ships **Dev/Personal only** (FamilyOffice held by the existing `kb_backstop`; every ship git-revertible).
- **Semantic** → `lane: review` — ALL F2 wikilink *inference*, ALL F8 (merge / contradiction / stale-rewrite / promote / emergent-theme page creation).
- **FamilyOffice, any framework** → held (backstop; unchanged).

Rationale for the split: mechanical fixes have a single correct output and are reversible; semantic fixes are judgment calls — exactly what the gate exists to hold. F8 never auto-runs on any KB.

## What gets vendored vs left behind

**Vendor** into the aios repo (`skills/garden/rulebook/`), security-audited first (`skill-security-auditor`, per env doctrine):
- `references/karpathy-llm-wiki.md` + `references/passes-karpathy-wiki.md` (F2)
- `references/anthropic-dreams.md` + `references/passes-reflection.md` (F8)
- `references/passes-general-hygiene.md` (G7, minus em-dash)
- `references/anthropic-architecture.md` + `references/passes-architecture.md` (F9 — index/orphan subset)

**Adapt on the way in:**
- Strip role-discovery (Steps 1.5) — resolve folders from our KB schema, not `.claude/vault-roles.json`.
- Drop F3 (caveman/em-dash) entirely.
- Retarget F9's `Plot.md` assumptions onto our real conventions: root/KB `CLAUDE.md` routing + `wiki/index.md` + `domains.yaml` (there is no `Plot.md` in our vaults).
- Convert every "walk + apply" instruction into "emit a proposal" — the fix text becomes the queued draft body; application is the gate's job.

**Leave behind entirely:** the interactive walk, bulk-apply default, HTML dashboard, `TaskCreate` progress UI, `vault-roles.json`, F1/F4/F5/F6 (→ v2), `os-operator`/`os-mcp`/`os-setup`/`team-os` (rejected in the parent design).

## Explicitly unchanged (the moat + load-bearing plumbing)

- `pipeline/QUEUE.md` contract, `tools/queue_tx.py`, `tools/lane_policy.py`, the Paper-Governs `kb_backstop`.
- `garden` Step 4 (`garden_sweep.py`) and Step 5 (propose routing) — already built; H21 only fills 1–3.
- Stage Contract seven invariants — H21 honors them (fact-free, self-contained, VERIFY, atomic `queue_tx` writes, self-heal, re-sync-on-edit, context-log append).
- The review gate, Paper-Governs hold, env routing, two-layer brief, fact silos.

## Testing

- **Logic-mirror harness** (same pattern as the state-engine H15–H16b build): each framework's finding-set is recomputed deterministically from a **fixture vault's** frontmatter and snapshot-asserted, so the model's judgment is checked against a fixed oracle rather than trusted.
- Runs against the **test vault** (`install/vault/`), never live, until proven.
- **Gate before merge:** fresh-context whole-branch review (not the builder) — block on CRITICAL; **dev-tier security leg:** `differential-review` on the diff. Native commit + push.

## Sequencing

1. **H22 first** (parent design D3) — rip the FUSE scar tissue; clean substrate. *(Shipped as A4.)*
2. **This (H21/A3)** — vendor + adapt rulebook → populate garden Steps 1–3 → logic-mirror tests → gate → merge.
3. **Schedule** (parent D4) — native Windows weekly task once garden logic proves out. *(The weekly garden task already exists; it picks the rulebook up via the skill.)*
4. **v2 candidates** — F1/F4 passes; ingest fork (B2); revisit remote/mobile access (separate ROADMAP thread 2).

## Self-review

- **Placeholders:** none — every framework maps to a concrete garden step + lane; vendored file list is explicit.
- **Consistency:** matches the parent design's D1 (fork Benos) and moat protect-list; resolves ROADMAP thread 1; the one departure (skip role-discovery) is stated with rationale.
- **Scope:** single implementation plan — garden Steps 1–3, Core-tier passes, one lane-mapping change (data-only, no `lane_policy` code edit). Ingest and v2 passes explicitly out.
- **Ambiguity:** "mechanical vs semantic" is the one line an implementer could read two ways — pinned in the Lane mapping section (mechanical = single-correct-output + reversible: frontmatter/dup-H1/broken-link/index; everything F8 and all wikilink *inference* = semantic).
- **Risk:** the vendored pass files assume os-optimizer's single-vault, auto-apply, role-registry model. The adapt step (strip role-discovery, retarget Plot.md, proposal-not-apply) is where that model is unwound — if a pass is imported without that unwind, it imports baggage. The plan must do the adapt per-pass, not wholesale-copy.
