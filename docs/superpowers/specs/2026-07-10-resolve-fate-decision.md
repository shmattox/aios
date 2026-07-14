---
type: spec
date: 2026-07-10
topic: resolve-fate decision (A51 moat zone)
status: decided
supersedes_zone: A51 "Resolve collapse (~275) + re-home resolve_verdict into the gate"
gates: A35, A36, A38
---

# Resolve-fate decision — retire `auto_promote`, advisory-verdict-only

## Context

The task-resolution layer (A31/A34/A40) computes, per economic claim, a deterministic
Paper-Governs verdict via `engine/tools/resolve_verdict.py::compute_verdict`:

- input: a claim quantity + typed evidence rows (paper > operational > verbal deference ladder)
- output: `verdict` ∈ {`papered`, `conflict`, `verbal-only`, `silent`} + `canonical` cite +
  `conflict` reason + `provenance` + an **`auto_promote`** boolean (True only on the strict-clean
  case: exactly one executed paper-tier doc, qty-aligned, nothing contradicting, nothing untyped).

The verdict is computed during the brief's *gather* (`skills/brief/references/gather.md`, model-run)
and rendered as a dossier card by `brief_render.render_dossier`. It reaches Seth as **advisory**
information during the brief walk — his decision point for economic items.

**The dead-end (A50, 2026-07-09 come-to-Jesus):** `auto_promote` is **consumed by nothing**. The
brief is read-only, so it structurally cannot act on the boolean. The never-built "gate promotes on
it" path (`resolve.js`) is not on disk. So `auto_promote` is advisory output that no code acts on.

## The decision (Seth, 2026-07-10 brainstorm)

**Retire `auto_promote`; keep the verdict as an advisory dossier card only.** Paper-Governs is
unchanged — **every economic promotion still holds for Seth's approval** (FO is `full` and omitted
from `auto_ship_kbs`, so everything economic holds regardless — the A9 finding).

Rationale: re-homing `resolve_verdict` into the gate only has a *purpose* if `auto_promote:True` is
meant to actually **fire** — i.e. auto-promote a cleanly-papered economic figure **without** Seth's
approval. That would **loosen** Paper-Governs, against the 2026-07-09 come-to-Jesus stance (*default
to subtraction; everything economic holds*). Since nothing economic auto-ships, there is no
auto-promotion to home anywhere. The verdict's real value — helping Seth decide faster — is already
delivered by the brief dossier card. So the boolean is genuinely dead code; retire it.

**Consequence:** this *demotes* the resolve-collapse from moat-touching to **moat-free plumbing** —
retiring an unwired boolean changes no economic behavior, so the collapse no longer needs the
`review-gate` workflow; it follows the standard suite-green dev loop.

## Scope of the retirement + collapse (now moat-free)

1. **Retire `auto_promote`** from `resolve_verdict.compute_verdict`'s returned dicts (all branches),
   and its two readers: `gather.md` stops copying it into the cache dossier; `test_resolve_e2e.py`
   drops the `auto_promote is True` assertion (assert the `verdict` instead). Remove the dead
   BUILD-STATUS prose about the never-built gate-promote path. Keep every verdict branch
   (`papered`/`conflict`/`verbal-only`/`silent`) and `canonical`/`conflict`/`provenance` — the
   advisory card is unchanged.
2. **Collapse the resolve plumbing (~275 lines)** — the A51 zone, now moat-free:
   - `resolve_sweep_task.py` 287→~80: drop the redundant `_read_resolve_list` YAML parser (the file
     already imports `_parse_yaml_subset`) and the content-hash warm-cache; **KEEP** the
     degraded-preserve guard (A34/A49 — load-bearing).
   - `resolve_fetch.py` 84→~40: reuse the shared `frontmatter.read_frontmatter` (A51 (ii)).
   - `seed_resolve_defaults.py` 81→~15.
   - Kernel (`resolve_sweep.py` + `resolve_verdict.py` minus `auto_promote`) stays load-bearing.

## Fate of the shelved items

- **A35 — un-shelved → Open.** Brief header "⚠ N economic figures with no paper" (all-domain sweep).
  This is *visibility* of an unresolved gap — advisory, adds no automation — so it is aligned with
  advisory-verdict-only. Survives.
- **A36 — closed won't-build.** sync-trello resolve-at-capture is *feature widening* of the resolve
  subsystem, against the subtraction stance. Pointer left; reopen if a concrete need appears.
- **A38 — closed won't-build.** Semantic-fallback self-heal is *feature widening*. The multi-dir
  entity-discovery leg already shipped (2026-07-08); the self-heal leg is dropped. Pointer left.

## Acceptance (for the retirement + collapse build item)

- `grep -rn auto_promote engine skills deploy` returns zero hits outside git history (shown).
- The four verdict branches still render their dossier cards unchanged (existing render tests green).
- `resolve_sweep_task`/`resolve_fetch`/`seed_resolve_defaults` per-file line-count deltas shown; the
  degraded-preserve guard retained (its test still green).
- Full suite green. Standard fresh-context review (moat-free — no review-gate required).
