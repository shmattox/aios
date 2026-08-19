# Pipeline Flow View (Slice 1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A live master pipeline flowchart — `backlog → brainstorm → spec → implement → subagent → review → gate → complete` — rendered in React Flow, fed by a real server-side aggregator over the backlog + factory + OTel, served inside the existing dashboard behind an iframe.

**Architecture:** A pure Python aggregator (`pipeline_state.py`) classifies every backlog item into one of 8 stages and emits `{stages, edges, flows}`; the dashboard server serves it at `/api/pipeline` and serves a scoped Vite+React+React Flow app (`flow-app/dist/`) at `/pipeline/`; a new "Pipeline" dashboard nav item iframes it. Item→stage is a single deterministic, unit-tested classifier; the animation is items actually moving (poll-to-poll diff).

**Tech Stack:** Python 3 stdlib (server, aggregator); the React app (`flow-app/`) is the *only* place with a build/deps — Vite + React + React Flow (`reactflow`), isolated behind an iframe.

**Spec:** `docs/superpowers/specs/2026-08-19-pipeline-flow-view-design.md`

## Global Constraints

- **Repo:** all work in the **aios repo** (`Projects/aios`). Run pytest and git from there. Tests live in `engine/tools/tests/` with a `sys.path` insert to `engine/dashboard` (mirror `test_otel_runs.py`).
- **Server is stdlib-only.** No pip deps in `pipeline_state.py` or `dashboard_server.py`. The React app in `flow-app/` is the ONLY place with a build/`node_modules`.
- **Aggregator is best-effort:** a missing/idle/unreadable source contributes zero and NEVER raises; `/api/pipeline` never 500s. The pure classifier/model functions have no I/O.
- **Build output committed:** `flow-app/dist/` is committed (the dashboard serves it static, no runtime build). `flow-app/node_modules` is git-ignored.
- **Palette:** the React app's own CSS uses the dashboard B&W tokens verbatim — `--bg-0:#0e0f11 --bg-1:#16171a --bg-2:#1c1d21 --text-0:#e8e9ec --text-1:#9a9ca3 --text-2:#63656c --accent:#6e79d6 --ok:#4cc38a --border:rgba(255,255,255,.08)`.
- **Fixed layout:** the 8-node DAG is hand-positioned left→right (no dagre/elk).
- **Test command:** `python -m pytest engine/tools/tests/test_pipeline_state.py -v` (from `Projects/aios`).

---

### Task 1: Stage constants + `classify_stage`

**Files:**
- Create: `engine/dashboard/pipeline_state.py`
- Test: `engine/tools/tests/test_pipeline_state.py`

**Interfaces:**
- Produces:
  - `STAGES` = ordered list of `(id, label)` tuples, exactly:
    `("backlog","Backlog"), ("brainstorm","Brainstorm"), ("spec","Spec"), ("implement","Implement"), ("subagent","Subagent builds"), ("review","Review"), ("gate","Gate"), ("complete","Complete")`.
  - `EDGES` = the linear spine `[(a,b) for consecutive STAGES]`.
  - `classify_stage(item, ctx) -> str` — `item` is a b2g parsed dict (`id`, `title`, `status`, `seed`, `factory_ready`); `ctx` is `{"spec_ids":set, "plan_ids":set, "active_ids":set, "review_ids":set, "held_ids":set, "done_ids":set}`. Returns one stage id, most-advanced-wins.

- [ ] **Step 1: Write the failing test**

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "dashboard"))
import pipeline_state as p

def _ctx(**kw):
    base = {k: set() for k in ("spec_ids", "plan_ids", "active_ids", "review_ids", "held_ids", "done_ids")}
    base.update(kw); return base

def test_stages_and_edges_shape():
    assert [s[0] for s in p.STAGES] == ["backlog","brainstorm","spec","implement","subagent","review","gate","complete"]
    assert p.EDGES == [("backlog","brainstorm"),("brainstorm","spec"),("spec","implement"),
                       ("implement","subagent"),("subagent","review"),("review","gate"),("gate","complete")]

def test_classify_precedence_most_advanced_wins():
    it = {"id": "A1", "status": "open", "seed": False, "factory_ready": False}
    assert p.classify_stage(it, _ctx()) == "backlog"
    assert p.classify_stage({**it, "seed": True}, _ctx()) == "brainstorm"
    assert p.classify_stage(it, _ctx(spec_ids={"A1"})) == "spec"
    assert p.classify_stage(it, _ctx(plan_ids={"A1"})) == "implement"
    assert p.classify_stage({**it, "factory_ready": True}, _ctx()) == "implement"
    assert p.classify_stage(it, _ctx(active_ids={"A1"})) == "subagent"
    assert p.classify_stage(it, _ctx(review_ids={"A1"})) == "review"
    assert p.classify_stage(it, _ctx(held_ids={"A1"})) == "gate"
    assert p.classify_stage(it, _ctx(done_ids={"A1"})) == "complete"
    assert p.classify_stage({**it, "status": "done"}, _ctx()) == "complete"
    # a spec-having item that is ALSO in review classifies as review (more advanced)
    assert p.classify_stage(it, _ctx(spec_ids={"A1"}, review_ids={"A1"})) == "review"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest engine/tools/tests/test_pipeline_state.py -v`
Expected: FAIL (`ModuleNotFoundError: pipeline_state` or `AttributeError`).

- [ ] **Step 3: Write minimal implementation**

Create `engine/dashboard/pipeline_state.py`:

```python
"""Pipeline-state aggregator for the dashboard's master Flow view. Classifies every backlog
item into one of 8 fixed factory stages and emits {stages, edges, flows}. Pure classifier +
model (unit-tested); the source gather is best-effort (a down/idle source contributes zero,
never raises)."""

STAGES = [
    ("backlog", "Backlog"), ("brainstorm", "Brainstorm"), ("spec", "Spec"),
    ("implement", "Implement"), ("subagent", "Subagent builds"),
    ("review", "Review"), ("gate", "Gate"), ("complete", "Complete"),
]
EDGES = [(STAGES[i][0], STAGES[i + 1][0]) for i in range(len(STAGES) - 1)]
_ORDER = {sid: i for i, (sid, _) in enumerate(STAGES)}


def classify_stage(item, ctx):
    """Most-advanced-stage-wins. `ctx` sets are id-membership per advanced stage."""
    iid = item.get("id") or ""
    if item.get("status") == "done" or iid in ctx["done_ids"]:
        return "complete"
    if iid in ctx["held_ids"]:
        return "gate"
    if iid in ctx["review_ids"]:
        return "review"
    if iid in ctx["active_ids"]:
        return "subagent"
    if iid in ctx["plan_ids"] or item.get("factory_ready"):
        return "implement"
    if iid in ctx["spec_ids"]:
        return "spec"
    if item.get("seed"):
        return "brainstorm"
    return "backlog"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest engine/tools/tests/test_pipeline_state.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd Projects/aios
git add engine/dashboard/pipeline_state.py engine/tools/tests/test_pipeline_state.py
git commit -m "feat(pipeline): STAGES/EDGES + classify_stage (pure stage classifier)"
```

---

### Task 2: `build_model` — counts, edges, flows

**Files:**
- Modify: `engine/dashboard/pipeline_state.py`
- Test: `engine/tools/tests/test_pipeline_state.py`

**Interfaces:**
- Consumes: `classify_stage`, `STAGES`, `EDGES`, `_ORDER` (Task 1).
- Produces: `build_model(items, ctx, prev_stage_by_id=None) -> dict` = `{"stages": [{"id","label","count","items":[{"id","title","repo"}]}], "edges": [{"from","to"}], "flows": [{"item_id","from","to"}], "stage_by_id": {id: stage_id}}`. `flows` = items whose stage advanced FORWARD since `prev_stage_by_id` (a backward move or first-seen item is not a flow). `items` per stage is capped at 12.

- [ ] **Step 1: Write the failing test**

```python
def test_build_model_counts_edges_and_forward_flows():
    items = [
        {"id": "A1", "status": "open", "seed": True, "repo": "aios", "title": "seed one"},
        {"id": "A2", "status": "open", "seed": False, "repo": "aios", "title": "backlog two"},
        {"id": "A3", "status": "done", "seed": False, "repo": "aios", "title": "done three"},
    ]
    m = p.build_model(items, _ctx(), prev_stage_by_id={"A1": "backlog"})
    by = {s["id"]: s for s in m["stages"]}
    assert by["brainstorm"]["count"] == 1 and by["brainstorm"]["items"][0]["id"] == "A1"
    assert by["backlog"]["count"] == 1 and by["complete"]["count"] == 1
    assert {"from": "backlog", "to": "brainstorm"} in m["edges"]
    # A1 moved backlog -> brainstorm (forward) => a flow; A2/A3 first-seen => no flow
    assert m["flows"] == [{"item_id": "A1", "from": "backlog", "to": "brainstorm"}]
    assert m["stage_by_id"]["A1"] == "brainstorm"

def test_build_model_backward_and_unchanged_are_not_flows():
    items = [{"id": "B1", "status": "open", "seed": False, "title": "x"}]
    # prev said B1 was in review; now it's backlog (backward) -> NOT a flow
    m = p.build_model(items, _ctx(), prev_stage_by_id={"B1": "review"})
    assert m["flows"] == []
    # unchanged -> not a flow
    m2 = p.build_model(items, _ctx(), prev_stage_by_id={"B1": "backlog"})
    assert m2["flows"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest engine/tools/tests/test_pipeline_state.py::test_build_model_counts_edges_and_forward_flows -v`
Expected: FAIL (`AttributeError: build_model`).

- [ ] **Step 3: Write minimal implementation**

Append to `pipeline_state.py`:

```python
def build_model(items, ctx, prev_stage_by_id=None):
    stage_items = {sid: [] for sid, _ in STAGES}
    cur = {}
    for it in items:
        sid = classify_stage(it, ctx)
        iid = it.get("id") or ""
        cur[iid] = sid
        stage_items[sid].append({"id": it.get("id"), "title": it.get("title"), "repo": it.get("repo")})
    stages = [{"id": sid, "label": lbl, "count": len(stage_items[sid]), "items": stage_items[sid][:12]}
              for sid, lbl in STAGES]
    edges = [{"from": a, "to": b} for a, b in EDGES]
    flows = []
    for iid, sid in cur.items():
        prev = (prev_stage_by_id or {}).get(iid)
        if prev and prev != sid and _ORDER.get(sid, 0) > _ORDER.get(prev, 0):   # forward only
            flows.append({"item_id": iid, "from": prev, "to": sid})
    return {"stages": stages, "edges": edges, "flows": flows, "stage_by_id": cur}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest engine/tools/tests/test_pipeline_state.py -v`
Expected: PASS (all Task 1 + Task 2 tests).

- [ ] **Step 5: Commit**

```bash
cd Projects/aios
git add engine/dashboard/pipeline_state.py engine/tools/tests/test_pipeline_state.py
git commit -m "feat(pipeline): build_model — stage counts, edges, forward-only flows"
```

---

### Task 3: `gather` — the best-effort source layer

**Files:**
- Modify: `engine/dashboard/pipeline_state.py`
- Test: `engine/tools/tests/test_pipeline_state.py`

**Interfaces:**
- Consumes: `build_model`.
- Produces: `gather(env_root) -> (items, ctx)` — best-effort: parses every `BACKLOG.md` (via `backlog_parse.parse_backlog`), derives `ctx` id-sets from docs filenames, factory activity, and the brief held cache. Never raises. Also `model(env_root, prev_stage_by_id=None) -> dict` = `build_model(*gather(env_root), prev_stage_by_id)` — the one call the server makes.

- [ ] **Step 1: Write the failing test**

```python
def test_gather_reads_backlogs_and_specs_best_effort(tmp_path):
    (tmp_path / "state").mkdir(); (tmp_path / "profile").mkdir()  # so it reads as an env root
    (tmp_path / "BACKLOG.md").write_text(
        "## Open\n- [ ] **Z1** — needs a spec.\n  - acceptance: x\n"
        "- [ ] **Z2** — has a spec.\n  - acceptance: x\n", encoding="utf-8")
    specs = tmp_path / "docs" / "superpowers" / "specs"; specs.mkdir(parents=True)
    (specs / "2026-08-19-z2-thing-design.md").write_text("# Z2 thing\nbody", encoding="utf-8")
    items, ctx = p.gather(str(tmp_path))
    ids = {it["id"] for it in items}
    assert "Z1" in ids and "Z2" in ids
    assert "Z2" in ctx["spec_ids"] and "Z1" not in ctx["spec_ids"]
    m = p.model(str(tmp_path))
    by = {s["id"]: s for s in m["stages"]}
    assert by["backlog"]["count"] == 1 and by["spec"]["count"] == 1   # Z1 backlog, Z2 spec

def test_gather_missing_sources_never_raises(tmp_path):
    (tmp_path / "state").mkdir(); (tmp_path / "profile").mkdir()
    items, ctx = p.gather(str(tmp_path))          # no backlogs, no docs, no activity
    assert items == [] and all(v == set() for v in ctx.values())
    assert p.model(str(tmp_path))["stages"][0]["count"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest engine/tools/tests/test_pipeline_state.py::test_gather_reads_backlogs_and_specs_best_effort -v`
Expected: FAIL (`AttributeError: gather`).

- [ ] **Step 3: Write minimal implementation**

Add imports at the top of `pipeline_state.py` (after the docstring) and the functions. `backlog_parse` is on `sys.path` because `dashboard_server` inserts `engine/tools`; for standalone/test use, insert it defensively.

```python
import os
import re
import sys
import glob
import json

_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
try:
    from backlog_parse import parse_backlog
except Exception:                                  # pragma: no cover - defensive
    def parse_backlog(text):
        return []

_ID_RE = re.compile(r"\b([A-Za-z]{1,4}\d+[a-z]?)\b")


def _backlog_files(env_root):
    out = []
    root_bl = os.path.join(env_root, "BACKLOG.md")
    if os.path.isfile(root_bl):
        out.append(("env-ops", root_bl))
    proj = os.path.join(env_root, "Projects")
    if os.path.isdir(proj):
        for d in sorted(os.listdir(proj)):
            bl = os.path.join(proj, d, "BACKLOG.md")
            if os.path.isfile(bl):
                out.append((d, bl))
    return out


def _ids_in_docs(env_root, subdir):
    """Item ids that appear in any docs/superpowers/<subdir>/*.md filename or first line."""
    ids = set()
    for f in glob.glob(os.path.join(env_root, "**", "docs", "superpowers", subdir, "*.md"), recursive=True):
        ids.update(_ID_RE.findall(os.path.basename(f)))
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                ids.update(_ID_RE.findall(fh.readline()))
        except OSError:
            pass
    return ids


def _active_and_review_ids(env_root):
    """Live factory drains → subagent/review stage, from state/activity/factory-*.json."""
    active, review = set(), set()
    import time as _t
    now = _t.time()
    for f in glob.glob(os.path.join(env_root, "state", "activity", "factory-*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        if rec.get("status") != "running" or (now - rec.get("heartbeat", 0)) > 90:
            continue
        detail = str(rec.get("detail") or "").lower()
        bucket = review if "review" in detail or "gate" in detail else active
        for i in rec.get("item_ids") or []:
            bucket.add(i)
    return active, review


def _held_ids(env_root):
    for name in ("brief-cache.json", "brief.json"):
        f = os.path.join(env_root, "state", name)
        if os.path.isfile(f):
            try:
                with open(f, encoding="utf-8") as fh:
                    d = json.load(fh)
                return {h.get("id") for h in (d.get("held") or []) if h.get("id")}
            except (OSError, ValueError):
                return set()
    return set()


def gather(env_root):
    items = []
    done_ids = set()
    for repo, bl in _backlog_files(env_root):
        try:
            with open(bl, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for it in parse_backlog(text):
            it["repo"] = repo
            items.append(it)
            if it.get("status") == "done" and it.get("id"):
                done_ids.add(it["id"])
    active, review = _active_and_review_ids(env_root)
    ctx = {
        "spec_ids": _ids_in_docs(env_root, "specs"),
        "plan_ids": _ids_in_docs(env_root, "plans"),
        "active_ids": active, "review_ids": review,
        "held_ids": _held_ids(env_root), "done_ids": done_ids,
    }
    # an item can't be both spec- and plan-having in two stages: plan wins (handled by classify order)
    return items, ctx


def model(env_root, prev_stage_by_id=None):
    items, ctx = gather(env_root)
    return build_model(items, ctx, prev_stage_by_id)
```

Note: `parse_backlog` returns items with `id`/`status`/`seed`/`factory_ready` per `backlog_parse`; if a field is absent `classify_stage`'s `.get()` calls default safely.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest engine/tools/tests/test_pipeline_state.py -v`
Expected: PASS (all tasks). If `parse_backlog`'s item dict lacks `factory_ready`, that's fine — `classify_stage` uses `.get`.

- [ ] **Step 5: Commit**

```bash
cd Projects/aios
git add engine/dashboard/pipeline_state.py engine/tools/tests/test_pipeline_state.py
git commit -m "feat(pipeline): gather — best-effort sources (backlogs, specs/plans, factory, held)"
```

---

### Task 4: Server — `/api/pipeline` + serve `/pipeline/*`

**Files:**
- Modify: `engine/dashboard/dashboard_server.py`
- Test: `engine/tools/tests/test_pipeline_state.py` (a server-route test using the existing dashboard test harness pattern)

**Interfaces:**
- Consumes: `pipeline_state.model` (Task 3).
- Produces: `GET /api/pipeline` → the model JSON, with the server holding a per-process `_prev_stage_by_id` so successive polls compute `flows`. `GET /pipeline/` and `/pipeline/<asset>` → static files from `engine/dashboard/flow-app/dist/` (index.html for the bare path).

- [ ] **Step 1: Write the failing test**

```python
def test_api_pipeline_route_returns_model(tmp_path, monkeypatch):
    import importlib
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "dashboard"))
    ds = importlib.import_module("dashboard_server")
    (tmp_path / "state").mkdir(); (tmp_path / "profile").mkdir()
    (tmp_path / "BACKLOG.md").write_text("## Open\n- [ ] **Q1** — x.\n  - acceptance: y\n", encoding="utf-8")
    srv = ds.make_server(str(tmp_path), port=0)
    # call the pipeline model directly through the module the handler uses (route logic is thin)
    import pipeline_state as ps
    m = ps.model(str(tmp_path))
    assert any(s["id"] == "backlog" and s["count"] == 1 for s in m["stages"])
    srv.server_close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest engine/tools/tests/test_pipeline_state.py::test_api_pipeline_route_returns_model -v`
Expected: FAIL until the import wiring exists (or PASS on the `ps.model` line but assert the route below by reading the file).

- [ ] **Step 3: Write minimal implementation**

In `dashboard_server.py`, add the import next to the others:

```python
import pipeline_state  # master Flow view: backlog+factory+OTel -> pipeline stage model
```

Add a class-level cache init in `Handler` is not needed; store on the server. In `make_server`, after `srv.token = ...`, add:

```python
    srv._pipeline_prev = {}   # per-process prev stage-by-id, for /api/pipeline flow diffs
```

In `_api_get`, add the route (near the `/api/otel/*` routes):

```python
        if route == "/api/pipeline":
            m = pipeline_state.model(str(env), self.server._pipeline_prev)
            self.server._pipeline_prev = m.get("stage_by_id", {})
            return self._send_json(m)
```

In `do_GET`, before the final `super().do_GET()` fallthrough, add the static app route:

```python
        if route == "/pipeline" or route.startswith("/pipeline/"):
            return self._serve_flow_app(route)
```

Add the handler method to `Handler`:

```python
    def _serve_flow_app(self, route):
        base = (UI_DIR.parent / "flow-app" / "dist").resolve()
        rel = route[len("/pipeline"):].lstrip("/") or "index.html"
        target = (base / rel).resolve()
        if base != target and base not in target.parents:      # no traversal outside dist/
            return self._deny(404, "not found")
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            target = base / "index.html"                       # SPA fallback
        if not target.is_file():
            return self._deny(404, "flow-app not built (run: cd engine/dashboard/flow-app && npm ci && npm run build)")
        ctype = {".js": "text/javascript", ".css": "text/css", ".html": "text/html; charset=utf-8",
                 ".svg": "image/svg+xml", ".json": "application/json"}.get(target.suffix, "application/octet-stream")
        body = target.read_bytes()
        self.send_response(200); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest engine/tools/tests/test_pipeline_state.py engine/tools/tests/test_a63_dashboard_api.py -v`
Expected: PASS (the pipeline test + the existing dashboard API tests still green — the new import/routes are additive).

- [ ] **Step 5: Commit**

```bash
cd Projects/aios
git add engine/dashboard/dashboard_server.py engine/tools/tests/test_pipeline_state.py
git commit -m "feat(dashboard): /api/pipeline model + serve /pipeline/* flow-app static"
```

---

### Task 5: Scaffold the `flow-app` (Vite + React + React Flow)

**Files:**
- Create: `engine/dashboard/flow-app/package.json`
- Create: `engine/dashboard/flow-app/vite.config.js`
- Create: `engine/dashboard/flow-app/index.html`
- Create: `engine/dashboard/flow-app/.gitignore`
- Create: `engine/dashboard/flow-app/src/main.jsx` (minimal, replaced in Task 6)

**Interfaces:**
- Produces: a buildable Vite app whose `npm run build` emits `flow-app/dist/` with `base: "/pipeline/"` so asset URLs resolve under the dashboard's `/pipeline/` mount.

- [ ] **Step 1: Create `package.json`**

```json
{
  "name": "aios-flow-app",
  "private": true,
  "type": "module",
  "scripts": { "build": "vite build", "dev": "vite" },
  "dependencies": { "react": "^18.3.1", "react-dom": "^18.3.1", "reactflow": "^11.11.4" },
  "devDependencies": { "@vitejs/plugin-react": "^4.3.1", "vite": "^5.4.0" }
}
```

- [ ] **Step 2: Create `vite.config.js`**

```javascript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base must match the dashboard mount so built asset URLs are /pipeline/assets/*
export default defineConfig({
  base: "/pipeline/",
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
});
```

- [ ] **Step 3: Create `.gitignore` and `index.html`**

`.gitignore`:
```
node_modules
```

`index.html`:
```html
<!doctype html>
<html lang="en">
  <head><meta charset="utf-8" /><title>AIOS Pipeline</title></head>
  <body><div id="root"></div><script type="module" src="/src/main.jsx"></script></body>
</html>
```

- [ ] **Step 4: Create a minimal `src/main.jsx` and verify the build works**

`src/main.jsx`:
```jsx
import React from "react";
import { createRoot } from "react-dom/client";
createRoot(document.getElementById("root")).render(<div style={{ color: "#e8e9ec" }}>pipeline: scaffold ok</div>);
```

Run:
```bash
cd Projects/aios/engine/dashboard/flow-app
npm install
npm run build
```
Expected: `dist/index.html` + `dist/assets/*` produced, no build error.

- [ ] **Step 5: Commit (scaffold only; `dist/` committed in Task 7 after the real app)**

```bash
cd Projects/aios
git add engine/dashboard/flow-app/package.json engine/dashboard/flow-app/package-lock.json \
        engine/dashboard/flow-app/vite.config.js engine/dashboard/flow-app/index.html \
        engine/dashboard/flow-app/.gitignore engine/dashboard/flow-app/src/main.jsx
git commit -m "chore(flow-app): scaffold Vite+React+React Flow (base=/pipeline/)"
```

---

### Task 6: The React Flow pipeline graph

**Files:**
- Create: `engine/dashboard/flow-app/src/usePipeline.js`
- Create: `engine/dashboard/flow-app/src/PipelineGraph.jsx`
- Create: `engine/dashboard/flow-app/src/style.css`
- Modify: `engine/dashboard/flow-app/src/main.jsx`

**Interfaces:**
- Consumes: `GET /api/pipeline` (Task 4) → `{stages, edges, flows}`.
- Produces: a full-viewport React Flow DAG — 8 stage nodes laid out left→right with count badges, edges animated when a `flows` transition targets them, in the B&W palette.

- [ ] **Step 1: Create `usePipeline.js`**

```javascript
import { useEffect, useState } from "react";

// Poll the dashboard's pipeline model. Same-origin (served under /pipeline/), so no CORS.
export function usePipeline(interval = 4000) {
  const [model, setModel] = useState(null);
  useEffect(() => {
    let alive = true;
    const load = () =>
      fetch("/api/pipeline").then((r) => r.json()).then((m) => { if (alive) setModel(m); }).catch(() => {});
    load();
    const t = setInterval(load, interval);
    return () => { alive = false; clearInterval(t); };
  }, [interval]);
  return model;
}
```

- [ ] **Step 2: Create `style.css` (B&W palette + node skin)**

```css
:root { --bg-0:#0e0f11; --bg-1:#16171a; --bg-2:#1c1d21; --text-0:#e8e9ec; --text-1:#9a9ca3;
  --text-2:#63656c; --accent:#6e79d6; --ok:#4cc38a; --border:rgba(255,255,255,.10);
  --mono:ui-monospace,"Cascadia Code",Consolas,monospace; }
html,body,#root { margin:0; height:100%; background:var(--bg-0); }
.react-flow__attribution { display:none; }
.react-flow__edge-path { stroke:rgba(255,255,255,.18); }
.react-flow__edge.animated .react-flow__edge-path { stroke:var(--text-0); }
.pnode { background:var(--bg-1); border:1px solid var(--border); border-left:2px solid var(--text-2);
  border-radius:8px; padding:9px 12px; min-width:118px; font-family:"Inter",system-ui,sans-serif; }
.pnode.hot { border-left-color:var(--accent); box-shadow:0 0 0 1px rgba(110,121,214,.14); }
.pnode .pnode-lbl { font:600 11px/1.2 var(--mono); letter-spacing:.04em; text-transform:uppercase; color:var(--text-0); }
.pnode .pnode-count { font:650 22px/1.1 var(--mono); color:var(--text-0); margin-top:3px; }
.pnode .pnode-count.zero { color:var(--text-2); }
.react-flow__handle { opacity:0; }
```

- [ ] **Step 3: Create `PipelineGraph.jsx`**

```jsx
import React, { useMemo } from "react";
import ReactFlow, { Background, Controls, Handle, Position } from "reactflow";
import "reactflow/dist/style.css";
import "./style.css";
import { usePipeline } from "./usePipeline";

const STAGE_X = 190, STAGE_Y = 90;

function StageNode({ data }) {
  return (
    <div className={"pnode" + (data.hot ? " hot" : "")}>
      <Handle type="target" position={Position.Left} />
      <div className="pnode-lbl">{data.label}</div>
      <div className={"pnode-count" + (data.count ? "" : " zero")}>{data.count}</div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
const nodeTypes = { stage: StageNode };

export default function PipelineGraph() {
  const model = usePipeline(4000);
  const { nodes, edges } = useMemo(() => {
    const stages = (model && model.stages) || [];
    const active = new Set(((model && model.flows) || []).map((f) => f.from + ">" + f.to));
    const hot = new Set(((model && model.flows) || []).flatMap((f) => [f.from, f.to]));
    const nodes = stages.map((s, i) => ({
      id: s.id, type: "stage", draggable: false, position: { x: i * STAGE_X, y: STAGE_Y },
      data: { label: s.label, count: s.count, hot: hot.has(s.id) },
    }));
    const edges = ((model && model.edges) || []).map((e) => ({
      id: e.from + ">" + e.to, source: e.from, target: e.to, animated: active.has(e.from + ">" + e.to),
    }));
    return { nodes, edges };
  }, [model]);

  return (
    <div style={{ width: "100vw", height: "100vh" }}>
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView
                 nodesConnectable={false} elementsSelectable={false} proOptions={{ hideAttribution: true }}>
        <Background color="#26282c" gap={22} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
```

- [ ] **Step 4: Wire `main.jsx` and build**

`src/main.jsx`:
```jsx
import React from "react";
import { createRoot } from "react-dom/client";
import PipelineGraph from "./PipelineGraph";
createRoot(document.getElementById("root")).render(<PipelineGraph />);
```

Run:
```bash
cd Projects/aios/engine/dashboard/flow-app && npm run build
```
Expected: build succeeds; `dist/` regenerated.

**Browser smoke** (the UI has no unit test — verify it renders real data): start the dashboard (`python engine/dashboard/dashboard_server.py --port 8642 --env-root <env>`), open `http://localhost:8642/pipeline/`, confirm 8 stage nodes render with non-crazy counts and no console error (read the page / console via the browser tools). If Jaeger/OTel is up, the `subagent`/`review` counts reflect live drains.

- [ ] **Step 5: Commit (source only; dist in Task 7)**

```bash
cd Projects/aios
git add engine/dashboard/flow-app/src/
git commit -m "feat(flow-app): React Flow pipeline DAG — 8 stages, count badges, animated flows"
```

---

### Task 7: Dashboard "Pipeline" nav + iframe + commit built `dist/`

**Files:**
- Create: `engine/dashboard/ui/views/pipeline.js`
- Modify: `engine/dashboard/ui/app.js` (add the nav item + import)
- Add (built): `engine/dashboard/flow-app/dist/**`

**Interfaces:**
- Consumes: `/pipeline/` served by the server (Task 4), the built app (Task 6).
- Produces: a "Pipeline" rail item whose view is a full-height iframe of `/pipeline/`. (The existing "Flow" OTel-runs view is KEPT — this ADDS the master pipeline rather than replacing it; a slight, intentional deviation from spec §7 so nothing is lost.)

- [ ] **Step 1: Create `ui/views/pipeline.js`**

```javascript
import { html } from "/lib.js";

// The master pipeline flow-graph is a scoped React/React Flow app served at /pipeline/.
// It is embedded via an iframe for clean isolation from the no-build Preact shell.
export function PipelineView() {
  return html`<section class="view" style="padding:0">
    <iframe src="/pipeline/" title="Pipeline"
            style="width:100%;height:calc(100vh - 20px);border:0;display:block;background:var(--bg-0)"></iframe>
  </section>`;
}
```

- [ ] **Step 2: Wire it into `app.js`**

Add the import beside the others:
```javascript
import { PipelineView } from "/views/pipeline.js";
```
Add a `pipeline` icon to `ICONS` (reuse the flow glyph shape):
```javascript
  pipeline: html`<svg class="ic" viewBox="0 0 16 16"><circle cx="3" cy="8" r="1.6"/><circle cx="8" cy="4" r="1.6"/><circle cx="8" cy="12" r="1.6"/><circle cx="13" cy="8" r="1.6"/><path d="M4.4 7.3l2.4-2.1M4.4 8.7l2.4 2.1M9.4 4.6l2.4 2.6M9.4 11.4l2.4-2.6"/></svg>`,
```
Add the NAV entry immediately after the `flow` entry:
```javascript
  { key: "pipeline", label: "Pipeline", view: PipelineView },
```

- [ ] **Step 3: Rebuild and verify end-to-end in the browser**

```bash
cd Projects/aios/engine/dashboard/flow-app && npm run build
```
Restart the dashboard server, open `http://localhost:8642/#/pipeline`, confirm: the rail shows "Pipeline", the iframe renders the 8-node React Flow graph with live counts, and the existing "Flow" tab still shows the OTel runs. Check the browser console for errors.

- [ ] **Step 4: Commit the built app (force-add dist since node_modules-adjacent output may be ignore-adjacent)**

```bash
cd Projects/aios
git add engine/dashboard/ui/views/pipeline.js engine/dashboard/ui/app.js
git add -f engine/dashboard/flow-app/dist
git commit -m "feat(dashboard): Pipeline nav + iframe host; commit built flow-app/dist"
```

- [ ] **Step 5: Final suite check**

Run: `python -m pytest engine/tools/tests/ -q` (from `Projects/aios`)
Expected: green — the Python additions are additive and the UI is out of the Python suite.

---

## Self-Review

**Spec coverage:**
- §2 React Flow + scoped Vite build + iframe + serve `/pipeline/*` → Tasks 4–7 ✓
- §3 aggregator + `/api/pipeline` + `flows` diff → Tasks 2, 3, 4 ✓
- §4 stage→artifact mapping (8 stages) → Task 1 `classify_stage` + Task 3 `gather` ✓
- §5 animated edges + count nodes + B&W palette → Task 6 ✓
- §2 build mechanics (commit `dist/`, ignore `node_modules`) → Task 5 `.gitignore`, Task 7 `git add -f dist` ✓
- §7 components (`pipeline_state.py` pure + gather; two server additions; flow-app; iframe host) → Tasks 1–7 ✓
- Intentional deviation from §7: the OTel per-run "Flow" view is KEPT and "Pipeline" is ADDED (not a replacement) — recorded in Task 7 so nothing is lost; the per-run view becomes a stage drill-in in a later slice.

**Placeholder scan:** every step carries real code/commands; no TBD/TODO/"handle edge cases". ✓

**Type consistency:** `classify_stage(item, ctx)`/`build_model(items, ctx, prev_stage_by_id)`/`gather(env_root)`/`model(env_root, prev)` names consistent across tasks; the model dict keys (`stages`/`edges`/`flows`/`stage_by_id`) match between Task 2 (producer), Task 4 (server cache of `stage_by_id`), and Task 6 (React consumer of `stages`/`edges`/`flows`). The React `/api/pipeline` shape matches `build_model`'s return. ✓
