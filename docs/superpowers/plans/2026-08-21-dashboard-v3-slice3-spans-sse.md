# Dashboard v3 Slice 3 — Span drill-in + SSE consolidation + polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the "neural graph": any run — from Sessions, Overview, or a pipeline stage with live work — drills into its OTel span waterfall via a REAL join key (OTel `session.id` == the transcript UUID in an activity record's `log_path`); views ride SSE instead of polling; the slice-1/2 carry-minors are closed.

**Architecture:** One new UI module (`ui/spantree.js`: deterministic `traceForRun` join + a `SpanTreeModal` waterfall over the existing orphaned `/api/otel/run/<id>` + `build_graph` projection — no new server route). One tiny server change (`/api/mtimes` gains the `activity` key the SSE fingerprint already has, as a scalar). Views swap `setInterval` polls for the existing `useLive` SSE hook. A final polish task lands the nine recorded carry-minors.

**Tech Stack:** Python 3 stdlib (one route tweak), Preact+HTM no-build UI, pytest.

**Spec:** `docs/superpowers/specs/2026-08-21-dashboard-v3-aaa-slice-design.md` §3.4 + cross-cutting SSE, §6 Slice 3.

## Global Constraints

- No build step, no new dependencies, no graph canvas — the waterfall is an indented DOM list (LangGraph-Studio tree convention), not SVG layout.
- No new server route; no new write path. `/api/otel/run/<traceid>` and `build_graph` already exist (`otel_runs.py:109-147,177-182`) — this slice gives them their consumer.
- The run→trace join must be DETERMINISTIC: OTel run `session_id` === the basename stem of an activity record's `log_path` when it ends in `.jsonl`. NO fuzzy matching (title/time heuristics fabricate lineage — the slice-2 ruling stands; this key is exact).
- Monochrome tokens only, existing semantic classes; new classes use the `sp-*` prefix, NO new hex.
- Every panel names its source; fail-open (a down Jaeger / no matching trace renders an honest visible state, never blank, never a dead button pretending to work).
- Match existing idioms: `useLive` (`ui/lib.js:40-63`), modal pattern (`ui/thread.js` ThreadModal), row affordances (`sessions.js`), `_events` fingerprint (`dashboard_server.py:620-633`).
- Commit after every task (H131). FOREGROUND test runs only. Never touch `ui/panels/` (A127 owns its deletion; its drain may merge mid-slice — the final task rebases).
- Suite: `python -m pytest engine -q` from the worktree, `PYTHONIOENCODING=utf-8`. Known environmental non-failures: `test_servers_dedupe_and_repo_from_prefix` (port 5174 occupied — netstat first) and occasionally `test_events_fingerprint_reacts_to_activity` under full-suite thread contention (passes in isolation).

## Baseline facts (verified 2026-08-21)

- SSE `_events` fingerprint ALREADY includes `activity` as `(count, max_mtime)` over `state/activity/*.json` (`dashboard_server.py:629-632`); `/api/mtimes` (`:315-321`) does NOT — so `useLive`'s poll fallback is blind to activity. JSON would serialize the tuple as an array, and `useLive` compares with `!==` (`lib.js:47`), so an array value would false-fire every poll — the mtimes key must be a SCALAR string.
- `useLive(surfaces, cb)` surface names = fingerprint keys: `brief standup gate_metrics queue standing spend board activity`.
- OTel run summary carries `session_id` (`otel_runs.py:94`); `/api/otel/run/<id>` returns `{nodes:[{id,label,type,op,depth,dur_ms,tokens,model,tool,agent,ok}], edges:[{source,target}], summary}`.
- Activity records: `log_path` for `session-*` records is the Claude transcript `…\<session-uuid>.jsonl`; factory drain records' log_path is a stdout `.log` tee (no uuid) — their SESSION twin record carries the jsonl.
- Current polls to replace: `overview.js:88` (load, 4s) + `:89` (loadSlow, 12s — keep, OTel/servers have no fingerprint), `software.js:69` (5s), `aios.js:84` (5s), `sessions.js` (8s).
- Slice-1/2 carry-minors recorded on A130's slice notes and the two SDD ledgers (restated in Task 5 below — the task is self-contained).

---

### Task 1: Worktree + base

- [ ] **Step 1:** From `C:\Users\sethh\Documents\Claude\Projects\aios`: `git status --short` clean, `git log --oneline -1` at v0.20.0 (`15abfdf`) or later (A127's factory merge may have landed — fine, base on it).
- [ ] **Step 2:** Worktree per `superpowers:using-git-worktrees`: branch `v3-slice3-spans-sse` from `main` (fresh dir `.worktrees/v3-slice3`).
- [ ] **Step 3:** Baseline `python -m pytest engine -q`; record the count.

### Task 2: `/api/mtimes` activity key (TDD)

**Files:**
- Modify: `engine/dashboard/dashboard_server.py` (`/api/mtimes` branch ~:315-321)
- Test: `engine/tools/tests/test_a63_dashboard_api.py` (update `test_mtimes_lists_watched`; add one test)

**Interfaces:**
- Produces: `/api/mtimes` gains `"activity": "<count>:<max_mtime_or_0>"` (a scalar string — same signal as the SSE fingerprint, comparable with `!==` in `useLive`'s poll fallback).

- [ ] **Step 1: Failing tests** — update `test_mtimes_lists_watched`'s expected set to include `"activity"`; append:

```python
def test_mtimes_activity_scalar_reacts(server, env_root):
    m1 = _get_json(server, "/api/mtimes")
    assert isinstance(m1["activity"], str) and ":" in m1["activity"]
    d = env_root / "state" / "activity"
    d.mkdir(parents=True, exist_ok=True)
    (d / "session-x-1.json").write_text(json.dumps({"id": "session-x-1"}), encoding="utf-8")
    m2 = _get_json(server, "/api/mtimes")
    assert m2["activity"] != m1["activity"]
```

- [ ] **Step 2:** Run the test file — the new/updated tests FAIL (missing key).
- [ ] **Step 3: Implement** — in the `/api/mtimes` branch, after the `out["board"] = ...` line add:

```python
            act_dir = activity.ACTIVITY_DIR(env)
            act_files = sorted(act_dir.glob("*.json")) if act_dir.exists() else []
            out["activity"] = "%d:%s" % (len(act_files),
                                         max((_mtime(p) for p in act_files), default=0) or 0)
```

- [ ] **Step 4:** Test file green, then full suite green.
- [ ] **Step 5:** Commit: `feat(dashboard): /api/mtimes activity key so useLive's poll fallback sees run changes`

### Task 3: `ui/spantree.js` — join helper + span waterfall modal

**Files:**
- Create: `engine/dashboard/ui/spantree.js`
- Modify: `engine/dashboard/ui/tokens.css` (`sp-*` classes, existing tokens only)

**Interfaces:**
- Produces: `traceForRun(run, otelRuns) -> traceId|null` and `SpanTreeModal({ traceId, title, onClose })`. Consumed by Task 4.

- [ ] **Step 1: Create `ui/spantree.js`:**

```javascript
// Span waterfall — the OTel trace behind a run, rendered as an indented tree (label · type ·
// duration · tokens · ok/fail), the LangGraph-Studio convention. Source: /api/otel/run/<traceId>
// (Jaeger via otel_runs.build_graph). The run→trace join is DETERMINISTIC: an OTel run's
// session.id equals the <uuid>.jsonl stem of an activity record's log_path — no heuristics.
import { html, api, useState, useEffect } from "/lib.js";

const fmtMs = (ms) => ms == null ? "—" : ms >= 1000 ? (ms / 1000).toFixed(1) + "s" : ms + "ms";
const fmtTok = (n) => { n = Number(n) || 0; return n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n); };

// activity record → its transcript session uuid (only session-style jsonl logs qualify)
export function sessionUuidOf(run) {
  const lp = String(run?.log_path || "").replace(/\\/g, "/");
  if (!lp.endsWith(".jsonl")) return null;
  return lp.split("/").pop().replace(/\.jsonl$/, "") || null;
}

// exact join: the OTel run whose session_id matches this activity record's transcript uuid
export function traceForRun(run, otelRuns) {
  const uuid = sessionUuidOf(run);
  if (!uuid) return null;
  const hit = (otelRuns || []).find((r) => r.session_id === uuid);
  return hit ? hit.trace_id : null;
}

// order spans as a DFS tree from the roots (parents before children, siblings by start order —
// node array order from build_graph is span order, so a stable DFS keeps it readable)
function orderTree(nodes, edges) {
  const kids = {};
  const hasParent = new Set(edges.map((e) => e.target));
  for (const e of edges) (kids[e.source] = kids[e.source] || []).push(e.target);
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const out = [];
  const walk = (id) => { const n = byId[id]; if (!n) return; out.push(n); (kids[id] || []).forEach(walk); };
  nodes.filter((n) => !hasParent.has(n.id)).forEach((n) => walk(n.id));
  for (const n of nodes) if (!out.includes(n)) out.push(n);   // orphans still render (fail-open)
  return out;
}

export function SpanTreeModal({ traceId, title, onClose }) {
  const [g, setG] = useState(null);
  useEffect(() => {
    let alive = true;
    api.get(`/api/otel/run/${encodeURIComponent(traceId)}`)
      .then((d) => { if (alive) setG(d); }).catch(() => { if (alive) setG({ error: true }); });
    return () => { alive = false; };
  }, [traceId]);

  const s = g?.summary;
  const rows = g && !g.error ? orderTree(g.nodes || [], g.edges || []) : [];
  const maxDur = Math.max(1, ...rows.map((n) => n.dur_ms || 0));
  return html`<div class="th-modal" onClick=${onClose}>
    <div class="th-card sp-card" onClick=${(e) => e.stopPropagation()}>
      <div class="th-head"><span class="th-title">${title || "Span tree"}</span>
        <span class="th-repo">trace ${String(traceId).slice(0, 12)}…</span>
        <button class="th-x" onClick=${onClose} aria-label="close">×</button></div>
      <div class="th-body">
        ${g == null ? html`<p class="stub">Loading trace…</p>`
        : g.error || !rows.length ? html`<p class="stub">No spans — the telemetry store (Jaeger) is down or this trace has expired from its retention window.</p>`
        : html`
          ${s ? html`<div class="sp-sum num">${s.model} · ${fmtMs(s.duration_ms)} · ${fmtTok(s.total_tokens)} tok · $${(s.cost_usd || 0).toFixed(2)} est. · ${s.errors} error${s.errors === 1 ? "" : "s"}</div>` : null}
          <div class="sp-list">
            ${rows.map((n) => html`<div class="sp-row ${n.ok === false ? "sp-fail" : ""}" key=${n.id}
                style="padding-left:${8 + Math.min(n.depth, 12) * 14}px">
              <span class="sp-dot ${n.ok === false ? "bad" : ""}"></span>
              <span class="sp-lbl" title=${n.op}>${n.label || n.op}</span>
              <span class="sp-kind">${n.type || ""}${n.agent ? " · " + n.agent : ""}</span>
              <span class="sp-track"><i style="width:${Math.max(2, Math.round(((n.dur_ms || 0) / maxDur) * 100))}%"></i></span>
              <span class="sp-num num">${fmtMs(n.dur_ms)}${n.tokens ? " · " + fmtTok(n.tokens) : ""}</span>
            </div>`)}
          </div>`}
      </div>
      <div class="th-foot"><span class="note">OpenTelemetry span tree · /api/otel/run · read-only.</span></div>
    </div>
  </div>`;
}
```

- [ ] **Step 2: Styles** (`sp-*`, existing tokens only): `.sp-card` (wider max-width than th-card if th-card is narrow — match th-card's pattern), `.sp-sum` (muted strip, hairline bottom border), `.sp-list` (column), `.sp-row` (flex: dot / lbl / kind(muted) / track(1fr) / num; hairline dividers), `.sp-dot` (6px circle, ok token; `.bad` = `--err`), `.sp-fail` (row text slightly `--err`-tinted via existing class pattern), `.sp-track` (thin bar track + `i` fill like `.uz-track/.uz-fill` — reuse those two classes if identical rather than duplicating).
- [ ] **Step 3:** `node --check engine/dashboard/ui/spantree.js`. Commit: `feat(dashboard): span-waterfall modal + deterministic run→trace join (spantree.js)`

### Task 4: Wire spans affordances (Sessions, Overview, Software live-runs)

**Files:**
- Modify: `engine/dashboard/ui/views/sessions.js`
- Modify: `engine/dashboard/ui/views/overview.js`
- Modify: `engine/dashboard/ui/views/software.js`

**Interfaces:**
- Consumes: `traceForRun`, `SpanTreeModal` from `/spantree.js`; `/api/otel/runs` (already served).

- [ ] **Step 1: sessions.js** — import `{ traceForRun, SpanTreeModal }`; add state `const [otel, setOtel] = useState(null);` and `const [spans, setSpans] = useState(null);` load OTel runs once + on the same refresh cadence as runs (`api.get("/api/otel/runs").then((d) => setOtel(d.runs || [])).catch(() => setOtel([]))`). In each row, compute `const tid = traceForRun(r, otel);` and render after the status span:

```javascript
        ${tid ? html`<button class="verb sp-open" title="Open the span waterfall"
            onClick=${(e) => { e.stopPropagation(); setSpans({ tid, title: r.title || r.id }); }}>spans</button>` : null}
```

and at the section end: `${spans ? html`<${SpanTreeModal} traceId=${spans.tid} title=${spans.title} onClose=${() => setSpans(null)} />` : null}`. A row with no matching trace shows NO button (honest absence, not a dead control).
- [ ] **Step 2: overview.js** — same import + `spans` state; in the "Running now" rows compute `tid = traceForRun(r, otelRuns)` (otelRuns already in scope) and add the same stop-propagation `spans` button; render the modal beside `ThreadModal`.
- [ ] **Step 3: software.js live-runs strip** — import `{ traceForRun, SpanTreeModal }`; fetch `/api/activity` + `/api/otel/runs` alongside the model load (slow cadence fine). Compute, for the SELECTED stage, the live runs whose `item_ids` intersect the stage's item ids:

```javascript
  const stageIds = new Set(items.map((i) => i.id));
  const liveRuns = (act || []).filter((r) => r.live && (r.item_ids || []).some((id) => stageIds.has(id)));
```

Render above the `ai-drill` (only when `liveRuns.length`): a thin strip — for each run: surface badge, title, and a `spans` button when `traceForRun(run, otel)` matches EITHER the run itself or its session twin (compute: `tid = traceForRun(r, otel) || traceForRun((act || []).find((s) => s.surface === "session" && s.live && String(s.title).includes(r.id)) , otel)` — NO: that string-match is a heuristic. Instead: for factory records (log tee), look for a session record whose `worktree` equals the factory run's `worktree` (both records carry the worktree path — an exact field equality, not fuzzy): `const twin = (act || []).find((s) => s.surface === "session" && s.worktree && s.worktree === r.worktree); const tid = traceForRun(r, otel) || traceForRun(twin, otel);`). If no tid, show the run without a button. Plus the modal render.
- [ ] **Step 4:** `node --check` all three; headless probe: server on a free port against the real env root, `/api/otel/runs` 200, `/` 200, stop. `python -m pytest engine/tools/tests/test_a63_dashboard_api.py -q` foreground.
- [ ] **Step 5:** Commit: `feat(dashboard): spans drill-in from Sessions, Overview, and Software live runs`

### Task 5: SSE consolidation + carry-minor polish

**Files:**
- Modify: `engine/dashboard/ui/views/overview.js`, `aios.js`, `software.js`, `sessions.js` (polls → `useLive`)
- Modify: `engine/dashboard/ui/views/health.js`, `ui/thread.js`, `engine/dashboard/otel_runs.py`, `ui/tokens.css` (carry-minors)

- [ ] **Step 1: SSE swaps** (import `useLive` from `/lib.js` where missing):
  - `overview.js`: replace the 4s `setInterval(load)` with `useLive(["board", "activity", "brief", "queue", "standing"], load)`; KEEP the 12s `loadSlow` interval (OTel + servers have no fingerprint).
  - `software.js`: replace the 5s interval with `useLive(["board", "activity", "brief"], load)`; when the events fire also re-run `loadStage()` if a stage is selected (call both in the cb).
  - `aios.js`: replace the 5s interval with `useLive(["queue", "brief"], () => { ...same reload body... })`.
  - `sessions.js`: replace the 8s interval with `useLive(["activity"], load)` (the OTel fetch stays on its own slow cadence or piggybacks the cb).
- [ ] **Step 2: carry-minors** (each is small and exact):
  1. `health.js`: render `origin` in the check row meta (`c.origin ? " · " + c.origin : ""` appended to the kind/cadence span).
  2. `health.js`: the `st` fallback backfills all four keys: `const st = h.standing || { reds: 0, greens: 0, watch_expired: 0, checks: [] };`
  3. Remove the inert `hl-row-${c.status}` class from health.js rows (no CSS matches it).
  4. `overview.js` health tile: value shows reds, `sub` becomes `` `${health?.standing?.watch_expired || 0} watch expired` `` when > 0 else "standing checks"; add `tabindex="0"` + Enter/Space `onKeyDown` to the clickable Metric (extend `Metric` to accept `onKeyDown` or wrap — match the `.ov-run` idiom).
  5. `sessions.js` rows: activate on Space as well as Enter (`if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setReplay(r); }`).
  6. `sessions.js` empty state: distinguish — if `runs.length === 0` render `"No runs in the window (terminal records prune after ~24h)."`; else if `rows.length === 0` render the existing no-match copy.
  7. `thread.js`: when the run is NOT live and no messages parsed, body copy becomes `"No readable messages — this log isn't a transcript (factory drains tee stdout; the drain's session record carries the full transcript)."` and the footer says `"Transcript (read-only)."` without "refreshing"; live runs keep the current copy. (The component receives `run` — use `run.live`.)
  8. `otel_runs.py` `_aggregate`: make the duration filter explicit — `durs = [r["duration_ms"] for r in runs if (r.get("duration_ms") or 0) > 0]` with a trailing comment `# 0/missing = no duration data, excluded from percentiles`. Update no tests (behavior identical).
  9. `tokens.css`: nothing new needed beyond what the above require; delete nothing else.
- [ ] **Step 3:** `node --check` every touched JS file; `python -m pytest engine/tools/tests/test_otel_runs.py engine/tools/tests/test_a63_dashboard_api.py -q` foreground green; then the FULL suite foreground.
- [ ] **Step 4:** Commit: `feat(dashboard): SSE-driven views + slice-1/2 polish minors`

### Task 6: Verification, review, merge

- [ ] **Step 1 (controller): browser pass** against the real env root — SSE: with the page idle, touch a state file and watch the view refresh without a poll storm; Sessions/Overview rows show `spans` only where a trace matches; open one span waterfall from a real run (indentation, durations, fail dots); Software: select a stage with a live run (a factory drain if one is running) and open its spans; Health shows `origin`; console clean. Screenshots (Playwright, dark, 1440).
- [ ] **Step 2:** Rebase onto latest `main` (A127 may have merged — panels deletion + route retire are orthogonal; tokens.css hunks may brush). Full suite after rebase.
- [ ] **Step 3:** Fresh-context whole-branch review (most capable model), spec §3.4 + Global Constraints; direct reviewer agent, not the review-gate Workflow.
- [ ] **Step 4:** ff-merge to `main` + bump `.claude-plugin/plugin.json` to the next minor + annotate A130 (Slice 3 shipped + carried items status) in one commit; push; suite on merged main; `git worktree remove` from outside.
- [ ] **Step 5:** Screenshots to Seth — his look is the slice gate.

---

## Self-review (done at authoring)

- Spec §3.4 all lands (Tasks 3-4); cross-cutting SSE lands (Task 5 s1; OTel stays polled per spec); carry-minors all nine addressed (Task 5 s2) — the one NOT addressed: override-ids→Board deep link (needs an id-addressable Board route that doesn't exist; stays on A130 as a noted deferral).
- Placeholders: none; the one rejected mid-step idea (title string-match) is shown crossed out with its exact replacement (worktree field equality — a real key).
- Type consistency: `traceForRun(run, otelRuns)` consumes `run.log_path`/`run.worktree` (activity contract) + `otelRuns[].session_id/trace_id` (otel contract); `SpanTreeModal` consumes `/api/otel/run` graph keys verbatim (`id,label,type,op,depth,dur_ms,tokens,agent,ok`).
