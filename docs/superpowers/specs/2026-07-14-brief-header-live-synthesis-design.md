<!-- ecosystem-check:exempt — this is a SUBTRACTION spec: it removes custom instant-paint/precompute machinery from the brief skill and moves an existing model synthesis from write-time to render-time. Nothing is being built or shopped for; there is no capability to source from the ecosystem. -->

---
type: spec
project: aios
topic: brief-header-live-synthesis
created: 2026-07-14
status: draft
tags: [brief, simplification, staleness, header, instant-paint]
---

# Brief header — kill the precomputed preview, synthesize live

## Problem

The wake-up brief opens with a "preview blurb" (`state/brief-headline.md`) — a rich,
model-authored narrative paragraph ("*Second straight quiet day on Notion, real growth in the
review queue…*") plus four count-chips, a Top item, and a First move. It is **precomputed to a
file and echoed verbatim as the trigger's instant first paint** (SKILL.md `# ⚡ FIRST`), then PASS 2
is supposed to rewrite it at the tail of each live gather (`references/gather.md` `## Cache
contract`).

**In practice it is chronically stale.** Observed 2026-07-14: `brief-headline.md` last written
06:59 (the ~7am scheduled flush) while an interactive brief walk ran at 09:44 (`brief-session.json`
mtime) **without rewriting the headline**. Seth fired the wake-up phrase three times across the day
and saw the same 7am paragraph each time; earlier runs showed a paragraph 3 days old. The
write-back that is meant to keep the blurb current does not reliably fire during at-desk runs, so a
*precomputed prose file is structurally guaranteed to drift out of sync with the brief it prefaces.*

**Root tension:** the rich paragraph is a *synthesis of the full gather* — it cannot be cheaply
regenerated in a fraction of a second the way a count can. So "instant paint" forced it to be
precomputed, and precomputing is exactly what makes it stale.

**Historical note (why the instant-paint existed):** it was built to stop the trigger hanging while
it chased out-of-mount `~/.aios/config.json` / engine tools before painting anything, under the
Cowork FUSE sandbox. That runtime is retired — AIOS is native-only now
(`Memory/aios-runtime-native-only.md`), there is no mount to starve on. The original reason for a
*precomputed* first paint is gone; all that remains is "show something so the terminal isn't silent
for a few seconds," which needs a static loading line, not a synthesized file.

## Decision

Seth (2026-07-14 brainstorm): **keep the header prose — it is the valuable part — but only ever
delivered fresh, synthesized from the actual data being shown.** Accept a few seconds' wait for the
full gather rather than an instant-but-stale paint. Keep a trivial loading line so the trigger
acknowledges immediately.

## Design

Three moving parts change; one is deleted.

### 1. Delete the precomputed headline artifact and the two-pass instant-paint machinery
- **Remove `state/brief-headline.md`** as a precomputed artifact and stop writing it. It is the
  stale-prose file at the center of the complaint.
- **Remove SKILL.md `# ⚡ FIRST — instant paint`** (the read-`brief-headline.md`-and-echo-verbatim
  block) and the PASS-1 "instant preview card" step. The "PASS 1 / PASS 2" two-pass framing
  collapses into a single flow: acknowledge → gather (or reuse a fresh cache) → render.
- **Remove the headline write-back** from `references/gather.md` `## Cache contract` (item 1).
  `headline_bubbles` (the count chips) stays in the JSON — it is cheap structured data the
  render-time synthesis and any chip line consume; only the standalone `.md` prose file dies.

### 2. First output is a trivial static loading line
The trigger's first emitted output becomes a single static line — no file read, no precompute, so
it **cannot go stale**:

```
🧭 Gathering your brief… (last run {age})
```

`{age}` is read cheaply from the `brief-session.json` mtime or `brief-cache.json` `generated_utc`
if present, else omitted — a nicety, never load-bearing. This replaces the "FIRST — instant paint"
determinism rule: the guaranteed-immediate first output is now this static ack, not an echoed file.

### 3. The header prose becomes a render-time synthesis, never a stored file
The same narrative synthesis Seth values is produced **at the moment the brief renders**, from the
exact data this brief is about to show — the freshly gathered payload, or a `brief-cache.json` that
is within its freshness window. Because it is synthesized from the data being displayed, it **cannot
be out of sync with the brief itself.**

- Model-authored (as today), not an engine renderer op — it is a voice synthesis, not a
  deterministic card. The count-chips line under it may still be lifted from `headline_bubbles`.
- Inputs it narrates (theme / what changed / Top / First move) all already exist in the cache
  payload and the gather; nothing new to compute.
- The `cache-status` fresh/stale/degraded logic in `brief_session.py` is **retained** — it still
  governs whether PASS 2 re-gathers or renders from a recent cache. Only the *headline-file* branch
  of the old two-pass paint is removed.

### 4. Keep `brief-cache.json` / `brief-cache.md` unchanged
The structured cache is the legitimate source of truth for the render, has a real freshness window
(minutes-old counts are fine — it was never the thing that felt "3 days old"), is `validate_cache`-gated,
and serves scoped briefs. It stays. This spec touches **only** the prose-preview layer.

## Resulting flow

```
"Wake up. Daddy's home."
  → 🧭 Gathering your brief… (last run 2h ago)          [instant, static, cannot be stale]
  → [cache-status: gather live, or reuse a fresh cache]  [seconds]
  → **{fresh narrative synthesis}** + count chips        [render-time, from THIS data]
     {ranked items / stationed walk, unchanged}
```

## Non-goals / YAGNI
- Not fixing the write-back (the thing that failed) — we delete the artifact that needed it.
- Not touching the stationed walk, gate, resolve, settle, or the structured cache.
- Not adding a scheduled cache-writer (the retired precompute stays retired).
- No engine-renderer op for the header — it stays a model synthesis.

## Downstream docs that must change with this (implementation will enumerate exact edits)
- `Projects/aios/skills/brief/SKILL.md` — delete `# ⚡ FIRST`; rewrite `## Render flow` (two-pass →
  single flow with the loading line + render-time synthesis); drop headline references in
  `## Cache contract` and `## Surface`.
- `Projects/aios/skills/brief/references/gather.md` — remove `brief-headline.md` from the
  `## Cache contract` write list (keep `headline_bubbles`).
- Env-root `CLAUDE.md` — rewrite the **"Trigger determinism (load-bearing)"** rule (currently
  "FIRST output is always the headline card read from `state/brief-headline.md`, echoed verbatim")
  to describe the static loading line + live synthesis. (Env-ops pointer, not owned here.)
- `Memory/brief-front-door-decision.md` — append the 2026-07-14 change.

## Acceptance
- `grep -rn "brief-headline" Projects/aios/skills/ Projects/aios/engine/` returns **no** live
  code/instruction references (only historical/spec mentions) — shown in chat.
- Firing the wake-up phrase emits the static `🧭 Gathering your brief…` line first, then a header
  paragraph whose `as of` freshness matches the current run (not a prior day) — shown in chat.
- No `state/brief-headline.md` write occurs during a brief run (`ls -la` before/after shows
  unchanged/absent) — shown in chat.
- The full brief (stationed walk, held panel, resolve) renders unchanged from `brief-cache.json`.
