# Settle loop — close the Notion write-loop via a settle-first brief

**Date:** 2026-07-08
**Status:** Design approved — ready for implementation plan
**Repos touched:** `aios` (engine) + env-ops (Windows task runner)
**Backlog homes:** AIOS `Projects/aios/BACKLOG.md` (reconciler, settle station, write closure, cleanups); env-ops `Documents\Claude\BACKLOG.md` (runner exit-code + freshness alarm)

---

## 1. Problem

AIOS keeps **two systems of record**, and only one self-heals:

| System of record | Kept current by | Health |
|---|---|---|
| **Vault** (knowledge) | capture → ingest → gate, nightly | ✅ closed loop |
| **Notion** (operational state — tasks, dashboards) | **only manual brief-walk decisions** | ❌ drifts |

`ship.py` (the gate) writes the **vault page + revert pointer + queue flip** — it does **not** touch Notion (despite `gate/SKILL.md`'s header claiming "wiki/Notion truth"; `gate-auto` explicitly writes "nothing under Notion/Drive"). The **only** Notion writer is `notion_writeback.py`, fired **only** when the user decides an item inside a brief walk. So any work completed elsewhere — dev sessions, payments, out-of-band tasks — produces git / session-capture / shipped-page evidence but **never flips its Notion task**.

Consequences observed 2026-07-08:
- `resolve-sweep` reports "zero task deltas for the third straight day" (Notion stale at source).
- The morning brief re-proposed already-progressed work (SEAMS-1065) because the gather trusts Notion open-status as truth; it only reconciled when the user drilled into the item (the *thread* reads git/ledger; the *gather* does not).
- Separately, `aios-brief-cache` **silently failed** that morning: it hit `max_turns=80` (exit 1) before writing the cache, but the Windows runner reported `0x0` to Task Scheduler, so nothing alarmed. The stale cache forced a slow mid-conversation live gather.

**Root cause:** the Notion write-loop is only closed for in-brief decisions; nothing reconciles out-of-band completions back to Notion. Reconcile-at-read alone ("filter done work at render time") is a band-aid — it heals the *view* while Notion, the actual system-of-record the dashboards read, stays permanently wrong.

## 2. Goal

Retrieval that is **instant and already accurate**: a reliable background precompute that also **settles tasks** — detects completions across all evidence sources and closes the Notion loop — so the brief opens on a current picture and Notion stays live.

## 3. Locked design decisions (from brainstorm 2026-07-08)

- **Auto-heal boundary:** deterministic replay (a decision already made whose write didn't land) auto-heals in the background, fix-then-tell; everything **inferred** waits for a one-click confirm at the desk. (Matches CLAUDE.md fix-then-tell vs. fail-loud-and-wait.)
- **Evidence scope:** full-fat from day one — walk-ledger, session-capture, git commits, **and** Drive/dataroom completion signals.
- **Placement:** fold the reconciler into the fixed `aios-brief-cache` precompute (one model run, one cache contract); the deterministic ledger↔changelog diff is a called script, the model does only the fuzzy matching.
- **Settle vocabulary:** three transitions the model proposes per candidate — `→ Done`, `→ In Progress`, `→ Due rolled` — each with cited evidence; the human confirms/adjusts/skips.
- **Surface:** a new **Stage 0 — Settle**, run *before* the KB station, so stale done-work is cleared before the brief proposes any new move.

## 4. Phases (one spec, phased implementation plan)

### Phase A — Unmask the silent failure (env-ops / runner) — do first
The safety net for everything else.
- The Windows task runner propagates the inner `claude -p` exit code to Task Scheduler instead of always reporting `0x0`.
- After `aios-brief-cache` runs, assert `state/brief-headline.md`'s mtime advanced past the run start. If not, append a `brief` anomaly line to `state/context-log.jsonl` so `pipeline_health.py` surfaces it in the next headline. (A run that writes nothing currently leaves no trace — this gives it one.)
- **Build note:** verify whether the runner lives in `aios/deploy/windows` (→ AIOS) or native `Scripts/` (→ env-ops) and route the change accordingly.

### Phase B — Reliable live precompute (AIOS)
So the cache is fresh and the reconciler has room to run.
- Raise `aios-brief-cache` `max_turns` 80 → 150 (the gather already blew 80; the settle pass adds work).
- Route the precompute's Notion leg through `notion_gather.py` (live headless, exactly as `resolve-sweep` already does — proven this morning reading 71 tasks live) so the cache is `notion_live:true` and the at-desk brief renders from it on the fast path instead of re-gathering.
- Reconcile the manifest drift: `aios-brief-cache` + `aios-resolve-sweep` are `enabled:false` in `deploy/tasks.manifest.json` but live in Task Scheduler — set `enabled:true` to match reality (we now depend on them).

### Phase C — Settle reconciler + Notion write closure (AIOS) — the feature

**Inputs (full-fat):**
- Open Notion tasks across all silos (already gathered by the precompute via `notion_gather.py`).
- Completion evidence: walk-ledger (`state/brief-session.json` `executed` decisions), `state/notion-changelog.jsonl` receipts, session-capture records (`raw/sessions/`), recent git commits (read-only `git log`, as session-capture already reads), Drive/dataroom completion signals.

**Two classes:**

1. **Deterministic auto-heal — `settle_reconcile.py` (called script, no model).**
   Diff walk-ledger `executed` decisions against `notion-changelog.jsonl`. An executed decision that recorded an **intended** Notion write but has no matching receipt = a write that didn't land, with a **known** target. The script fires `notion_writeback.py flip` headlessly (token from env/Credential Manager, same path as `notion_gather`), logs the receipt, fix-then-tell.
   - **Honest dependency:** this class is only reliable once the in-brief loop *records write-intent* on each executed decision (see the third piece below). Until then it is best-effort and most completions flow through the inferred class.

2. **Inferred candidates — the precompute model.**
   Given the open-task list × the day's completion evidence, the model proposes matches. Each candidate:
   ```
   { task_id, title, proposed_transition: "done"|"in_progress"|"due_rolled",
     evidence: [ {source, ref, quote} ], confidence, domain }
   ```
   **Precision posture:** surface a candidate *only* with a concrete cited anchor (a commit / record / doc that names or clearly maps to the task) — recall-biased toward surfacing *with evidence*, never a bare guess. **None are written in the background** — all wait for at-desk confirm.

3. **Tighten the in-brief write loop.** When a brief decision's action implies a Notion change, the write fires immediately (already the pattern) **and the ledger records the write-intent** on the `executed` decision — so the deterministic pass (class 1) can detect any receipt that failed to land. Build will verify current leakage by diffing the ledger's `executed` set against the changelog.

**Output → cache contract.** New `settle` block in `brief-cache.json`:
```json
"settle": {
  "auto_healed": [ { "task_id": "...", "title": "...", "transition": "done",
                     "receipt_ref": "...", "evidence": [ ... ] } ],
  "candidates":  [ { "task_id": "...", "title": "...", "proposed_transition": "in_progress",
                     "evidence": [ {"source":"git","ref":"<hash>","quote":"..."} ],
                     "confidence": "high", "domain": "dev" } ]
}
```
Plus a `headline_bubbles` chip: `N settled · M to confirm`. `validate_cache` extended to check the block's shape.

**Stage 0 — Settle station (render + act).**
- Seeded ahead of KB in the walk order (`settle,kb,system,personal,familyoffice,dev` at root; scoped walks prepend `settle` to their own order).
- Rendered deterministically by a new `brief_render.py settle` op (never hand-composed):
  - **Auto-heal summary:** `✅ Healed {N} stale Notion writes: {task} → {transition}` — one line each, fix-then-tell.
  - **Candidate batch panel**, grouped by proposed transition (mirrors the KB Stage-1 batch pattern):
    ```
    ▸ 4× → Done          e.g. "SEAMS 1065 …", …    [Confirm all] [Expand]
    ▸ 2× → In Progress    …
    ```
    Per-row on Expand: `current status → proposed`, the cited evidence, buttons **Confirm** (write) · **Adjust** (different transition) · **Skip** (leave as-is).
- On **Confirm** → `notion_writeback.py flip` (Status/Due), act-then-tell, receipt to `notion-changelog.jsonl`, and `record_decision … settle …` in the walk ledger. When the station is cleared, `advance` to KB.

## 5. Safety / discipline (load-bearing)

- **Content gate intact.** `notion_writeback flip` targets only status/select/checkbox/date and refuses economic/number/relation fields *by type* — a settle write can never touch a dollar/ownership term. A completion that implies an economic write surfaces as a normal economic card, not a settle.
- **Fail-loud boundary.** Inferred completions never auto-write; only deterministic replay auto-heals. This *is* the fix-then-tell vs. fail-loud rule.
- **Reversible.** Notion page history + the changelog receipt; a status flip is trivially undone. A false match costs a *skipped row*, not a wrong write.
- **Read-through-helper discipline** unchanged: queue via `queue_tx.py`, Notion via `notion_gather`/`notion_writeback`, never ad-hoc MCP or raw file writes.

## 6. Testing

- `settle_reconcile` deterministic diff (ledger `executed` × changelog) — fixtures for hit / miss / no-intent.
- `brief_render settle` op — golden output for auto-heal summary + grouped candidate panel.
- Settle-path write integration — dry-run through `notion_writeback` (content-gate refusal on an economic field is a required assertion).
- `validate_cache` accepts a well-formed `settle` block and rejects a malformed one.
- Fixture cache with settle candidates renders the Stage-0 panel end-to-end.

## 7. Cleanups (fold in)

- Fix `gate/SKILL.md` header: "durable **wiki** truth" (gate ships the vault only, not Notion).
- Manifest `enabled:false → true` for `aios-brief-cache` + `aios-resolve-sweep` (Phase B).

## 8. Out of scope (YAGNI)

- Source-hooking dev sessions to write Notion at completion — the reconciler covers it; a source-hook is a later optimization.
- Wiring gate → Notion task-flip — the wrong seam once the reconciler exists (the gate ships knowledge; it doesn't know which task a page satisfies).
- Vector/semantic matching infra — start with cited-anchor model matching; add only if precision demands it.

## 9. Success criteria

- The morning after ship: `brief-headline.md` mtime advances overnight (fresh cache); a failed precompute alarms instead of masking.
- The brief opens with a **Stage 0 settle pass**: deterministic misses already healed (reported), inferred completions surfaced with cited evidence for one-click confirm.
- After a settle pass, `notion-changelog.jsonl` shows the confirmed flips and the next `resolve-sweep` reports **non-zero deltas** — Notion is current at the source, not just filtered at read time.
- SEAMS-1065-class re-proposals stop: a progressed task shows `→ In Progress` in the settle panel, not as fresh untouched work.
