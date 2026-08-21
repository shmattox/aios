# Dashboard v3 Slice 1 — Health + Gate panels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the env's two darkest high-value signals — standing-check reds (+ task fleet + source staleness) and the gate ledger (agreement/override) — as two new dashboard surfaces fed by read-only aggregation.

**Architecture:** One new aggregator module (`health_state.py`) building a health payload from files the engine already writes; the existing `/api/health` ping extended to serve it; a new `/api/gate-metrics` route promoting the ledger out of `/api/spend`; two new Preact views registered in the shell nav. Zero new write logic; advisory surfaces fail OPEN (an unreadable input renders as its own red, never as silence).

**Tech Stack:** Python 3 stdlib (aggregator + `http.server` routes), Preact+HTM no-build UI (vendored, `/lib.js` helpers), pytest.

**Spec:** `docs/superpowers/plans/../specs/2026-08-21-dashboard-v3-aaa-slice-design.md` §3.1, §3.2, §6 Slice 1.

## Global Constraints

- No build step, no new dependencies, no React Flow (spec §4).
- Server never writes state; new routes are GET-only read aggregations (A63 D2).
- Monochrome token sheet only — semantic status classes already exist (`warn` etc. in `ui/tokens.css`); no new palette.
- Every panel names its source and shows data age (stale-is-a-visible-badge, A63 D-"stale").
- Fail-open advisory posture: a broken input file must render as a visible red row, never hide the panel (Memory/decisions.md fail-safe-per-surface).
- Match existing idioms exactly: `Metric`/`ov-strip`/`ov-sect`/`uz-src` classes (see `ui/views/usage.js`), `api.get` + `.catch(() => {})`, route dispatch in `_api_get` (see `dashboard_server.py:311-412`), test harness `make_server` + tmp `env_root` fixture (see `engine/tools/tests/test_a63_dashboard_api.py:11-52`).
- Commit after every task — in a factory-managed repo, uncommitted work across a wait is unsafe (H131).
- This repo's suite: `python -m pytest engine -q` from `Projects/aios`.

---

### Task 1: Worktree + base

**Files:** none (setup)

- [ ] **Step 1:** From `C:\Users\sethh\Documents\Claude\Projects\aios`, confirm the Slice-0 factory drain state: `git log --oneline -5` and `git status --short`. If A125/A127/A128 have merged to `main`, base on that; if the drain is still running, base on current `main` and note that the final task rebases.
- [ ] **Step 2:** Create the isolated workspace per `superpowers:using-git-worktrees`: branch `v3-slice1-health-gate` from `main`. Do NOT reuse the factory's `.worktrees/factory-*` trees.
- [ ] **Step 3:** In the worktree, run `python -m pytest engine -q` — record the green baseline count before touching anything.

### Task 2: `health_state.py` aggregator (TDD)

**Files:**
- Create: `engine/dashboard/health_state.py`
- Test: `engine/tools/tests/test_health_state.py`

**Interfaces:**
- Produces: `health_state.summary(env_root: str) -> dict` with keys
  `{"ok": bool, "generated_utc": str|None, "standing": {"reds": int, "watch_expired": int, "greens": int, "checks": [ {id, kind, cadence, origin, on_violation, first_red, reason, status} ]}, "fleet": [ {task, last_run_utc, age_s} ], "sources": [ {name, path, age_s} ]}`.
  `ok` is False only when results.json was unreadable — in that case `standing.checks` still contains one synthetic entry `{"id": "standing-checks-unreadable", "status": "red", "reason": "<exception class>: <path>"}` so the failure is a visible red (fail-open).

Input shapes (real, verified 2026-08-21):
- `state/standing-checks/results.json`: `{"generated_utc": iso, "checks": [{"id","kind","cadence","origin","on_violation","last_run","first_red","reason","status"}], "watching_clear": [], "findings": []}`; `status ∈ {green, red, observed}`.
- Fleet: each dir `state/task-logs/<task>/` → newest mtime among `last-run.log` / `last-result.txt` (fleet is informational ages only — red/green judgment belongs to the `scheduled-task-fleet-healthy` standing check, don't invent a second threshold).
- Sources: fixed map — `brief-cache.json`, `factory/standup.json`, `factory/gate-metrics.json`, `queue.json`, `standing-checks/results.json`, newest `factory/spend-*.json` — each with `age_s` from mtime (None mtime → `age_s: null`, keep the row).

- [ ] **Step 1: Write the failing tests** in `engine/tools/tests/test_health_state.py`:

```python
import json, os, time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))
import health_state


def _env(tmp_path, results=None):
    (tmp_path / "state" / "standing-checks").mkdir(parents=True)
    (tmp_path / "state" / "factory").mkdir(parents=True)
    (tmp_path / "state" / "task-logs" / "aios-ingest").mkdir(parents=True)
    (tmp_path / "state" / "task-logs" / "aios-ingest" / "last-run.log").write_text("ok", encoding="utf-8")
    if results is not None:
        (tmp_path / "state" / "standing-checks" / "results.json").write_text(
            json.dumps(results), encoding="utf-8")
    return str(tmp_path)


RESULTS = {"generated_utc": "2026-08-21T10:56:16+00:00", "watching_clear": [], "findings": [],
           "checks": [
               {"id": "a", "kind": "standing", "cadence": "daily", "origin": "H1",
                "on_violation": "fix a", "last_run": "x", "first_red": "2026-08-19", "reason": "boom", "status": "red"},
               {"id": "b", "kind": "standing", "cadence": "daily", "origin": "H2",
                "on_violation": "", "last_run": "x", "first_red": None, "reason": None, "status": "green"},
               {"id": "c", "kind": "watch", "cadence": "daily", "origin": "H3",
                "on_violation": "", "last_run": "x", "first_red": None, "reason": "expired", "status": "observed"},
           ]}


def test_summary_counts_and_checks(tmp_path):
    s = health_state.summary(_env(tmp_path, RESULTS))
    assert s["ok"] is True
    assert s["standing"]["reds"] == 1 and s["standing"]["greens"] == 1
    assert s["standing"]["watch_expired"] == 1
    red = [c for c in s["standing"]["checks"] if c["status"] == "red"][0]
    assert red["id"] == "a" and red["on_violation"] == "fix a"


def test_fleet_and_sources(tmp_path):
    s = health_state.summary(_env(tmp_path, RESULTS))
    fleet = {f["task"]: f for f in s["fleet"]}
    assert "aios-ingest" in fleet and fleet["aios-ingest"]["age_s"] >= 0
    names = {r["name"] for r in s["sources"]}
    assert {"brief-cache", "gate-metrics", "standing-checks"} <= names
    missing = [r for r in s["sources"] if r["name"] == "queue"][0]
    assert missing["age_s"] is None            # absent file keeps its row, age null


def test_fail_open_on_unreadable_results(tmp_path):
    env = _env(tmp_path, None)
    (tmp_path / "state" / "standing-checks" / "results.json").write_text("{not json", encoding="utf-8")
    s = health_state.summary(env)
    assert s["ok"] is False
    assert any(c["id"] == "standing-checks-unreadable" and c["status"] == "red"
               for c in s["standing"]["checks"])
    assert s["standing"]["reds"] >= 1          # the synthetic red is counted


def test_missing_results_is_also_a_visible_red(tmp_path):
    s = health_state.summary(_env(tmp_path, None))
    assert s["ok"] is False
    assert any(c["id"] == "standing-checks-unreadable" for c in s["standing"]["checks"])
```

- [ ] **Step 2:** Run `python -m pytest engine/tools/tests/test_health_state.py -q` — expect FAIL (`No module named 'health_state'`).
- [ ] **Step 3: Implement** `engine/dashboard/health_state.py` (style-match `content_state.py`: module docstring stating purpose + read-only contract; stdlib only):

```python
"""health_state — read-only health aggregation for the dashboard.

Builds one payload from files the engine already writes: standing-check
results (A94 runner), the scheduled-task fleet's last-run ages, and the
age of each key state source. ADVISORY surface: fails OPEN — an unreadable
results.json becomes a synthetic red check, never a hidden panel.
Never writes anything.
"""
import json, os, time
from pathlib import Path

_SOURCES = [
    ("brief-cache",     "state/brief-cache.json"),
    ("standup",         "state/factory/standup.json"),
    ("gate-metrics",    "state/factory/gate-metrics.json"),
    ("queue",           "state/queue.json"),
    ("standing-checks", "state/standing-checks/results.json"),
]

_CHECK_FIELDS = ("id", "kind", "cadence", "origin", "on_violation",
                 "first_red", "reason", "status")


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return None


def _age(m, now):
    return max(0, int(now - m)) if m else None


def summary(env_root):
    env = Path(env_root)
    now = time.time()
    ok, generated, checks = True, None, []
    rp = env / "state" / "standing-checks" / "results.json"
    try:
        data = json.loads(rp.read_text(encoding="utf-8"))
        generated = data.get("generated_utc")
        for c in data.get("checks", []):
            checks.append({k: c.get(k) for k in _CHECK_FIELDS})
    except (OSError, ValueError) as e:
        ok = False
        checks.append({"id": "standing-checks-unreadable", "kind": "standing",
                       "cadence": None, "origin": "health_state fail-open",
                       "on_violation": "standing-check results could not be read - run standing_checks.py / check the nightly gather",
                       "first_red": None, "reason": "%s: %s" % (type(e).__name__, rp),
                       "status": "red"})
    reds = sum(1 for c in checks if c["status"] == "red")
    greens = sum(1 for c in checks if c["status"] == "green")
    watch_expired = sum(1 for c in checks if c["status"] == "observed")

    fleet = []
    logs = env / "state" / "task-logs"
    if logs.is_dir():
        for d in sorted(p for p in logs.iterdir() if p.is_dir()):
            m = max((x for x in (_mtime(d / "last-run.log"),
                                 _mtime(d / "last-result.txt")) if x), default=None)
            fleet.append({"task": d.name, "last_run_utc": m, "age_s": _age(m, now)})

    sources = []
    for name, rel in _SOURCES:
        sources.append({"name": name, "path": rel, "age_s": _age(_mtime(env / rel), now)})
    spends = sorted((env / "state" / "factory").glob("spend-*.json"))
    sources.append({"name": "spend", "path": "state/factory/spend-*.json",
                    "age_s": _age(_mtime(spends[-1]) if spends else None, now)})

    return {"ok": ok, "generated_utc": generated,
            "standing": {"reds": reds, "greens": greens,
                          "watch_expired": watch_expired, "checks": checks},
            "fleet": fleet, "sources": sources}
```

- [ ] **Step 4:** Run `python -m pytest engine/tools/tests/test_health_state.py -q` — expect all PASS.
- [ ] **Step 5:** Commit: `git add engine/dashboard/health_state.py engine/tools/tests/test_health_state.py && git commit -m "feat(dashboard): health_state read-only aggregator (standing checks + fleet + source ages)"`

### Task 3: server routes — `/api/health` extended, `/api/gate-metrics` new, spend slimmed (TDD)

**Files:**
- Modify: `engine/dashboard/dashboard_server.py` (route dispatch `_api_get` ~:311-330; `WATCHED` map ~:37-42; module imports ~:20-30)
- Test: `engine/tools/tests/test_a63_dashboard_api.py` (append tests; update `test_mtimes_lists_watched` and `test_spend_aggregates`)

**Interfaces:**
- Consumes: `health_state.summary(env_root: str) -> dict` (Task 2).
- Produces: `GET /api/health` → Task 2 payload + `{"env_root": str, "now": float}` merged in (keeps the old ping keys; `ok` now means "health inputs readable"). `GET /api/gate-metrics` → the raw `state/factory/gate-metrics.json` object + `_age_s: int|None` (same `_file_with_age` idiom as `/api/standup`). `GET /api/spend` → `{"days": [...]}` ONLY (the `gate_metrics` key is REMOVED — promoted to its own route; its lone reader was a test). `WATCHED` gains `"standing": "state/standing-checks/results.json"` so SSE + the age badge fire on the nightly refresh.

- [ ] **Step 1: Write the failing tests** (append to `test_a63_dashboard_api.py`; also write a results.json + a task-logs dir into the existing `env_root` fixture so `/api/health` has data):

In the `env_root` fixture body add:

```python
    (tmp_path / "state" / "standing-checks").mkdir(parents=True)
    (tmp_path / "state" / "standing-checks" / "results.json").write_text(json.dumps({
        "generated_utc": "2026-08-21T10:00:00+00:00", "watching_clear": [], "findings": [],
        "checks": [{"id": "x", "kind": "standing", "cadence": "daily", "origin": "t",
                    "on_violation": "fix", "last_run": "y", "first_red": None,
                    "reason": None, "status": "red"}]}), encoding="utf-8")
    (tmp_path / "state" / "task-logs" / "aios-ingest").mkdir(parents=True)
    (tmp_path / "state" / "task-logs" / "aios-ingest" / "last-run.log").write_text("ok", encoding="utf-8")
```

New tests:

```python
def test_health_aggregation(server):
    h = _get_json(server, "/api/health")
    assert h["ok"] is True and "now" in h and "env_root" in h
    assert h["standing"]["reds"] == 1
    assert any(f["task"] == "aios-ingest" for f in h["fleet"])
    assert any(s["name"] == "gate-metrics" for s in h["sources"])


def test_gate_metrics_route(server):
    g = _get_json(server, "/api/gate-metrics")
    assert g["generated"] == "2026-07-20"
    assert g["_age_s"] >= 0


def test_spend_no_longer_carries_gate_metrics(server):
    s = _get_json(server, "/api/spend")
    assert "gate_metrics" not in s
```

Update the two existing assertions: `test_mtimes_lists_watched` expected set gains `"standing"`; `test_spend_aggregates` drops its `gate_metrics` line.

- [ ] **Step 2:** Run `python -m pytest engine/tools/tests/test_a63_dashboard_api.py -q` — expect the three new tests FAIL (old ping payload / 404 / key present) and the two updated ones FAIL.
- [ ] **Step 3: Implement** in `dashboard_server.py`: import `health_state` next to the other aggregator imports; add `"standing": "state/standing-checks/results.json"` to `WATCHED`; replace the `/api/health` branch body with

```python
        if route == "/api/health":
            data = health_state.summary(str(env))
            data.update({"env_root": str(env), "now": time.time()})
            return self._send_json(data)
```

add after the `/api/spend` branch

```python
        if route == "/api/gate-metrics":
            return self._file_with_age(env / WATCHED["gate_metrics"])
```

and in the `/api/spend` branch delete the `"gate_metrics": ...` entry so it returns `{"days": days}`.

- [ ] **Step 4:** Run `python -m pytest engine/tools/tests/test_a63_dashboard_api.py -q` — expect all PASS; then the full suite `python -m pytest engine -q` — expect green (any other test asserting the spend payload or WATCHED set must be updated in this task, not skipped).
- [ ] **Step 5:** Commit: `git add engine/dashboard/dashboard_server.py engine/tools/tests/test_a63_dashboard_api.py && git commit -m "feat(dashboard): /api/health aggregation + /api/gate-metrics route; spend slimmed"`

### Task 4: Health view + nav + Overview tile

**Files:**
- Create: `engine/dashboard/ui/views/health.js`
- Modify: `engine/dashboard/ui/app.js` (imports ~:4-10, `ICONS` ~:22-31, `NAV` ~:41-51, chord map ~:117-125)
- Modify: `engine/dashboard/ui/views/overview.js` (metric strip — add a Health tile)
- Modify: `engine/dashboard/ui/tokens.css` or the shared stylesheet ONLY if a needed class is missing (prefer existing `ov-*`/`uz-*`/`warn` classes; new classes use the `hl-*` prefix and existing tokens)

**Interfaces:**
- Consumes: `GET /api/health` (Task 3 payload).
- Produces: `export function HealthView()` from `/views/health.js`; nav key `"health"`, chord `g h`; Overview tile navigates to `#/health`.

- [ ] **Step 1: Write `health.js`** (conventions from `usage.js` — one fetch on mount, `Metric` strip, sections with `ov-sect` + `uz-src` source badges):

```javascript
// Health — the env's invariants, rendered. Standing-check reds (A94 runner), the scheduled-task
// fleet's last-run ages, and per-source staleness. Read-only; an unreadable input arrives as a
// synthetic red row from the server (fail-open), never as a hidden panel.
import { html, api, useState, useEffect } from "/lib.js";

const fmtAge = (s) => s == null ? "—" : s < 90 ? `${s}s` : s < 5400 ? `${Math.round(s / 60)}m`
  : s < 172800 ? `${Math.round(s / 3600)}h` : `${Math.round(s / 86400)}d`;

function Metric({ k, v, sub, warn }) {
  return html`<div class="ov-cell"><div class="ov-k">${k}</div>
    <div class="ov-v ${warn ? "warn" : ""}">${v}</div><div class="ov-s">${sub || ""}</div></div>`;
}

const Dot = ({ status }) => html`<span class="hl-dot hl-${status}" title=${status}></span>`;

export function HealthView() {
  const [h, setH] = useState(null);

  useEffect(() => { api.get("/api/health").then(setH).catch(() => setH({ error: true })); }, []);

  if (h == null) return html`<section class="view"><div class="viewhead"><h1>Health</h1></div><p class="stub">…</p></section>`;
  if (h.error) return html`<section class="view"><div class="viewhead"><h1>Health</h1></div>
    <p class="stub">The dashboard server did not answer /api/health — it is itself the red.</p></section>`;

  const st = h.standing || { checks: [] };
  const order = { red: 0, observed: 1, green: 2 };
  const checks = [...st.checks].sort((a, b) => (order[a.status] ?? 3) - (order[b.status] ?? 3) || String(a.id).localeCompare(String(b.id)));
  const fleet = [...(h.fleet || [])].sort((a, b) => (b.age_s ?? Infinity) - (a.age_s ?? Infinity));
  const stale = (h.sources || []).filter((s) => s.age_s == null || s.age_s > 172800).length;

  return html`<section class="view">
    <div class="viewhead"><h1>Health</h1><span class="sub">standing checks · fleet · staleness</span></div>

    <div class="ov-strip">
      <${Metric} k="standing reds" v=${st.reds} warn=${st.reds > 0} sub=${`${st.greens} green`} />
      <${Metric} k="watch expired" v=${st.watch_expired} warn=${st.watch_expired > 0} sub="unobserved" />
      <${Metric} k="fleet" v=${fleet.length} sub="scheduled tasks seen" />
      <${Metric} k="stale sources" v=${stale} warn=${stale > 0} sub="> 48h or missing" />
    </div>

    <h3 class="ov-sect">Standing checks <span class="uz-src">· state/standing-checks/results.json · ${h.generated_utc || "?"}</span></h3>
    <div class="hl-list">
      ${checks.map((c) => html`<div class="hl-row hl-row-${c.status}" key=${c.id}>
        <${Dot} status=${c.status} />
        <span class="hl-id">${c.id}</span>
        <span class="hl-meta">${c.kind}${c.cadence ? " · " + c.cadence : ""}${c.first_red ? " · red since " + c.first_red : ""}</span>
        ${c.status !== "green" ? html`<div class="hl-why">${c.reason ? c.reason + " — " : ""}${c.on_violation || ""}</div>` : null}
      </div>`)}
    </div>

    <h3 class="ov-sect">Task fleet <span class="uz-src">· state/task-logs · last-run age (health judgment lives in the fleet standing check)</span></h3>
    <div class="hl-list">
      ${fleet.map((f) => html`<div class="hl-row" key=${f.task}>
        <span class="hl-id">${f.task}</span><span class="hl-meta num">${fmtAge(f.age_s)}</span>
      </div>`)}
    </div>

    <h3 class="ov-sect">Sources <span class="uz-src">· mtime age per state file</span></h3>
    <div class="hl-list">
      ${(h.sources || []).map((s) => html`<div class="hl-row" key=${s.name}>
        <span class="hl-id">${s.name}</span>
        <span class="hl-meta">${s.path}</span>
        <span class="hl-meta num ${s.age_s == null || s.age_s > 172800 ? "warn" : ""}">${fmtAge(s.age_s)}</span>
      </div>`)}
    </div>
  </section>`;
}
```

- [ ] **Step 2: Style** — add to the stylesheet that holds the `ov-*` classes, using existing tokens only: `.hl-list` (column flex, hairline dividers), `.hl-row` (grid: dot / id / meta, `gap`), `.hl-dot` (8px circle; `hl-green` = `--ok`-class token if present else muted, `hl-red` = the existing warn/critical token, `hl-observed` = muted), `.hl-why` (full-width second line, muted). Reuse whatever semantic tokens `tokens.css` already defines — introduce no new hex values.
- [ ] **Step 3: Register** in `app.js`: `import { HealthView } from "/views/health.js";`; `ICONS.health = html`<svg class="ic" viewBox="0 0 16 16"><path d="M1.5 8h3l1.5-4 2.5 8 1.5-4h4.5"/></svg>``; NAV entry `{ key: "health", label: "Health", view: HealthView }` after `usage`; chord `if (e.key === "h") { location.hash = "#/health"; return; }` in the `g` block.
- [ ] **Step 4: Overview tile** — in `overview.js`'s metric strip add a fifth tile fetching `/api/health` alongside its existing loads: value = standing reds (warn style when > 0), sub = "standing checks", `onClick` → `location.hash = "#/health"` (wrap in the same clickable pattern the other tiles use, or a plain `cursor:pointer` div if none are clickable — match what's there, don't invent a new component).
- [ ] **Step 5: Browser check** — `python engine/dashboard/dashboard_server.py --open` against the REAL env root; verify with the preview tools: Health nav item renders, 18 checks listed with today's 6 reds on top and their `on_violation` text, fleet ages plausible, gate-metrics source row shows the ~24d age in warn. Console clean (`read_console_messages`).
- [ ] **Step 6:** Commit: `git add engine/dashboard/ui && git commit -m "feat(dashboard): Health view - standing checks, task fleet, source staleness"`

### Task 5: Gate view + nav

**Files:**
- Create: `engine/dashboard/ui/views/gate.js`
- Modify: `engine/dashboard/ui/app.js` (import, `ICONS.gate`, NAV entry after `health`, chord — `g` then `t` since `g g` collides with the chord starter)

**Interfaces:**
- Consumes: `GET /api/gate-metrics` → `{generated, windows: {all: {n, totals: {accepted, rejected, reverted}, reverts_hist, deciders: {human, auto, scheduled, unknown}, agreement: {agree, override, hold, na}, override_ids: []}}, _age_s}`.
- Produces: `export function GateView()`; nav key `"gate"`.

- [ ] **Step 1: Write `gate.js`** (reuse the `Bar` idiom from `usage.js`; keep every number sourced):

```javascript
// Gate — the differentiator, rendered. The review-gate ledger: decisions, who decided,
// and how often the human overrode the machine's recommendation. Source:
// state/factory/gate-metrics.json (engine/tools/gate_metrics.py; freshness = A128).
import { html, api, useState, useEffect } from "/lib.js";

const pct = (a, b) => b ? Math.round((a / b) * 100) + "%" : "—";
const fmtAge = (s) => s == null ? "?" : s < 172800 ? `${Math.round(s / 3600)}h` : `${Math.round(s / 86400)}d`;

function Metric({ k, v, sub, warn }) {
  return html`<div class="ov-cell"><div class="ov-k">${k}</div>
    <div class="ov-v ${warn ? "warn" : ""}">${v}</div><div class="ov-s">${sub || ""}</div></div>`;
}

function Bar({ label, n, of }) {
  const p = of ? Math.round((n / of) * 100) : 0;
  return html`<div class="uz-row"><span class="uz-lbl">${label}</span>
    <span class="uz-track"><span class="uz-fill" style="width:${p}%"></span></span>
    <span class="uz-n num">${n}</span></div>`;
}

export function GateView() {
  const [g, setG] = useState(null);
  useEffect(() => { api.get("/api/gate-metrics").then(setG).catch(() => setG({ error: true })); }, []);

  if (g == null) return html`<section class="view"><div class="viewhead"><h1>Gate</h1></div><p class="stub">…</p></section>`;
  const w = g.windows?.all;
  if (g.error || !w) return html`<section class="view"><div class="viewhead"><h1>Gate</h1></div>
    <p class="stub">No gate ledger — state/factory/gate-metrics.json is missing or empty (regeneration is aios A128).</p></section>`;

  const ag = w.agreement || {}, de = w.deciders || {}, to = w.totals || {};
  const decided = (ag.agree || 0) + (ag.override || 0);
  const stale = (g._age_s || 0) > 172800;

  return html`<section class="view">
    <div class="viewhead"><h1>Gate</h1>
      <span class="sub">review ledger · generated ${g.generated}
        ${stale ? html` · <span class="warn">${fmtAge(g._age_s)} old</span>` : ""}</span></div>

    <div class="ov-strip">
      <${Metric} k="decisions" v=${w.n} sub="all time" />
      <${Metric} k="agreement" v=${pct(ag.agree, decided)} sub=${`${ag.agree || 0} agree · ${ag.override || 0} override`} />
      <${Metric} k="held for human" v=${ag.hold || 0} sub="review-lane holds" />
      <${Metric} k="reverts" v=${(to.reverted || 0) + (w.reverts_hist || 0)} warn=${(to.reverted || 0) > 0} sub="ledger + historical" />
    </div>

    <h3 class="ov-sect">Outcomes <span class="uz-src">· gate-metrics totals</span></h3>
    <div class="uz-bars-list">
      <${Bar} label="accepted" n=${to.accepted || 0} of=${w.n} />
      <${Bar} label="rejected" n=${to.rejected || 0} of=${w.n} />
      <${Bar} label="reverted" n=${to.reverted || 0} of=${w.n} />
    </div>

    <h3 class="ov-sect">Deciders <span class="uz-src">· who shipped it</span></h3>
    <div class="uz-bars-list">
      ${["human", "auto", "scheduled", "unknown"].map((k) => html`<${Bar} key=${k} label=${k} n=${de[k] || 0} of=${w.n} />`)}
    </div>

    <h3 class="ov-sect">Overrides <span class="uz-src">· human decided against the recommendation · ${(w.override_ids || []).length} items</span></h3>
    ${(w.override_ids || []).length
      ? html`<div class="hl-list">${w.override_ids.slice(0, 20).map((id) => html`<div class="hl-row" key=${id}><span class="hl-id">${id}</span></div>`)}
          ${w.override_ids.length > 20 ? html`<p class="uz-note">…and ${w.override_ids.length - 20} more in the ledger.</p>` : ""}</div>`
      : html`<p class="stub">No overrides recorded.</p>`}
  </section>`;
}
```

- [ ] **Step 2: Register** in `app.js`: import; `ICONS.gate = html`<svg class="ic" viewBox="0 0 16 16"><path d="M3 13.5V5.5a5 5 0 0 1 10 0v8M1.5 13.5h13M8 8.5v2"/></svg>``; NAV `{ key: "gate", label: "Gate", view: GateView }` after `health`; chord `g t` → `#/gate`; add both new chords to the `#keys` footer hints if the pattern there covers all views (match what's listed — don't overflow the strip).
- [ ] **Step 3: Browser check** — reload; Gate view renders n=1270, agreement ≈ 92% (917/996), decider bars, the stale-age warning (until A128's regen lands), override ids list capped at 20. Console clean.
- [ ] **Step 4:** Run the full suite once more: `python -m pytest engine -q` — green.
- [ ] **Step 5:** Commit: `git add engine/dashboard/ui && git commit -m "feat(dashboard): Gate view - agreement, deciders, overrides from the review ledger"`

### Task 6: Verification, review, merge

**Files:** none (process)

- [ ] **Step 1: Full browser verification pass** against the real env root: every nav item still routes; Health + Gate render real data; screenshot both views (the Rule-Zero evidence for Seth's look). Note: per env memory the in-app browser pane may not screenshot — if so, use Playwright (the `psGearCamera`-era pattern: a short script driving `http://127.0.0.1:8642`) or capture via the preview tools' DOM checks + a native screenshot.
- [ ] **Step 2: Rebase onto latest `main`** (`git fetch . main` from the worktree / `git rebase main`) — Slice-0 (A125/A127/A128) may have merged mid-build; resolve conflicts in `dashboard_server.py`/`app.js` (A127 deletes the panels + `/api/standup`/`/api/brief` routes — deletions compose with these additions). Re-run `python -m pytest engine -q` after rebase.
- [ ] **Step 3: Fresh-context review** — dispatch an independent reviewer subagent (never the builder) on the whole branch diff (`git diff main...HEAD`) against the spec §3.1/§3.2 + Global Constraints; CRITICAL findings loop back into a fix pass. (Use a direct reviewer agent, NOT the saved review-gate Workflow — it false-PASSes on linked worktrees per Memory.)
- [ ] **Step 4: Merge** per `superpowers:finishing-a-development-branch`: ff-merge to `main`, delete branch + worktree (careful cleanup — `git worktree remove`, never `rm -rf` from inside; the 2026-08-13 gotcha), run the suite once on merged `main`.
- [ ] **Step 5: Ledger** — annotate A130 in `BACKLOG.md` with a dated slice-1-shipped sub-note (item stays open; slices 2-4 remain), commit with the merge push. Present both screenshots to Seth — **his look is the slice's terminal gate**.

---

## Self-review (done at authoring)

- Spec coverage: §3.1 (strip + drill page ✓ via Overview tile + HealthView; fleet ✓; staleness chips ✓; `/api/health` ✓; fail-open ✓), §3.2 (all listed fields rendered ✓; override ids link into Board deferred — rendered as text list, acceptable under YAGNI since Board has no id-addressable deep link yet; noted for slice 3), §6 Slice 1 acceptance items each land in Tasks 4-6.
- Placeholders: none (all code inline).
- Type consistency: `health_state.summary` payload keys match Task 3's route tests and Task 4's view reads (`standing.reds`, `fleet[].task`, `sources[].name`); `_file_with_age` produces `_age_s` consumed by `gate.js`.
