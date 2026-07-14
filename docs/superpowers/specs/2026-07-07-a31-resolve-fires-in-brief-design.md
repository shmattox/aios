# A31 — Make the resolve step fire in the brief (deterministic read of the warm cache)

**Date:** 2026-07-07
**Item:** A31 remaining leg (the "brief reads the warm cache → renders dossiers" acceptance).
**Status:** design approved; ready for a plan.

## Problem

The AIOS brief has a resolve step (`skills/brief/references/gather.md` step 4) that is meant to,
for each economic FO task: flag it, match it to a `familyoffice` entity, read the governing Drive
doc, build typed evidence rows, run `resolve_verdict.py`, and render a papered / conflict /
verbal-only dossier. The live-fire finding (2026-07-07) is that **the model silently skips this whole
step** — it lives only as prose, and the brief takes the paint + Notion-delta path without ever
running resolve. `state/resolve-cache/` is never written on a brief run.

A34 shipped an overnight sweep (`aios-resolve-sweep`) that now writes `state/resolve-cache/sweep.json`
with the flagged economic tasks + their candidate crosswalk refs (proven live: 71 real tasks → 45
flagged). But A34 only pre-computes the **first half** (flags + candidate refs) — not verdicts, not
Drive bytes. The brief still doesn't *read* `sweep.json`, and the per-task deep-resolve (Drive read →
align → verdict) is still model+MCP work that can be skipped.

**Reading the cache alone does not fix this.** The design's job is to make the resolve step
**impossible to skip silently** — either it fires, or the brief says loudly that it didn't.

## The env principle this applies

*Deterministic render for must-hold formats* + *fix-then-tell vs fail-loud*: a format that must
always appear is emitted by a deterministic renderer and lifted verbatim; a step that must always run
is verified by a deterministic check that fails loud when it didn't. Precedents in this repo:
`brief_render.py` (A10/A11 — the `🔵/🟠` card can't drop) and `context_log.py check` (A21 — a missing
stage line is caught on disk, not taken on the model's word).

## Design — three deterministic guarantees make the step un-skippable

### 1. Concrete worklist from the warm cache

Gather reads `state/resolve-cache/sweep.json` and turns it into an **explicit enumerated worklist**:
the flagged economic tasks + their candidate governing-doc refs. The brief no longer reads prose that
says "run `resolve_sweep`"; it reads a named list of tasks to resolve. For each, it dispatches one
`deep_model` sub-agent (`dispatching-parallel-agents`) that: reads the candidate Drive doc(s), builds
the typed evidence rows `{source, ref, says, value, qty, tier, executed}`, runs `resolve_verdict.py`
(NEVER sets the verdict itself), and writes the dossier to `resolve.cache_dir` keyed by task id +
content hash. Concrete, enumerated work beats buried prose.

### 2. Deterministic dossier render (subsumes A37)

`brief_render.py` gains a `render_dossier` op: dossier data → the verbatim verdict card, mapping
`verdict` → `system_voice` grade (papered / conflict / verbal-only / silent) — same discipline as the
two-layer `🔵/🟠` block. The model produces *data* (the evidence rows + the `resolve_verdict` result);
the engine owns the *format*. This keeps the Paper-Governs papered↔conflict distinction from drifting
between renders, and it means "render the dossier" structurally requires having resolved the task.
This is A37's "deterministic dossier render" work, pulled into this slice because the two are the same.

### 3. Completeness check = the forcing function

A tested check (the `context_log.py check` pattern, applied to resolve) verifies that **every flagged
task in `sweep.json` has a dossier** in `resolve.cache_dir` before the brief is considered valid. The
**tool itself emits the loud line** ("⚠ resolve INCOMPLETE — N of M flagged economic tasks unresolved:
…") and the brief lifts it **verbatim** — the line is NOT composed by the model, because a
model-composed warning can be skipped exactly like the resolve step it is meant to police. This
tool-owned, verbatim-lifted output is the guarantee that actually forces firing: a skipped resolve is
caught on disk and surfaced in the brief body, never hidden.

## Flow

```
overnight A34 sweep ──► sweep.json (flagged tasks + candidate refs)
                             │
brief PASS 2 gather ─────────┤ 1. read worklist from sweep.json
                             │ 2. per flagged task: deep_model sub-agent
                             │      Drive read → evidence rows → resolve_verdict → dossier
                             │      (write to resolve.cache_dir)
                             │ 3. brief_render.render_dossier  → verbatim verdict cards
                             │ 4. completeness check           → loud if any flagged task has no dossier
                             ▼
                         brief body with the resolve dossiers (or a loud INCOMPLETE line)
```

## Components / units

1. **Worklist read** — a small deterministic reader (in `gather.md` flow, backed by a tested helper)
   that loads `sweep.json` and yields `[{task_id, title, candidates[]}]`. Absent/empty cache → empty
   worklist, resolve section omitted cleanly (no error). *Plan note: units 1 and 3 both read
   `sweep.json` + `resolve.cache_dir` and are naturally one tool (e.g. `resolve_brief.py` with
   `worklist` and `check` ops) — decide in the plan.*
2. **`brief_render.render_dossier`** — dossier data → verdict card (papered/conflict/verbal-only/
   silent → `system_voice` grade), lifted verbatim. Unit-tested over fixtures for each verdict.
3. **Completeness check** — a tested function/CLI: given `sweep.json` + `resolve.cache_dir`, return
   the set of flagged task ids with no dossier; the brief renders a loud INCOMPLETE line when non-empty.
4. **Wire deep-resolve to the worklist** — replace the buried gather.md prose with the enumerated
   worklist + per-task sub-agent dispatch, keeping the existing evidence-row / `resolve_verdict` /
   self-heal contract.

## Boundaries / non-goals

- **No new economic judgment.** Verdicts come only from the tested `resolve_verdict.py`; the model
  builds evidence and never sets a verdict. The gate still holds economic ships — this slice renders
  dossiers in the *brief* (read/advice surface), it does not ship anything.
- **Drive-byte fetch stays at brief time** (model+MCP) — the sweep does not pre-fetch bytes (A34's
  deferral holds). This slice is about the brief *consuming* the warm flags and resolving reliably.
- **A35 (header "N figures with no paper" count) is separate** — this slice renders the per-task
  dossiers + the completeness line; the all-domain header count remains A35.
- **Self-heal (semantic fallback → gate-proposed link add)** stays as already specified in gather.md;
  not expanded here (that scale-out is A38).

## Acceptance (what b2g compiles into the goal)

- `python -m pytest engine/tools/tests/` green (exit shown), including new tests for
  `brief_render.render_dossier` (papered vs conflict vs verbal-only vs silent) and the completeness
  check (all-resolved → OK; a missing dossier → the flagged id is reported).
- Fire the FO brief against a state with a flagged economic task in `sweep.json`: show the resolve
  step **fires** — a dossier is written to `resolve.cache_dir` and a verdict card appears in the brief
  body **rendered from the warm cache**, not skipped.
- Delete one dossier and re-render: the completeness check emits the loud INCOMPLETE line naming the
  unresolved task (shown) — proving a skip is caught, not hidden.
- A conflict case renders as `conflict` and a papered case as `papered` (shown).
- Moat (`queue_tx.py` / `lane_policy.py` core invariants) untouched.

## Review tier

Production-state / Paper-Governs (the brief's FO economic rendering) → the **review-gate workflow**
on the diff; block on confirmed CRITICAL/security-HIGH. No auto-shipped economic judgment.
