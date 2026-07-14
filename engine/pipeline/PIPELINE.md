# Pipeline — capture & keep-fresh (the engine's other half)

The brief is the **read-&-judge** half; this is the **capture-&-keep-fresh** half. It runs
continuously so {{ENTITY_NAME}} just **reviews**. Five stages, one queue (`QUEUE.md`), bound by the
four-phase lifecycle. Lean/native: `SKILL.md` + scheduled tasks + a JSON queue + git. No custom software.

## Stages

| # | Stage | Skill | Parallel? | Human in loop? |
|---|---|---|---|---|
| 0 | Capture-router | `tools/capture_router.py` (the scheduled `aios-capture-router` task) | yes — per source | no |
| 1 | Inbox-capture | `skills/inbox-capture` | yes — per-source adapters (X · bookmarks · email · WhatsApp · …) | no |
| 2 | Sort | `skills/sort` (runs in the `aios-ingest` Stage A) | yes — per item | no |
| 3 | Ingest (Phase A) | `skills/ingest` | yes — per-item draft (fan out N drafting agents) | no |
| 4 | Gate — review & ship (Phase B) | `skills/gate` | yes across independent items; **serialize on `conflict_key`** | **review lane only** |
| 5 | Garden | `skills/garden` | no — whole-vault, single pass | approves proposals |

**Recovery (cross-cutting, not a forward stage):** `skills/rewind` (+ `tools/rewind.py`) is the
pipeline's universal undo. Any stage that goes wrong, stalls, or gets rejected is walked back to a
clean re-entry point — `reset` a unit to an earlier stage, `undo-ship` a bad ship, or `reconcile`
state/file desyncs — each atomic (via `queue_tx`) and itself revertible (a snapshot per rewind).
This is the REFLECT/repair arm of the four-phase lifecycle: Plan→Build→Review→Ship can now fail
*safely*. Referenced here, defined in its SKILL.md (don't restate).

**Built now: the spine** = the queue contract + Stage 4 (`gate`) + the context log + the
discipline rules below. Stages 1, 2, 3, 5 are next — generalized from `Scheduled/unified-capture`,
`inbox-autosort`, `ingest-phase-a`, and `doc-refresh` (proven machinery, de-personalized).

## Parallelism (multiple sub-agents per stage)

Native via agent-teams (already enabled in the env). The queue is the coordination layer: workers
**claim/lease** items and **serialize on conflict-keys** (QUEUE.md). Fan out drafting + review across
independent items; keep **Garden** single-pass (it touches everything). Parallelism is a property of
the queue, never a per-skill hack — that's what keeps fan-out from reproducing the 2026-06-11 clobber.

## Context log — the system's self-awareness (`state/context-log.jsonl`)

One append-only line per stage-run:

```jsonc
{"ts":"...","stage":"gate","run_id":"...","items_in":12,"items_out":9,
 "auto_shipped":7,"held":2,"rejected":0,"repairs":["rebuilt dev queue from drafts"],
 "anomalies":[],"duration_ms":8400}
```

Every stage reads the recent **tail** at run-start (the way {{ENTITY_NAME}} reads `memory.md` at
session-start) so the pipeline knows its own recent history. Plain, cheap, readable by both the
system and the human.

## Self-heal & VERIFY — see the Stage Contract

Self-heal (fix-then-tell), the VERIFY gate, atomic writes, fact-free, self-contained,
re-sync-on-edit, and the context-log append are the seven invariants every stage obeys — defined
once in `STAGE-CONTRACT.md`.
Generalizes the env's 2026-06-18 integrity rule + `brief-spec §6`.

## Recursive self-improvement = the pipeline pointed at itself (design intent — receiving rail built, producer not yet)

The intended shape: the **context log is the capture** — when a pattern shows (a skill's drafts keep
getting rejected, a stage keeps repairing the same thing, a step is consistently slow) the pipeline
would **draft a proposed skill edit** and route it through the **same `gate`** as any other item
(`source:self`, `lane:review`, `conflict_key:` = the skill file); {{ENTITY_NAME}} approves; the ship
records a revertible unit (a `rewind.py` snapshot / revert pointer — not a git commit; git is an
optional history layer), and `revert {id}` undoes a bad self-edit.

**Build status (honest):** the RECEIVING rail exists and is enforced — `gate` holds any `source:self`
item for explicit human approval (never auto-ships), and `revert` undoes it. What does NOT yet exist is
the PRODUCER: no tool mines the context-log for rejection/repair patterns to draft those proposals, so
today `source:self` items only arrive if a human/skill authors one. This is the **eventual
pattern-learning layer** (`lane_policy.py` labels it the same way), not a shipped capability — wiring a
real producer is a separate future backlog item.

> **HARD RULE (already enforced by the gate):** the system **proposes** improvements to its own skills;
> it **never silently rewrites its own behavior.** Any self-modification rides the human-approval gate —
> the same discipline as a Paper-Governs write. For something that touches money, that's the only safe setting.

## Orchestration & cadence (the overnight chain)

Each stage is its own scheduled task, chained overnight so the morning brief reads a fresh queue.
Heavy automation runs overnight; peak/interactive hours are reserved (env timing convention).

| Stage | ~cadence |
|---|---|
| `capture-router` | nightly, BEFORE inbox-capture (~01:15) — bridges `00_Inbox/auto/` → `{kb}/raw/inbox/` (B-G19) |
| `inbox-capture` | nightly ~00:00 |
| `sort` | folded into `aios-ingest` Stage A (right after inbox-capture) |
| `ingest` (Phase A) | nightly ~02:00 |
| `gate` — auto-ship + confirm-timeout | nightly ~03:00 (unattended; writes a revert pointer per ship) |
| `gate` — review lane | on-demand (your approval, surfaced in the brief) |
| `garden` | weekly |

**Re-sync + VERIFY** are Stage Contract invariants (`STAGE-CONTRACT.md` #6, #3): after editing a
stage's `SKILL.md`, re-sync it to its task; every stage VERIFYs before reporting success.

## Multi-day away

The pipeline keeps running on schedule, accumulating `awaiting` items. A longer absence = a longer
review queue when you return, surfaced in the brief's Phase A review panel. Nothing is lost; nothing
auto-decides a review-lane item in your absence.
