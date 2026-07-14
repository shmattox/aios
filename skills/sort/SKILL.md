---
name: sort
description: Stage 2 — route each captured queue item to its KB, set conflict_key and a provisional lane; mechanical metadata routing only, no drafting or file moves.
---

**§0 Resolve the install first:** `${CLAUDE_PLUGIN_ROOT}/engine/pipeline/RESOLVE-INSTALL.md` — markers first (walk up for `state/`+`profile/`), config second; only when BOTH fail is the install absent, and even then STOP with guidance, never auto-`/aios:setup`.

You are Stage 2 (`${CLAUDE_PLUGIN_ROOT}/engine/pipeline/PIPELINE.md`). Take `captured` queue items and route them —
mechanical classification of queue metadata only. No drafting.

> **Scope (reconciled 2026-06-30, B-G19).** Sort operates on the QUEUE, not the filesystem. The
> `00_Inbox/auto/ → {kb}/raw/inbox/` file move (the old `inbox-autosort` job) is now its own
> upstream stage — `capture_router.py` / the scheduled capture-router stage — which runs BEFORE capture and
> owns husk lifecycle (delete-independent move + best-effort husk delete). Sort no longer touches
> `auto/` husks or moves raw files; doing so was dead code (the scheduled runtime, `aios-ingest`
> Stage A, only ever set conflict_key + lane). This skill is the canonical spec for that runtime.

# Run — the tables are code (A25)

The sort tables live in `${CLAUDE_PLUGIN_ROOT}/engine/tools/sort.py` — type→conflict_key path,
kb→lane proposal + economic-signal escalation + review_gates finalization (via `lane_policy`),
the `type: session-record` pre-key pass-through (the carried `conflict_key` is canonical, used
verbatim), and the daily-note collision check. **Run `sort.py run` ONCE over all `captured`
items** (all args from the profile); it flips every routable item to `sorted` atomically and
prints a `needs_judgment` list (raws with no routable declared type):
```
python "${CLAUDE_PLUGIN_ROOT}/engine/tools/sort.py" run --queue "<env_root>/state/queue.json" \
  --vault-root "<vault>" --kb-map '<the profile's vault.live_kb_map as JSON>' \
  --auto-ship-kbs '<the profile's gate.auto_ship_kbs as a JSON list — absent means []>' \
  --review-gates '<the profile's domains.yaml review_gates map as JSON, if set>'
```

**Your judgment covers ONLY the `needs_judgment` residue.** Classify each from its excerpt
(person / organization / software / concept / source …), pick the wiki target, then finalize
through the tool — the lane decision stays the tool's:
```
python "${CLAUDE_PLUGIN_ROOT}/engine/tools/sort.py" one --queue "<env_root>/state/queue.json" \
  --vault-root "<vault>" --kb-map '…' --auto-ship-kbs '…' --id <id> --ck "{kb}/wiki/<type>/<slug>.md"
```
Never hand-set a lane or hand-flip a stage.

**VERIFY:** the run summary's `sorted + needs_judgment` covers every `captured` item; every
finalized item carries a non-null `conflict_key` + `lane`.

# Discipline

- Obeys the **Stage Contract** (`${CLAUDE_PLUGIN_ROOT}/engine/pipeline/STAGE-CONTRACT.md`).
- Stage-specific: no decisions beyond routing; no drafting; no filesystem moves (that is the
  capture-router's job, upstream). Cadence: with Ingest (`aios-ingest` Stage A), right after capture.
