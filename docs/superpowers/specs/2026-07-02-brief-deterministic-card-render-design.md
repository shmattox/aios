# Deterministic brief card render — design

**Date:** 2026-07-02
**Status:** Approved (design) → hand off to writing-plans
**Scope:** aios engine + brief skill + brief precompute. Reference implementation of a general enforcement doctrine (see §8); the doctrine's rollout to other skills is out of scope here.

---

## 1. Problem

The daily brief silently drops parts of its per-item card — most visibly the two-layer
recommendation block (`🔵 Your system says …` / `🟠 Claude …`, the "system vs. Claude adds"
section). When challenged mid-brief, the model concedes ("you're right, I dropped the card
format") and reformats ad hoc. Observed across **desktop, laptop, and Cowork** — i.e. it is not a
machine or install artifact.

## 2. Root cause

The card format is **prose the model reproduces from memory**, and it passes through **two
independent LLM stages**, either of which can drop it:

1. **Precompute** (`deploy/tasks/brief-cache.md`) — a background LLM that gathers records and
   *writes* `brief-cache.{md,json}`. Card layout is at its discretion.
2. **On-trigger brief** (`skills/brief/SKILL.md`) — a second LLM that reads the cache and
   *re-renders* to chat per the prose render contract (SKILL.md:275–283, 355–364). Layout is at its
   discretion again.

The only format in the brief that never breaks is the widget review panel — because it is a
**tracked template file (`templates/review-panel.html`) lifted verbatim** (SKILL.md:445), not a
format reproduced from prose. The default conversational card has no such artifact.

The skill already *asserts* the fix in principle — line 273: **"the engine renders, never
re-grades"** — but no engine renderer exists. Both stages free-hand the markdown. The existing
`VERIFY step` (SKILL.md:285) validates the **input cache**, never the **rendered output**, so per the
env's own gate doctrine ("a run with no gate is a bug") the brief's gate is pointed at the wrong end
of the pipe.

Load-bearing discovery: the structured data and its validator **already exist**.
`brief-cache.json` carries per-item `system_voice` (`{grade, text, cite}` or null for Grade 0) and
`claude_voice.text`, and `brief_session.py validate_cache` (lines 327–401) already rejects any item
missing them. What is missing is only the **renderer** that turns that validated JSON into the card
markdown.

## 3. Goals / non-goals

**Goals**
- The two-layer block, and the full per-item card layout, **cannot be dropped** — enforced
  structurally, not by prose or by model self-discipline.
- Identical card output across every surface (native `/aios:brief`, Cowork, both machines).
- The graded-voice styling (Grade 1 solid / 2a dashed / 2b faint / 0 omitted, SKILL.md:263–283) is
  produced deterministically from the `system_voice.grade` field.

**Non-goals**
- No change to *what* the brief decides or grades — model judgment still populates the data.
- No cross-skill enforcement framework (that is the scope-2 build; here we only plant the doctrine).
- No change to the interactive button mechanism beyond tying it to the rendered layers.
- No change to the queue/gate moat.

## 4. Architecture

One source of truth (JSON), one renderer, verbatim lift:

```
precompute LLM ──writes──▶ brief-cache.json     (DATA + judgment; validate_cache gates completeness)
                                  │
                       brief_render.py           (NEW — pure fn: json → card markdown; no LLM/network)
                                  │
     trigger brief LLM ──echoes VERBATIM──▶ chat card body  +  host-native A/B/Other buttons
```

The LLM is confined to what only an LLM can do: populate structured judgment into the JSON, and
drive interaction. It never reproduces a must-hold format from prose.

## 5. Components

### 5.1 `engine/tools/brief_render.py` (new)
- Pure function `render_card(item) -> str` returning the per-item card markdown: title (verbatim) +
  domain tag, `Urgency`, `Your playbook`, `Flags`, then the two-layer block.
- The `🟠 Claude` line is **always** emitted from `claude_voice.text`. The `🔵` line is emitted per
  `system_voice.grade` using the exact table at SKILL.md:263–283; Grade 0 / null emits the
  `— your system is silent —` row and no blue line.
- CLI surface for the stationed walk: `brief_render.py station <cache.json> <station>` (emits all
  cards for one station) and `brief_render.py card <cache.json> <item_id>` (single item). Keys match
  `brief_session.py`'s station model.
- Deterministic, no network, no LLM. Pure `json → str`.

### 5.2 `skills/brief/SKILL.md` rewire
- The Stage-2 per-domain render step (SKILL.md:238–247) and the "Render — per item" fences change
  from *"render per this format"* to: **call `brief_render.py station …`, emit its output verbatim,
  then attach the A/B/Other buttons** where **A** ← the system layer and **B** ← the Claude layer.
- The per-item layout fences and the graded-voice table remain in the skill **only as human
  reference**, explicitly no longer do-it-yourself instructions (a comment marks them as mirrored
  from the renderer).

### 5.3 `deploy/tasks/brief-cache.md` (precompute) rewire
- Contract becomes: *"populate the complete structured `brief-cache.json`; do NOT hand-author card
  markdown."* `validate_cache` remains its exit gate (fail-loud on incomplete items).
- `brief-cache.md` becomes a **generated artifact** — derived from `brief-cache.json` by
  `brief_render.py` — so the markdown has one source of truth instead of being separately
  LLM-authored. (Recommended; if deferred, `brief-cache.md` is left as-is and only the live render
  path is made deterministic.)

## 6. The seam — card body vs. buttons

The renderer emits the **static card body** verbatim (title / urgency / playbook / flags / 🔵🟠).
The skill appends the **host-native A/B/Other buttons**, which cannot be pre-baked (they are host
"ask the user" affordances that open the item's thread). The determinism guarantee covers the body
— the thing that was drifting. The buttons remain host affordances but are structurally derived
1:1 from the two rendered layers, so "two layers present" implies "two framed options present."

## 7. Enforcement (three legs) & testing

**Enforcement**
- (a) `validate_cache` rejects any item missing `claude_voice.text` or a valid `system_voice` →
  brief not presented. *(already exists)*
- (b) `brief_render.py` is the **sole** producer of card markdown → format cannot drift. *(new)*
- (c) a golden-file test locks the renderer's output. *(new)*

A card cannot render without both layers.

**Testing**
- Unit tests for `render_card` across all four grades (1 / 2a / 2b / 0) + the missing-cite edge
  (2b cite optional; 1/2a cite required).
- Golden-file test on a fixture cache (`render station` output byte-stable).
- Negative `validate_cache` test: an item missing `claude_voice.text` → `INVALID`.
- Adds one module to the existing 9-file suite; `python -m pytest engine/tools/tests/ -q` stays
  green.

## 8. Doctrine (scope-1 seed only)

Record in `Memory/decisions.md` + a one-line rule in the appropriate `CLAUDE.md`:

> **A must-hold rendered format is emitted by a deterministic engine renderer and lifted verbatim;
> skills never reproduce a must-hold format from prose. Model judgment populates structured data;
> the engine renders it.**

Rollout to other skills is opportunistic via `env-maintenance`, one skill at a time — not part of
this build.

## 9. Open items to resolve in planning
1. **Field nesting:** a sampled item carries `system_voice`/`claude_voice` under `recommended`,
   while `validate_cache` reads them at item level (lines 381/385). Reconcile the renderer +
   validator against the real schema before coding; the brief currently passes validation, so
   confirm which path is live.
2. **`brief-cache.md` generated vs. LLM-authored:** adopt renderer-generated now (recommended) or
   defer.
3. **Station keys:** confirm `brief_render.py` station identifiers match `brief_session.py`'s
   station model exactly (KB → System → Personal → Family Office → Dev; scoped walks render only
   the scope's stations).

## 10. Acceptance
- `brief_render.py` exists; `render station`/`card` emit the full card incl. the two-layer block for
  every grade; output is byte-stable against a golden fixture (run and shown).
- `skills/brief` and `deploy/tasks/brief-cache.md` call the renderer / echo verbatim; no do-it-
  yourself card-format prose remains on the live render path.
- `validate_cache` negative test rejects a missing `claude_voice.text`; suite green at 10 files
  (exit codes shown).
- A live brief run renders every item with the `🔵/🟠` block present (shown).
- Doctrine entry written; fresh-context review of the change reports zero CRITICAL.
