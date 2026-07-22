---
type: findings
project: aios
item: A109
created: 2026-07-22
tags: [a109, dashboard-v2, handoff, needs-you, blocked-legs]
---

# A109 Dashboard v2a — blocked-legs hand-off (env session + human)

**Why this doc exists.** A109's engine legs (tasks 1–5 + the task-10 docs leg) shipped and merged;
the plugin is live at **v0.9.2**. The remaining legs are all genuine blocks that autonomous
worktree drains cannot execute — the standing-check YAML and reinstall steps had been "emitted in
the drain report" (ephemeral chat) rather than captured anywhere durable. This is the single
executable hand-off: pick it up in a **native env session** (not an isolated worktree) and work
top-down. Source of record for the leg detail: the plan
`docs/superpowers/plans/2026-07-22-a109-dashboard-v2a.md` and the A109 BACKLOG line.

Nothing here touches a guard-frozen surface autonomously — the standing-check block below is quoted
for a **human** to append; the drain never edits `state/standing-checks/checks.yaml`.

---

## 1. Standing-check — append to `state/standing-checks/checks.yaml` (env session)

Task 5 (SSE) graduates a health hook: the stdlib `ThreadingHTTPServer` gives one thread per SSE
stream, fine until streams exceed ~a dozen concurrent (tabs + phone), at which point the spec's
uvicorn graduation applies. The check is a placeholder-free manual-observation trigger. **Append
this block by hand** to `<env_root>/state/standing-checks/checks.yaml` (it is guard-frozen — a human
edits it, not a drain):

```yaml
  - id: dashboard-sse-thread-headroom
    kind: standing
    cadence: weekly
    predicate: 'python -c "import json,sys;print(0)"'
    note: >-
      Placeholder-free health hook: revisit when SSE streams exceed ~a dozen
      concurrent (tabs+phone). Manual observation trigger — graduate the
      dashboard to uvicorn per A109 spec §Engine work 4 if degradation observed.
    on_violation: note
```

Predicate is cross-platform (`python -c`, no `test`/`$(…)`/`ls`) per the standing-check convention.

---

## 2. Both-scope reinstall — the task-10 tail (env session)

The **version-bump half is already done** (`plugin.json` at 0.9.2, `e5522bc`). What remains is the
cross-scope reinstall so the reference install picks up the merged engine slice (`/api/board`,
`/api/events` SSE, `queue_tx dismiss`, `brief_session record_reply`, the `backlog_parse` parser).
In a native env session on the reference machine:

1. `/plugin update aios` (or reinstall from the latest tag) — both the user scope and the
   project scope that enable `aios@aios`.
2. Live smoke against the real install: open the dashboard, confirm the Board endpoint returns
   auto-discovered lanes and an SSE-pushed state-file change reflects without reload.
3. Confirm `runtime_fingerprint` shows no stale-sha drift after the reinstall (the `e5522bc`
   ancestry check is what makes the bump-then-reinstall clean).
4. Delete this section from the doc once confirmed live.

Laptop + any other clone: same reinstall on their next update (no divergent-root issue here — this
is an ordinary engine bump, not a history reset).

---

## 3. UI legs 6–8 — need a networked build box (not this sandbox)

Tasks 6–8 (vendor Preact+HTM, UI shell, Inbox, Board) are **un-drainable in an isolated worktree**:
the sandbox has no network, so `curl https://esm.sh/preact@10.24.3/...` returns HTTP 000 / exit 35 —
Preact+HTM cannot be vendored — and there is no headless browser to run the mandatory smoke checks
(SSE-live re-render, 375/320px accordion + zero horizontal scroll, fixture-repo-appears-without-config).
Hand-writing the Preact/HTM source would be fabrication, not vendoring. **Run tasks 6–8 on a machine
with network + a browser**, following the plan verbatim; the pinned versions are `preact@10.24.3`,
`htm@3.1.1` (both MIT — record in `ui/vendor/LICENSES.md` per task 6).

---

## 4. Edit-verb leg 9 (`ship.py amend`) — human review-gate pass

Task 9 adds `ship.py amend` (operator-edited draft through the full gate path) + the `gate_edit`
action + the UI edit mode. It touches **ship-path semantics** (a new write verb that replaces a
staging draft then proceeds through the revert-pointer / receipt / Paper-Governs flow), so it is
**review-gate tier, human-gated** — out of the autonomous drain envelope by the plan's own
constraint (plan §Global Constraints + task 9 header). `ship.py` today has verbs
`resolve/ship/reject/sweep-husks/backfill-explored` and **no `amend`/`edit`** — the leg is unbuilt.
Build it in a session where the diff can go to the `review-gate` workflow + Seth before merge.

---

## Disposition

A109 stays **open** (needs-you). No leg here is a `[GATE: human]` *decision* Seth must make before
anyone can proceed — the design is approved; these are execution hand-offs to the right context
(env session for §1–2, networked box for §3, human-reviewed session for §4). Close each section as
it lands; close A109 when §3 + §4 ship and §1–2 are confirmed live.
