# Brief — deterministic entry (instant-paint hard gate)

**Date:** 2026-07-03
**Status:** implemented
**Sibling:** `2026-07-02-brief-deterministic-card-render-design.md` (that one made *render* deterministic; this makes *entry* deterministic).

## Problem

The ritual phrase ("Wake up. Daddy's home.") fired the `aios:brief` skill correctly, but the
brief went silent doing plumbing and never painted the instant headline card — slow and
non-deterministic. Observed in a Cowork-sandbox transcript: two turns of tool calls, no card.

### Root cause — an ordering contradiction in `SKILL.md`

- `# 0 — Resolve the install (every run, first)` told the model to read `~/.aios/config.json`,
  resolve `env_root`, and locate the plugin engine tools **first**.
- `PASS 1` told the model to echo `brief-headline.md` "before any other tool call or reasoning."

These contradict on which is "first." Section 0 (numbered *0*, labeled *"every run, first"*) won.
That ceremony is exactly what breaks in the sandbox:
1. `~/.aios/config.json` lives at the Windows user path — **outside the mount** → hunt.
2. Engine tools (`brief_render.py`, `brief_session.py`) live in the plugin bundle — **outside
   the mount** → unreachable → more hunting.
3. A staleness check on a 29h-old cache → more chasing.

By the time the ceremony resolved, everything was buffered and the card never emitted. The
instant-paint that hides the slow gather got starved by the plumbing in front of it.

**Key insight:** PASS 1 needs *zero* plumbing. The headline card is a ~1.3KB local file at
`state/brief-headline.md` under env_root; echoing it needs no config read, no engine tool, no
staleness check. The fix is to force the echo as the mechanically-first action and push all
resolution to PASS 2 (where the slow gather already hides behind the card).

## Design

Prose hard-gate (mirrors yesterday's forced `brief_render.py` template — forcing applied to
entry instead of render). No new infra. Escape hatch: fall through to live gather only if the
cache file is absent.

### Edits (all to source-of-truth, never generated files)

1. **`Projects/aios/skills/brief/SKILL.md`** — new `# ⚡ FIRST — instant paint` hard gate above
   `# 0`. Forces: resolve `env_root` cheaply (cwd, else walk UP to the first parent containing
   `state/brief-headline.md` — no `config.json`, works from env root or any `Projects/` subfolder);
   echo `state/brief-headline.md` verbatim as first output + "…pulling the full brief"; FORBIDDEN
   before the echo = config read, engine-tool lookup, staleness check, Notion delta-check; fall
   through to Section 0 only if the file is absent. Section 0 retitled to
   `(PASS 2 — only after the instant card is on screen)` so nothing else claims "first."
2. **`CLAUDE.md`** (env root) — one-line "Trigger determinism (load-bearing)" pointer under the
   "Wake up. Daddy's home." paragraph.
3. **`engine/templates/CLAUDE.template.md`** — same forcing line under the "Brief launch (the
   wedge)" block, so every generated orchestrator CLAUDE.md inherits it. (The setup skill
   generates CLAUDE.md from this template; editing the template is the portable fix.)

### env_root resolution (PASS 1, config-free)

cwd if it contains `state/brief-headline.md`, else walk up parents to the first that does. Works
in the Cowork sandbox (cwd IS env_root), native-from-root, and native-from-subfolder (scoped
briefs fired inside `Projects/<name>`). Never blocks first paint on the out-of-mount `config.json`.

## Backlogged (not shipped)

Native `UserPromptSubmit` hook that injects the headline deterministically — higher-in-the-order
hardening for the native path, but new infra and won't fire in the Cowork sandbox, so it's a
follow-up, not the fix.

## Verification

- `state/brief-headline.md` exists, 1326 bytes — the tiny instant-paint payload PASS 1 reads.
- It sits at `<env_root>/state/`, so the walk-up resolution is a direct hit at env root.
- SKILL.md gate and PASS 1 are consistent (gate is the forcing version; PASS 1 elaborates).
