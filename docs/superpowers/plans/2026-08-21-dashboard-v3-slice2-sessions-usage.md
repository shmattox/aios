# Dashboard v3 Slice 2 — Sessions + Usage upgrades + cost honesty Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A completed-and-live run browser (findable + replayable), plus Usage latency + error-classification, plus honest cost labelling — all from data the engine already writes.

**Architecture:** Extend the pure OTel projection (`otel_runs.py`) with error-classification, unpriced-model tracking, and latency percentiles (all in a unit-testable `_aggregate`). Add one Preact view (`sessions.js`) over the existing `/api/activity` records, sharing the transcript-replay modal that is extracted out of `overview.js` into `ui/thread.js` (DRY). No new server route, no new collector.

**Tech Stack:** Python 3 stdlib (`otel_runs.py`), Preact+HTM no-build UI (vendored, `/lib.js`), pytest.

**Spec:** `docs/superpowers/specs/2026-08-21-dashboard-v3-aaa-slice-design.md` §3.3, §3.6, §3.7/A129, §6 Slice 2.

## Global Constraints

- No build step, no new dependencies, no React Flow (spec §4).
- Server never writes state; no new write path. This slice adds NO new server route — Sessions reuses `GET /api/activity`, Usage reuses `GET /api/otel/runs`.
- Monochrome token sheet only; existing semantic classes (`warn`, `.hl-*`, `.uz-*`, `.ov-*`) — NO new hex values.
- Every panel names its source and shows data age where relevant (stale-is-a-visible-badge).
- Cost is a derived estimate, never authoritative (`otel_runs.py:20-21`): every rendered dollar figure carries an "est." marker; an unknown *real* model id (priced via `DEFAULT_PRICE`) is surfaced as a visible "unpriced model" note, never silently blended.
- Fail-open advisory posture: a down Jaeger / empty activity dir renders a visible empty state, never a blank panel or an exception.
- Match existing idioms exactly: `Metric`/`ov-strip`/`ov-sect`/`uz-src` (see `ui/views/usage.js`, `ui/views/overview.js`); nav registration pattern in `ui/app.js` (import + `ICONS` + `NAV` + `g`-chord); test harness in `engine/tools/tests/test_otel_runs.py`.
- Commit after every task — in a factory-managed repo, uncommitted work across a wait is unsafe (H131).
- Suite command: `python -m pytest engine -q` from `Projects/aios` (with `PYTHONIOENCODING=utf-8`). Known environmental non-failure: `test_servers_dedupe_and_repo_from_prefix` fails only when local port 5174 is occupied — verify with `netstat` before treating it as real; it is NOT introduced by this slice.

## Baseline facts (verified 2026-08-21, so the implementer need not re-derive)

- **Activity record** (`engine/tools/activity.py` `read_all`): every `.json` under `state/activity/`; fields `{id, surface, title, item_ids[], repo, pid, started, heartbeat, ended, status, tokens, cost_usd, input_tokens, output_tokens, log_path, detail, worktree, spans[], pending_approval}` plus computed `live` (bool) and `age_s` (float). Surfaces: `factory, pipeline, session, goal, workflow`. Terminal statuses: `shipped, parked, no-op, failed, ended`. Terminal records are pruned after 24h (`prune retain_s=86400`), so the browser shows "recent + live", not all-time — label it so.
- **Replay:** `GET /api/activity/<id>/log?tail=N` returns `{lines:[...]}`; the transcript modal in `overview.js` (`ThreadModal` + `parseThread`, lines 17-66) renders it. Works for any record whose `log_path` still resolves.
- **OTel run summary** (`otel_runs.summarize_trace`): per run `{trace_id, title, session_id, model, started, duration_ms, in_tokens, out_tokens, cache_w, cache_r, total_tokens, cost_usd, span_count, tool_count, agent_count, top_tools[], errors, status}`. `fetch_runs()` → `{runs:[...], agg:{runs, tokens, cost_usd, errors, jaeger_up}}`.
- **OTel span shape** (`test_otel_runs.py`): a `tool.execution` span carries `{"span.type":"tool.execution","success":"false"}` and NO `tool_name`; the tool name lives on its CHILD_OF parent `tool`-type span (`tool_name`). Error-classification must resolve exec→parent.

---

### Task 1: Worktree + base

**Files:** none (setup)

- [ ] **Step 1:** From `C:\Users\sethh\Documents\Claude\Projects\aios`, confirm `main` is clean and current: `git status --short` (empty) and `git log --oneline -1` (should be the v0.19.0 Slice-1 commit `730a28b` or later).
- [ ] **Step 2:** Create the isolated workspace per `superpowers:using-git-worktrees`: branch `v3-slice2-sessions-usage` from `main`. Do NOT reuse any `.worktrees/factory-*` or the stale `.worktrees/v3-slice1` dir.
- [ ] **Step 3:** In the worktree, run `python -m pytest engine -q` and record the green baseline count (expect ~779, ±1 for the port-5174 environmental case).

### Task 2: `otel_runs.py` — error-classification, unpriced-model tracking, latency percentiles (TDD)

**Files:**
- Modify: `engine/dashboard/otel_runs.py` (`summarize_trace` ~:61-106; add module-level `_pct`, `_aggregate`; rewire `fetch_runs` ~:160-174)
- Test: `engine/tools/tests/test_otel_runs.py` (append)

**Interfaces:**
- Produces (consumed by Task 4): `summarize_trace(trace)` gains two keys — `error_kinds: {tool_name|"unknown": int}` (failed `tool.execution` spans grouped by their parent `tool` span's `tool_name`) and `priced: bool` (False only for a real model id absent from `PRICES`; the `"—"`/empty no-model sentinel stays `priced: True`). `fetch_runs()["agg"]` gains `error_kinds: {name:int}` (merged), `unpriced_models: [str]` (sorted), `p50_ms: int|None`, `p95_ms: int|None`.

- [ ] **Step 1: Write the failing tests** (append to `test_otel_runs.py`; the existing `TRACE` fixture already has a failed exec `s4` whose parent `s3` is `tool_name="Agent"`):

```python
def test_error_kinds_groups_by_parent_tool():
    r = o.summarize_trace(TRACE)
    assert r["error_kinds"] == {"Agent": 1}      # s4 exec failed; its CHILD_OF parent s3 is tool "Agent"
    assert r["priced"] is True                    # claude-opus-5 is in PRICES


def test_priced_flag_true_for_no_model_sentinel():
    # a run with no llm span → model "—" → NOT an unpriced real model
    r = o.summarize_trace({"traceID": "n", "spans": [
        _span("i", "claude_code.interaction", {"span.type": "interaction"})]})
    assert r["priced"] is True and r["error_kinds"] == {}


def test_priced_flag_false_for_unknown_real_model():
    t = {"traceID": "u", "spans": [
        _span("i", "claude_code.interaction", {"span.type": "interaction"}),
        _span("l", "claude_code.llm_request",
              {"span.type": "llm_request", "model": "claude-future-9",
               "input_tokens": 10, "output_tokens": 5}, parent="i")]}
    r = o.summarize_trace(t)
    assert r["priced"] is False and r["model"] == "claude-future-9"


def test_aggregate_merges_kinds_unpriced_and_percentiles():
    runs = [
        {"total_tokens": 100, "cost_usd": 0.01, "errors": 1, "duration_ms": 1000,
         "error_kinds": {"Agent": 1}, "model": "claude-opus-5", "priced": True},
        {"total_tokens": 200, "cost_usd": 0.02, "errors": 2, "duration_ms": 3000,
         "error_kinds": {"Agent": 1, "Bash": 2}, "model": "claude-future-9", "priced": False},
        {"total_tokens": 50, "cost_usd": 0.0, "errors": 0, "duration_ms": 2000,
         "error_kinds": {}, "model": "—", "priced": True},
    ]
    agg = o._aggregate(runs, jaeger_up=True)
    assert agg["error_kinds"] == {"Agent": 2, "Bash": 2}
    assert agg["unpriced_models"] == ["claude-future-9"]
    assert agg["p50_ms"] == 2000 and agg["p95_ms"] == 3000
    assert agg["runs"] == 3 and agg["errors"] == 3


def test_aggregate_empty_is_safe():
    agg = o._aggregate([], jaeger_up=False)
    assert agg["p50_ms"] is None and agg["p95_ms"] is None
    assert agg["error_kinds"] == {} and agg["unpriced_models"] == [] and agg["jaeger_up"] is False
```

- [ ] **Step 2:** Run `python -m pytest engine/tools/tests/test_otel_runs.py -q` — expect the 5 new tests FAIL (`KeyError`/`AttributeError` on missing keys / `_aggregate`).
- [ ] **Step 3: Implement** in `otel_runs.py`. In `summarize_trace`, after `execs = _spans_of_type(spans, "tool.execution")` and the existing `errors = ...` line, add error-kind resolution and the priced flag; add both keys to the returned dict:

```python
    by_id = {s.get("spanID"): s for s in spans}

    def _parent_tool_name(sp):
        for ref in sp.get("references", []) or []:
            if ref.get("refType") == "CHILD_OF":
                p = by_id.get(ref.get("spanID"))
                if p is not None:
                    return _tag(p, "tool_name")
        return None

    error_kinds = {}
    for s in execs:
        if str(_tag(s, "success")).lower() not in ("true", "1"):
            name = _parent_tool_name(s) or _tag(s, "tool_name") or "unknown"
            error_kinds[name] = error_kinds.get(name, 0) + 1
    real_unknown_model = model not in PRICES and model not in ("—", "", None)
```

then in the returned dict add (next to `"errors": errors,`):

```python
        "error_kinds": error_kinds,
        "priced": not real_unknown_model,
```

Add two module-level helpers (place above `fetch_runs`):

```python
def _pct(vals, q):
    """Nearest-rank percentile over a list (returns None on empty)."""
    if not vals:
        return None
    xs = sorted(vals)
    i = max(0, min(len(xs) - 1, int(round(q * (len(xs) - 1)))))
    return xs[i]


def _aggregate(runs, jaeger_up):
    """Pure roll-up over run summaries → the /api/otel/runs agg strip (unit-testable)."""
    error_kinds = {}
    for r in runs:
        for k, c in (r.get("error_kinds") or {}).items():
            error_kinds[k] = error_kinds.get(k, 0) + c
    durs = [r["duration_ms"] for r in runs if r.get("duration_ms")]
    return {
        "runs": len(runs),
        "tokens": sum(r["total_tokens"] for r in runs),
        "cost_usd": round(sum(r["cost_usd"] for r in runs), 2),
        "errors": sum(r["errors"] for r in runs),
        "error_kinds": error_kinds,
        "unpriced_models": sorted({r["model"] for r in runs if not r.get("priced", True)}),
        "p50_ms": _pct(durs, 0.5),
        "p95_ms": _pct(durs, 0.95),
        "jaeger_up": jaeger_up,
    }
```

and replace the inline agg block in `fetch_runs` with `agg = _aggregate(runs, data is not None)` (keep the `runs.sort(...)` line before it; return `{"runs": runs, "agg": agg}` unchanged).

- [ ] **Step 4:** Run `python -m pytest engine/tools/tests/test_otel_runs.py -q` — expect all PASS (existing 4 + new 5). Then the full suite `python -m pytest engine -q` — green (any other test asserting the old agg keys must be updated here, not skipped; there are none expected, `test_a63_dashboard_api.py`'s otel test uses a live-ish path).
- [ ] **Step 5:** Commit: `git add engine/dashboard/otel_runs.py engine/tools/tests/test_otel_runs.py && git commit -m "feat(dashboard): otel error-classification + unpriced-model + latency percentiles in the agg"`

### Task 3: Shared thread module + Sessions view + nav

**Files:**
- Create: `engine/dashboard/ui/thread.js` (extracted `ThreadModal` + `parseThread`)
- Create: `engine/dashboard/ui/views/sessions.js`
- Modify: `engine/dashboard/ui/views/overview.js` (delete the local `ThreadModal`/`parseThread`, import from `/thread.js`)
- Modify: `engine/dashboard/ui/app.js` (import, `ICONS.sessions`, `NAV` after `gate`, `g e` chord)
- Modify: `engine/dashboard/ui/tokens.css` (only if a needed row/table class is missing — prefer existing `.hl-*`/`.ov-run`/`.uz-*`; new classes use the `se-*` prefix and existing tokens only)

**Interfaces:**
- Consumes: `GET /api/activity` → `{runs:[<record + live + age_s>], _now}`; `GET /api/activity/<id>/log?tail=N` (via the shared modal).
- Produces: `ui/thread.js` exports `ThreadModal` and `parseThread`; `sessions.js` exports `SessionsView`; nav key `"sessions"`, chord `g e`.

- [ ] **Step 1: Create `ui/thread.js`** — move `parseThread` (overview.js:17-34) and `ThreadModal` (overview.js:36-66) verbatim into it, prefixed with the imports they need:

```javascript
// Shared read-only transcript viewer: parse a Claude Code JSONL transcript into turns and
// render a live-tailing modal. Used by Overview ("running now") and Sessions (replay).
import { html, api, useState, useEffect } from "/lib.js";

export function parseThread(lines) {
  // ... exact body from overview.js:18-33 ...
}

export function ThreadModal({ run, onClose }) {
  // ... exact body from overview.js:37-65 ...
}
```

- [ ] **Step 2: Rewire `overview.js`** — delete its local `parseThread` and `ThreadModal` (lines 17-66) and add `import { ThreadModal } from "/thread.js";` to the import block (the `parseThread` import is not needed by overview). Leave everything else untouched. Verify `node --check engine/dashboard/ui/views/overview.js`.

- [ ] **Step 3: Write `sessions.js`** (idioms from usage.js/overview.js; a searchable table over all activity runs, newest first, replay on click):

```javascript
// Sessions — recent + live runs across every surface (factory drains, interactive sessions,
// pipeline stages, goals, workflows). Source: /api/activity (this env's own run records;
// terminal records are pruned after ~24h, so this is "recent", not all-time). Click a row to
// replay its transcript. Cost is a derived estimate.
import { html, api, useState, useEffect } from "/lib.js";
import { ThreadModal } from "/thread.js";

const fmtUsd = (n) => "$" + (Number(n) || 0).toFixed(2);
const fmtTok = (n) => { n = Number(n) || 0; return n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n); };
const fmtDur = (r) => { const e = (r.ended || r.heartbeat || 0) - (r.started || 0); return e > 0 ? (e >= 60 ? Math.round(e / 60) + "m" : Math.round(e) + "s") : "—"; };
const fmtAge = (s) => s == null ? "—" : s < 90 ? `${Math.round(s)}s` : s < 5400 ? `${Math.round(s / 60)}m` : s < 172800 ? `${Math.round(s / 3600)}h` : `${Math.round(s / 86400)}d`;

export function SessionsView() {
  const [runs, setRuns] = useState(null);
  const [q, setQ] = useState("");
  const [replay, setReplay] = useState(null);

  const load = () => api.get("/api/activity")
    .then((d) => setRuns(d.runs || [])).catch(() => setRuns([]));
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, []);

  if (runs == null) return html`<section class="view"><div class="viewhead"><h1>Sessions</h1></div><p class="stub">…</p></section>`;

  const needle = q.trim().toLowerCase();
  const match = (r) => !needle || [r.id, r.repo, r.surface, r.title, (r.item_ids || []).join(" ")]
    .some((v) => String(v || "").toLowerCase().includes(needle));
  const rows = runs.filter(match)
    .sort((a, b) => (b.ended || b.heartbeat || b.started || 0) - (a.ended || a.heartbeat || a.started || 0));
  const live = rows.filter((r) => r.live).length;

  return html`<section class="view">
    <div class="viewhead"><h1>Sessions</h1><span class="sub">recent + live runs · /api/activity · replayable</span></div>

    <div class="ov-strip">
      <div class="ov-cell"><div class="ov-k">runs shown</div><div class="ov-v">${rows.length}</div><div class="ov-s">of ${runs.length} in window</div></div>
      <div class="ov-cell"><div class="ov-k">live now</div><div class="ov-v">${live}</div><div class="ov-s">still running</div></div>
    </div>

    <div class="se-search"><input class="se-input" type="search" placeholder="filter by id · repo · surface · item"
      value=${q} onInput=${(e) => setQ(e.target.value)} aria-label="filter sessions" /></div>

    ${rows.length ? html`<div class="hl-list se-table">
      ${rows.map((r) => html`<div class="hl-row se-row ${r.live ? "se-live" : ""}" key=${r.id}
          tabindex="0" title="Replay this run's transcript" onClick=${() => setReplay(r)}
          onKeyDown=${(e) => { if (e.key === "Enter") setReplay(r); }}>
        <span class="badge">${(r.surface || "").slice(0, 4).toUpperCase()}</span>
        <span class="se-title">${r.title || r.id}</span>
        <span class="se-meta">${r.repo || ""}${(r.item_ids || []).length ? " · " + r.item_ids.join(",") : ""}</span>
        <span class="se-stat ${r.status === "failed" || r.status === "parked" ? "warn" : ""}">${r.live ? "running" : r.status}</span>
        <span class="se-nums num">${fmtDur(r)} · ${fmtTok(r.tokens)} · ${fmtUsd(r.cost_usd)} est. · ${fmtAge(r.age_s)}</span>
      </div>`)}
    </div>` : html`<p class="stub">No runs match. (Terminal records are pruned after ~24h — this window is recent + live only.)</p>`}

    ${replay ? html`<${ThreadModal} run=${replay} onClose=${() => setReplay(null)} />` : null}
  </section>`;
}
```

- [ ] **Step 4: Style** — add to the sheet holding `.hl-*`, existing tokens only: `.se-search` (margin), `.se-input` (full-width, token bg/border/text, mono-ish), `.se-row` (grid: badge / title(1fr) / meta / stat / nums — align to the `.hl-row` grid idiom), `.se-live` (a subtle left accent using an existing token), `.se-title` (ellipsis overflow), `.se-meta`/`.se-stat`/`.se-nums` (muted, `.num` tabular where numeric). No new hex.
- [ ] **Step 5: Register** in `app.js`: `import { SessionsView } from "/views/sessions.js";`; `ICONS.sessions = html`<svg class="ic" viewBox="0 0 16 16"><path d="M2 3.5h12M2 8h12M2 12.5h8"/></svg>``; `NAV` entry `{ key: "sessions", label: "Sessions", view: SessionsView }` after the `gate` entry (still above the `pipelines` section divider); chord — add `if (e.key === "e") { location.hash = "#/sessions"; return; }` to the `g` block (e = sEssions; `s` is taken by software).
- [ ] **Step 6: Headless verify** (controller does the browser pass) — `node --check` on `thread.js`, `sessions.js`, `overview.js`, `app.js`; start the server against the real env root on a free port, curl `/api/activity` → 200 JSON with a `runs` array, curl `/` → 200; stop the server. Run `python -m pytest engine/tools/tests/test_a63_dashboard_api.py -q` to prove no server regression.
- [ ] **Step 7:** Commit: `git add engine/dashboard/ui && git commit -m "feat(dashboard): Sessions view (recent+live runs, search, transcript replay) + shared thread module"`

### Task 4: Usage upgrades — latency, error-classification, cost honesty (A129)

**Files:**
- Modify: `engine/dashboard/ui/views/usage.js`
- Modify: `engine/dashboard/ui/views/overview.js` (spend tile "est." marker)

**Interfaces:**
- Consumes: `GET /api/otel/runs` → `{runs, agg:{... p50_ms, p95_ms, error_kinds, unpriced_models}}` (Task 2).

- [ ] **Step 1: Usage latency tile** — in `usage.js`'s `ov-strip`, add a fifth `Metric`: `k="latency · p50/p95"`, value = `otelUp && agg.p50_ms != null ? \`${fmtMs(agg.p50_ms)} / ${fmtMs(agg.p95_ms)}\` : "—"`, sub = "telemetry window". Add a `fmtMs` helper: `const fmtMs = (ms) => ms == null ? "—" : ms >= 1000 ? (ms/1000).toFixed(1) + "s" : ms + "ms";`.
- [ ] **Step 2: Cost honesty markers** — append `" est."` to the "spend · today" and "telemetry · window" tile values (or their `sub`), and to the "factory drains" tile. Change the "Daily cost" section source badge to read `· factory drains · /api/spend · est.`. In `overview.js`, change the "spend · today" tile `sub` to include `est.` (e.g. `${fmtTok(tokToday)} tok · est.`).
- [ ] **Step 3: Unpriced-model note** — under the strip (or beside the telemetry tile), when `agg.unpriced_models?.length`, render a visible `<p class="uz-note warn">` naming them: `Unpriced model(s) charged at the $5/$15 default — cost is a floor, not a quote: ${agg.unpriced_models.join(", ")}`. Render nothing when the list is empty.
- [ ] **Step 4: Error-classification row** — add a section `<h3 class="ov-sect">Errors by tool <span class="uz-src">· telemetry window · /api/otel/runs</span></h3>`; when `Object.keys(agg.error_kinds||{}).length`, render a `.uz-bars-list` of `Bar` rows (reuse the existing `Bar`) one per tool, `n=count`, `of=agg.errors || 1`, sorted desc; else `<p class="stub">No tool errors in the telemetry window.</p>`.
- [ ] **Step 5: Promote the two-ledger prose to a badge** — the existing `uz-note` explaining "run counts complete, per-run cost only where carried" stays, but add to the "By surface" section's `uz-src` badge the honest qualifier `· by volume, not dollars` so the caveat reads at a glance without reading the paragraph.
- [ ] **Step 6: Headless verify** — `node --check` on both files; start the server on a free port against the real env root; if Jaeger is up, curl `/api/otel/runs` and confirm the agg carries `p50_ms`/`error_kinds`/`unpriced_models` keys (if Jaeger is down, confirm the view's `—`/empty degrade path by reading the code); stop the server. Suite: `python -m pytest engine/tools/tests/test_a63_dashboard_api.py -q`.
- [ ] **Step 7:** Commit: `git add engine/dashboard/ui && git commit -m "feat(dashboard): Usage latency + errors-by-tool + cost-honesty markers (A129)"`

### Task 5: Verification, review, merge

**Files:** none (process)

- [ ] **Step 1: Full browser verification** against the real env root (controller): Sessions nav item routes; the table lists real runs (confirm at least one terminal `factory-*` drain record and one `session-*` record are present and both open the replay modal with transcript content); the search box filters; Usage shows the latency tile, the errors-by-tool section (or its honest empty state), "est." on every dollar figure, and the unpriced-model note only if a non-standard model appears. Console clean on all touched routes. Capture Sessions + Usage screenshots (Playwright fallback per the browser-pane-cannot-screenshot memory: local `playwright`, `colorScheme:"dark"`, 1440-wide).
- [ ] **Step 2: Rebase onto latest `main`** (a factory tick may have landed A127's panel prune, which deletes `ui/panels/` — orthogonal to this slice's files; resolve trivially if it touches app.js). Re-run `python -m pytest engine -q`.
- [ ] **Step 3: Fresh-context whole-branch review** (most capable model) against spec §3.3/§3.6/§3.7 + Global Constraints; CRITICAL/IMPORTANT loop back into a fix pass; use a direct reviewer agent, not the saved review-gate Workflow (worktree false-PASS memory).
- [ ] **Step 4: Merge** per `superpowers:finishing-a-development-branch`: ff-only to `main`; bump `.claude-plugin/plugin.json` to `0.20.0` (version-bump-on-engine-merge, A93) and annotate A130 with a Slice-2-shipped note in the same commit; push; run the suite once on merged `main`; remove the worktree (`git worktree remove`, never `rm -rf` from inside — 2026-08-13 gotcha).
- [ ] **Step 5: Present** Sessions + Usage screenshots to Seth — his look is the slice's terminal gate.

---

## Self-review (done at authoring)

- **Spec coverage:** §3.3 Sessions — activity-runs browser + search + replay (Task 3); the activity↔OTel merge is deliberately scoped to "activity is the replayable spine, OTel stays in Usage" (ruling: a precise join is unreliable — activity records carry no trace_id — and the acceptance "findable + replayable" is fully met by activity records; noted as a Slice-3 refinement). §3.6 Usage — latency (Task 4 s1), error-classification (Task 4 s4, degraded to OTel-live tool grouping since the usage-audit taxonomy is not a standing feed — ruling), cost honesty deferred-to-A129 (Task 4 s2-3). §3.7/A129 — est. markers + unpriced-model note (Task 4 s2-3). §6 Slice 2 acceptance lands in Task 5.
- **Placeholder scan:** none (all code inline).
- **Type consistency:** `summarize_trace` new keys (`error_kinds`, `priced`) ↔ `_aggregate` reads (`r.get("error_kinds")`, `r.get("priced")`) ↔ Task 4 view reads (`agg.error_kinds`, `agg.unpriced_models`, `agg.p50_ms`); `ThreadModal`/`parseThread` exported from `/thread.js` ↔ imported by overview.js and sessions.js with the same names.
