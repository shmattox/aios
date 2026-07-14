# Stage Contract — the rules every pipeline stage obeys (defined once)

Every pipeline skill (`inbox-capture`, `sort`, `ingest`, `gate`, `garden` — and the `brief` on its
read side) obeys this contract. Skills **reference** it in one line instead of restating it, so the
rules can't drift apart — they already did once (the lane list and the `recommended` enum diverged
across files written minutes apart). "Define once, reference everywhere" — the env's own rule,
applied to itself.

## The seven invariants

1. **Fact-free.** A stage's SKILL.md contains zero facts about any person. Every title, number, id,
   rule, and opinion comes from the profile, the queue, or the nodes — never the skill file.
2. **Self-contained.** A scheduled run is a fresh session with no memory of prior runs. Each skill
   carries its own constants, adapters, and failure modes; it reads the context-log tail for history.
3. **VERIFY gate.** Before reporting success, re-read everything written, parse any JSON, confirm
   intended-vs-actual counts. Any mismatch → post the ⚠ variant and **never report success**.
4. **Atomic validated write — via `tools/queue_tx.py`, never by hand (G6; single-file A4).** A stage
   authors ONLY the items it touches, then commits them through the helper's primitives:
   `add` (new items, dedupe-fenced) / `update` (existing items). These are **targeted** — they load
   the single queue file (`<install>/state/queue.json`), change only the named items, validate the
   full set, and land it atomically (tmp + `os.replace`, under the `queue.json.lock` advisory lock).
   A stage NEVER raw-writes the queue file — so a sloppy/torn write is **rejected, not committed**.
   Reads use `queue_tx.py select`/`load()`. Bulk whole-queue replace (`commit`) is
   operator-only — never the daily-stage path.
5. **Self-heal (fix-then-tell).** On a torn/corrupt state file, rebuild it from the source of truth
   (item `payload_path` drafts → queue), proceed, and **report the repair**. Fail loud only when
   nothing reconstructable survives.
6. **Re-sync on edit.** A registered scheduled task holds its OWN snapshot of the prompt — after
   editing a stage's SKILL.md you MUST re-sync it to the task, or the next run runs the stale snapshot.
7. **Context-log (write).** Append one line to `state/context-log.jsonl` at the end of each run —
   `ts · stage · run_id · counts · repairs · anomalies · note`. This is the substrate for both
   self-awareness and self-improvement. (The read side — checking the tail at run-start — is
   invariant #2.)

## Glossary — the canonical vocabulary (one name per thing)

- **Stages** (item lifecycle, closed set): `captured → sorted → awaiting → shipped`, plus
  terminal `rejected` / `reverted`. `auto-ship` / `confirm` / `review` are **lanes, not stages**.
- **Lanes** (risk): `auto-ship`, `review` (base) + `confirm` (opt-in per profile). The canonical
  definition of which item-types fall in each lives in `QUEUE.md §Lanes` — **reference it, never
  re-enumerate.**
- **The clock:** `first_drafted_utc` — the one timestamp the confirm-timeout measures from (not
  `first_seen_utc` / `captured_utc`; one field, one name).
- **`conflict_key` is canonical** for routing & serialization; `kb` / `domain` are conveniences
  derived from it. If they disagree, `conflict_key` wins.
- **The review surface** is the brief's **review panel** (one name; not "approval panel").
- **`recommended`** ballot enum: `approve | hold | reject` (+ `rec_reason`).
