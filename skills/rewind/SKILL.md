---
name: rewind
description: The pipeline's universal UNDO — send a unit back a stage, undo a ship, or reconcile state/file desyncs; atomic, logged, itself revertible.
---

**§0 Resolve the install first:** `${CLAUDE_PLUGIN_ROOT}/engine/pipeline/RESOLVE-INSTALL.md` — markers first (walk up for `state/`+`profile/`), config second; only when BOTH fail is the install absent, and even then STOP with guidance, never auto-`/aios:setup`.

You are **rewind** — the one place a unit (or a whole process) gets *cleanly walked back*. Where
`gate` moves items forward and `revert {id}` undoes a single ship, rewind generalizes undo to
**every stage**: any item that desynced, stalled, or got rejected can be returned to a known-good
earlier stage and re-flowed. Contract: `${CLAUDE_PLUGIN_ROOT}/engine/pipeline/STAGE-CONTRACT.md`. Queue: `${CLAUDE_PLUGIN_ROOT}/engine/pipeline/QUEUE.md`.

Rewind is **not a forward stage** — it doesn't advance work, it repairs it. It rides the same
invariants as every stage (fact-free, self-contained, VERIFY gate, atomic-via-`queue_tx`,
context-log) and adds one of its own: **every rewind is itself revertible.**

# The enforced mechanism — `"${CLAUDE_PLUGIN_ROOT}/engine/tools/rewind.py"`

Implemented once in code (like `queue_tx.py` for forward writes) so no stage hand-rolls a half-undo:

| Op | What it does | Use when |
|---|---|---|
| `reset <queue> <ids> <to_stage> [reason]` | Walk items back to an earlier stage; clears every field a *later* stage introduced; appends history; snapshots the pre-image. | An item is stuck/wrong at its current stage and should re-flow from `sorted`/`captured`. |
| `undo-ship <queue> <id> <vault_root> <revert_dir> [to_stage] [--kb-map JSON]` | Remove the shipped vault file (from its revert pointer), return the item to `awaiting` (default). | A bad auto-ship needs pulling back (generalizes `revert {id}`). |
| `reconcile <queue> <vault_root> [--apply] [--kb-map JSON]` | Detect desyncs — `awaiting` w/ no staging draft → `sorted`; `shipped` w/ no vault file → `awaiting`. Dry-run unless `--apply`. | A run claimed work that isn't on disk (the 2026-06-20 "drafted 8, zero files" class). |
| `undo <queue> <snap_id>` | Restore every item in a snapshot to its exact pre-rewind state. | A rewind itself was wrong. |
| `list` | Show all snapshots. | Finding the `snap_id` to undo. |

Stages: `captured < sorted < awaiting < shipped` (+ terminal `rejected` / `reverted`).
`reset` preserves earlier-stage fields and strips later ones, so a rewound item is indistinguishable
from one that legitimately sits at that stage (e.g. `awaiting → sorted` drops
`first_drafted_utc` / `recommended` / `rec_reason`, keeps `conflict_key` / `lane` / `kb`).

# Run

1. **Diagnose.** Read the context-log tail + the queue (`queue_tx.load`). Identify the
   units to walk back and the *target stage* (where do they re-enter the pipeline cleanly?). For a
   suspected desync, run `reconcile` (dry-run) first — let the tool find them.
2. **Rewind.** Call the matching `rewind.py` op. It snapshots the pre-image to
   `<env_root>/state/rewind/{snap_id}.json` **before** committing, then commits the new queue through
   `queue_tx` (atomic: tmp + `os.replace`, under the advisory lock). A torn/invalid proposed queue is rejected, live untouched.
3. **VERIFY (gate).** Re-read the queue: `queue_tx validate` → OK; rewound items are at the target
   stage with later-stage fields gone; the snapshot file exists. For `reconcile --apply`, re-run
   `reconcile` (dry-run) and confirm the desync list is now empty. Any mismatch → ⚠, never report success.
4. **Log.** Append one `context-log.jsonl` line: `op`, `ids`, `from`/`to` stages, `snap_id`,
   `repairs`, `anomalies`.

# Guardrails

- **Every rewind is revertible.** One snapshot per op; `undo {snap_id}` restores the pre-image. This
  is the symmetric partner to "every ship is revertible by id."
- **Atomic + validated, never hand-rolled.** All queue writes go through `queue_tx` — rewind never
  raw-writes the queue (the clobber/torn-write fix applies to undo too).
- **Scope = the RESOLVED vault (production).** Writes the aios queue,
  `state/rewind/`, and the per-KB resolved vault — the **REAL vault** at `<vault>/<vault.live_kb_map[kb]>`
  for every KB in `vault.live_kb_map`. Resolve the vault root exactly as `gate` does (`vault` +
  `vault.live_kb_map`); a `kb` NOT in the map is an error — hold + flag, never invent a fallback vault —
  and pass that resolved root to `undo-ship`/`reconcile` **together with
  `--kb-map '<the vault.live_kb_map as JSON>'` (A19 — REQUIRED against the real vault: its folders
  are the mapped names, and an unmapped live-vault `reconcile --apply` would bounce every healthy
  legacy item)**. **NEVER** Notion / Drive / Memory. Because undo now touches real wiki pages, every op
  still snapshots its pre-image first (see "every rewind is revertible") — that snapshot is the undo path
  for a real-vault file too.
- **Self-heal vs. judgment (fix-then-tell).** Mechanical, deterministic desyncs (drafted-but-no-file,
  shipped-but-no-file) → `reconcile --apply` self-heals and reports. Anything touching an economic /
  ownership / Paper-Governs term, or any irreversible real-data move, is **not** auto-rewound — surface it.
- **Rewind is the lifecycle's REFLECT/recovery arm.** Plan→Build→Review→Ship can now fail *safely*:
  whatever a stage half-did, rewind walks back to a clean re-entry point. Stages that hit an
  unrecoverable state should leave the unit claimable and let rewind/reconcile clean up, rather than
  forcing a forward write.
