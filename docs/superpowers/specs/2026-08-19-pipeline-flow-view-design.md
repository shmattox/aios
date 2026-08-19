# Pipeline Flow view — design

**Date:** 2026-08-19
**Status:** draft (brainstorm → spec)
**Repo:** aios (`engine/dashboard`)

---

## 1. Problem

The dashboard's Flow view shows one agent run's OTel trace. What's missing is the **master
pipeline visualization**: one live flowchart of the whole factory —
`backlog → brainstorm → spec → implement → subagent builds → review → gate → complete` —
with items animating through it in real time, plus git worktrees, PRs, and the vault-flow
loop. A hand-rolled SVG graph caps at "amateur" at that scale; an industry-standard node-flow
UI is a solved problem and should be adopted, not re-invented.

## 2. Decision — React Flow in a scoped Vite build

The animated, node-based flow graph is standard technology: **React Flow (xyflow)** — used by
Dagster, LangGraph Studio, Stripe, Typeform (see §Ecosystem-check). It brings animated edges,
custom nodes, minimap, pan/zoom, and live status as first-class features.

React Flow requires React + a build, and the dashboard is no-build Preact. Resolution: a
**scoped Vite build for just this one view**, isolated behind an iframe, so the rest of the
dashboard stays no-build.

- **`engine/dashboard/flow-app/`** — a self-contained Vite + React + React Flow app
  (`package.json`, `node_modules` git-ignored). `vite build` → `flow-app/dist/`.
- The dashboard server serves **`/pipeline/*`** from `flow-app/dist/` via its existing static
  handler.
- The dashboard's **"Flow" rail item renders `<iframe src="/pipeline/">`** — clean isolation;
  the React app never touches the Preact runtime, but lives at the same origin inside the
  dashboard.
- The React app fetches **`/api/pipeline`** from that same origin — no CORS, no separate server.

**Build mechanics:** `flow-app/dist/` is **committed** (the dashboard serves it static; there is
no runtime build); `flow-app/node_modules` is git-ignored. Revisit (a deploy build step) only if
the committed bundle bloats.

## 3. The pipeline-state aggregator

A new server module **`engine/dashboard/pipeline_state.py`** builds one graph model from the
real sources and the dashboard serves it at **`/api/pipeline`**:

```json
{ "stages": [{"id": "backlog", "label": "Backlog", "count": 12,
              "items": [{"id": "A1", "title": "...", "age_s": 900, "repo": "aios"}]}],
  "edges":  [{"from": "backlog", "to": "brainstorm"}],
  "flows":  [{"item_id": "A1", "from": "spec", "to": "implement", "at": 1787170000}] }
```

`flows` are transitions computed by diffing the current stage-assignment against the prior poll's
(held in a small server-side cache) — the animation is *items actually moving*, not decoration.
Polled every ~4s by the React app (SSE is a later optimization). The aggregator is best-effort:
a missing/idle source contributes zero, never errors.

## 4. Stages → real artifacts (Slice 1)

| Stage | Source of truth |
|---|---|
| Backlog | `## Open` items across every `BACKLOG.md` (the b2g `parse_items`) |
| Brainstorm | seeds (`◷`) + open items with no matching `docs/superpowers/specs/*` |
| Spec / write | item id appears in a `specs/*` filename/body but not a `plans/*` |
| Implement | item has a `plans/*`, or is `[FACTORY]`-stamped drainable |
| Subagent builds | live factory drains (`state/activity/factory-*.json`, live) + OTel subagent runs |
| Review | factory review-gate stage / OTel review runs |
| Gate | held items awaiting the human (brief `held` / `[GATE: human]`) |
| Complete | `## Done` / merged |

**Honest limit:** brainstorm and review are *inferred from artifacts*, not directly observed;
this is good enough to show real movement and sharpens as OTel-driven stage detection lands in a
later slice. Item→stage is a single deterministic classifier (`classify_stage(item, ctx)`), unit-
tested against fixtures.

## 5. Real-time animation

React Flow renders the fixed 8-node DAG. Each poll updates node counts and fires **animated
edges** for the `flows` transitions since the last poll; a node pulses when its count changes.
Custom node components are styled in the dashboard's **B&W `tokens` palette** (React Flow's
mechanics, our skin) — nothing amateur, nothing off-brand.

## 6. Scope (decomposed)

- **Slice 1 (this spec):** the animated pipeline spine on backlog + factory + OTel.
- **Slice 2:** git worktrees (`git worktree list`) + PRs (`gh pr list`) as node decorations.
- **Slice 3:** vault-flow overlays (vault writes from the secondbrain git log; `consult-vault`
  approvals from OTel) + click-a-node drill-in to the items/runs in that stage.

## 7. Components (isolation & interfaces)

- `pipeline_state.py` — pure `classify_stage(item, ctx)` + `build_model(sources)` (unit-tested) +
  a best-effort source-gather. Returns the JSON model. No I/O in the pure parts.
- `dashboard_server.py` — two additions: serve `/pipeline/*` static from `flow-app/dist/`; a
  `GET /api/pipeline` route calling `pipeline_state.build_model(...)`.
- `flow-app/` — React app: fetch `/api/pipeline`, render the React Flow DAG with custom nodes +
  animated edges, poll on an interval. One `PipelineGraph` component + a `usePipeline()` hook.
- The Preact `views/flow.js` becomes a thin iframe host (the per-run OTel trace view moves to a
  drill-in later, or a second tab).

## Ecosystem check

Per Shop-Before-Build. This session's graph-library research (2026-08-19) grounds the library leg.

### Leg 1 — Anthropic-first

```
Q: Does Claude Code / Anthropic ship a pipeline-flow-graph UI component to reuse?
Result: No. Claude Code emits native OTel telemetry (the data), but no node-flow UI
   widget. The graph MECHANICS are a general web-UI concern, not an Anthropic primitive.
```
Verdict: **build-because-none** for the view; the data upstream is native OTel (already adopted).

### Leg 2 — Marketplace (libraries)

```
Q: What is the industry-standard JS library for an animated node-based flow graph?
Result (this session's graph-lib sweep): React Flow (xyflow) is the de-facto standard —
   MIT, animated edges + custom nodes + minimap + pan/zoom, used by Dagster, LangGraph
   Studio, Stripe, Typeform (n8n uses its Vue port). It is a canvas, not a layout engine;
   pair with dagre/elk for auto-layout, but a fixed 8-node pipeline DAG needs no auto-layout.
   Alternatives (dagre + own SVG, Cytoscape) are lighter but read less polished for an
   animated pipeline — the "amateur" risk this view exists to avoid.
```
Verdict: **drop-in-skill** — adopt React Flow.

### Leg 3 — Our own skills / tools

```
$ ls engine/dashboard/{otel_runs.py,dashboard_server.py} engine/tools/backlog_parse.py
Result: We own the data sources — the backlog parser, the factory activity records, the
   OTel run projection (otel_runs.py, just shipped), the brief held/gate cache. The
   pipeline aggregator EXTENDS these; the dashboard server + static handler host the app.
   The existing Preact Flow view is reused as the iframe host.
```
Verdict: **adapt-skill** — extend our own dashboard + parsers.

### Leg 4 — Full-service platforms

```
Q: Adopt a hosted pipeline UI (Dagster / Temporal / n8n) wholesale?
Result: No. Those visualize THEIR own runtimes' pipelines, not our AIOS factory (backlog
   → drain → gate). We adopt the PATTERN (React Flow, which Dagster/LangGraph themselves
   use) and feed it our own model — not the service.
```
Verdict: **reference-only** — adopt the pattern, not the platform.

| Component | Source | Verdict |
|---|---|---|
| Node-flow graph library | React Flow (marketplace) | drop-in-skill |
| Pipeline aggregator + model | our data sources | build-because-none |
| Dashboard host + data sources | our own dashboard/parsers | adapt-skill |
| Hosted pipeline platform | Dagster/Temporal/n8n | reference-only |

## 8. Non-goals

- No auto-layout engine (dagre/elk) — the pipeline DAG is a fixed 8-node spine, laid out by hand
  in React Flow; auto-layout only earns its place if the graph becomes data-shaped (later slices).
- No second server — the app is served by, and reads from, the existing dashboard server.
- No runtime build — `dist/` is committed; the dashboard stays a static server.
- Slice 1 does not include worktrees, PRs, or vault overlays (slices 2–3).

## References
- Graph-library research: this session (2026-08-19) — React Flow as the standard.
- Data sources: `engine/tools/backlog_parse.py`, `engine/dashboard/otel_runs.py`,
  `state/activity/factory-*.json`, the brief held cache.
