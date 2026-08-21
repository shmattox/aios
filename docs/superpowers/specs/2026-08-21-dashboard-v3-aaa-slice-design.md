# Dashboard v3 — the AAA slice (design)

Date: 2026-08-21
Status: approved direction (Seth's six rulings, this session) → build via interactive SDD waves (render-touching — Rule-Zero terminal human look; NOT factory-drained)
Origin: the Fable dashboard-V2 review (`Documents/Claude/docs/superpowers/findings/2026-08-21-dashboard-v2-fable-review.md`) + Seth's same-day decision queue answers.
Backlog: **A130** (this spec's build item) + A127/A128/A129 (P0 siblings), env-ops H140 (metrics sink) / H142 (vault-currency check).

## 1. Retro-record — the 2026-08-20 IA rebuild is APPROVED and this spec governs it

The 25-commit 2026-08-20 rebuild (`d11b271`..`bfbab2b`) shipped with no spec; Seth retro-approved it 2026-08-21 (`Memory/decisions.md`). What it is, now on the record:

- **Shell:** hash-routed Preact+HTM no-build app; rail nav **Overview / Board / Files / Usage** + a *Pipelines* section (**AIOS / Software** live; **Marketing / Ops** `soon` stubs); 860px mobile drawer; `g`-chord nav; live age badge from `/api/mtimes`.
- **Flow renderer:** native DOM `ui/views/pipeflow.js` (domain-agnostic node/edge strip) — **replaced the React Flow iframe app entirely** (`a02e475`). The 2026-08-19 pipeline-flow spec's React Flow adoption is thereby superseded; the cockpit spec's "no build step" non-goal stands again. Seed **A117** (d3-dag flow) is superseded by `pipeflow.js` — reconcile/close at the A126 brainstorm.
- **Aesthetic:** monochrome thin-line, accent = white light, Linear.app register — **approved** (supersedes A63 Decision 6 indigo).
- **Retired without record, now recorded:** Inbox and Mirror views (their verb set lives on in Board cards + the gate verbs), the OTel per-run Flow tab, the standup/cockpit panels.

Still binding from prior specs: server never writes state except via the gated-CLI action allowlist (A63 D2); 127.0.0.1 + Host validation + per-start write token; stale-is-a-visible-badge; fail-open advisory surfaces; adopt-the-pattern-never-the-platform; one-machine-renders-others-view (H57).

## 2. Rulings this design implements (Seth, 2026-08-21)

1. **The dashboard is the front door** — canonical operating + approval surface; the chat brief stays as narrative/mobile voice. (Vault: `[[entities/aios]]` updated.)
2. **Files goes READ-ONLY + "Open in Cursor".** The 08-20 editor violates the A63 write boundary; retire it.
3. **Desktop canonical; cloud is planned separately** (env-ops H143). This spec adds nothing cloud-shaped beyond the already-ruled `tailscale serve` viewing path.
4. **Cursor: run-both, no migration** — the Files deep-link is the integration point.
5. **"Operating UI" acceptance:** Seth runs a full week of gate decisions + factory triage here without falling back to the chat brief. That week is the A109 publish-unblock gate.

## 3. Design — six additions, one subtraction

### 3.1 Health panel (Overview strip + drill page)
The single largest gap: 6/18 standing checks are red today, invisible. Render `state/standing-checks/results.json` (A94 runner — already nightly): red/watch counts as an Overview metric tile; drill page lists each check (id, status, first_red, on_violation, origin), plus the scheduled-task fleet (from `state/task-logs/*/last-run.log` mtimes + the existing missed-run detector), plus per-source staleness badges (`gate-metrics` 24d-stale-class anomalies get a visible age chip, per stale-is-a-badge). New route `/api/health` — read-only aggregation, no new write logic. Advisory surface: fails open (an unreadable results.json renders as its own red, never as silence).

### 3.2 Gate panel ("lead with the Gate")
Render `state/factory/gate-metrics.json` (freshness = A128): decisions n, accepted/rejected/reverted, decider split (human/auto/scheduled), **agreement vs override rate**, override ids linking into Board cards. This is the product's differentiator rendered as its own surface — the human-factor metric (escalation/intervention rate) the 2026 observability stack treats as table stakes. Route: promote gate-metrics out of the `/api/spend` payload into `/api/gate-metrics`.

### 3.3 Sessions (history + replay)
Completed-run browser: merge `state/activity/*.json` records (all surfaces, not just `live`) with OTel run summaries (existing `otel_runs.fetch_runs`, lookback widened on demand) → table (when, surface, repo/items, model, tokens, est. cost, duration, errors) with the existing transcript-tail modal as the replay view. Search by item id / repo / surface. Data exists today; no new collectors.

### 3.4 Flow drill-in (the "neural graph" completion)
`pipeflow.js` nodes gain click-through to the span waterfall: reconnect the orphaned `/api/otel/run/<traceid>` + `build_graph` projection as a per-run span tree rendered in the inspector pane (the LangGraph-Studio convention: live node status → drill into the trace). Edge animation stays movement-only (`flows` diff) — already our doctrine. A126 (OTel-fed subagent/review stage detection) remains the follow-on that lights stages from OTel; this slice only renders what a node already knows.

### 3.5 Inventory (skills / plugins / MCP)
Read-only panel: plugin manifest + `skills/*/SKILL.md` frontmatter (name, description) joined with last-invocation timestamps (from session activity records where available; "unknown" is an honest value), + MCP servers configured for the install (names/status only — no secrets, no keys; key *values* never render, per `files_state` refusal doctrine). Closes the July audit's blind spot (0/24 scheduled runs invoking skills — invisible until a monthly report).

### 3.6 Usage upgrades
Latency tile (`duration_ms` p50/p95 from OTel summaries) + error-classification row (taxonomy from the repaired usage-audit extract when present; degrade to counts). Cost honesty is A129. When env-ops H140 lands a metrics sink, Usage reads it as the authoritative token/cost feed; until then the two-ledger split stays visibly labeled.

### 3.7 Files → read-only + "Open in Cursor" (the subtraction)
Remove `/api/files/write` + `/api/files/create` and the editor affordances; keep tree/read/search + syntax highlighting. Add per-file **"Open in Cursor"** (`cursor://file/<abs-path>[:line]` — verify the exact scheme against the installed Cursor at build; fall back to copy-path if the deep link no-ops). The dashboard returns to zero write surface outside the 7-action gate allowlist.

### Cross-cutting
- **SSE consolidation:** the views poll (4–5s) while `/api/events` SSE exists — move AIOS/Software/Overview onto the SSE `useLive` path; polling stays only where mtime fingerprints can't signal (OTel).
- **Marketing/Ops factories:** explicitly out of scope; when their stage models exist they instantiate `pipeflow.js` + a `*_state.py` aggregator each — the shell already anticipates them. No shell work now.

## 4. Non-goals
No build step / no React Flow (re-affirmed). No new palette (monochrome approved). No editor. No auth beyond the existing token model (localhost + tailscale-view only). No cloud legs (H143 owns that). No new write logic in the server. No Marketing/Ops stage models. No eval/LLM-judge scoring layer (the gate's fresh-context review IS our eval; rendering it is 3.2 — building a second eval system is not).

## Ecosystem-check (§5)

**Leg 1 — Anthropic-first.**
```
Grep OTEL ~/.claude/settings.json → CLAUDE_CODE_ENABLE_TELEMETRY=1, OTEL_TRACES_EXPORTER=otlp,
OTEL_METRICS_EXPORTER=none (traces→Jaeger live today; metrics stream exists natively, currently off → H140)
```
Claude Code natively exports metrics+logs via OTLP (Grafana integration doc; grafana.com/docs/grafana-cloud/...integration-claude-code/); `/workflows` provides the in-session live orchestration view; Remote Control + `tailscale serve` cover remote viewing. **Adopt:** native OTel export as the data plane; nothing Anthropic-hosted replaces a local read-only cockpit over `state/` (Paper-Governs data stays on-machine — standing A109 ruling).

**Leg 2 — marketplace.**
```
SearchSkills ["dashboard","observability","agent monitoring","telemetry","usage metrics"] → {"results":[]}
```
Zero installable skills. OSS adjacent (2026-08-21 research, findings doc §2/§3): ccusage (JSONL cost CLI), claude-code-otel / Grafana dashboard #25255 (metrics stack), Stargx/claude-code-dashboard + claude-fleet (session fleet monitors). **Adopt patterns** (JSONL/OTel-fed panels, fleet table); **adopt none as platform** — none reads our `state/` contracts (queue, standing-checks, gate metrics, backlogs), which is the product.

**Leg 3 — our own skills/tools.**
```
Get-ChildItem engine/dashboard → 8 aggregator .py + 16 ui .js (5 = dead panels/, A127 deletes);
orphaned-but-built: otel_runs.build_graph + /api/otel/run (3.4 reconnects), git_state + /api/git
(worktrees/PRs — claimed for Sessions/Board enrichment), standing_checks.py results.json (3.1 renders),
gate_metrics.py (3.2 renders, A128 refreshes)
```
**The strongest leg:** four of the six additions render data the engine already computes. Custom-build is limited to thin read-only aggregation + views.

**Leg 4 — full-service platforms.**
```
WebSearch sweep (research agent, this session — full citations in the findings doc §1-§3):
"LLM observability platforms 2026 Langfuse LangSmith Braintrust Arize" → marktechpost.com 2026-08-09
15-platform review; langfuse.com (MIT, self-hostable, OTel-ingesting); Arize→Dynatrace acquisition
Aug 2026; "Claude Code OTel Grafana dashboard" → grafana.com integration-claude-code + dashboard
#25255; "claude code fleet dashboard github" → Stargx/claude-code-dashboard, tianyilt/claude-fleet
```
All solve LLM-trace observability; none render the gate ledger, standing checks, backlogs, or the queue — adopting one adds a second dashboard beside the state it can't see, violating adopt-the-pattern-never-the-platform (A109 precedent: Linear/Plane/Agent-Inbox). **Verdict table:**

| Component | Buy/Adopt | Build | Verdict |
|---|---|---|---|
| Telemetry data plane | ✅ native Claude Code OTel (+H140 sink) | — | partial-service |
| Span waterfall UI | pattern from Jaeger/LangGraph-Studio | thin Preact view over existing `build_graph` | reference-only |
| Health/Gate/Inventory/Sessions | — (no product reads our state) | thin read-only aggregations (data exists) | build-because-none |
| Cursor integration | `cursor://` deep-link scheme | one button | reference-only |
| Eval layer | — | ❌ not built (gate review is the eval) | reference-only |

## 6. Slices + acceptance

- **Slice 0 (pre-req, factory):** A127 prune + A128 gate-metrics freshness + A125. A129 rides Slice 2.
- **Slice 1 — Health + Gate panels + `/api/health`,`/api/gate-metrics`.** Acceptance: real reds render (6 today) with drill detail; gate agreement/override renders from fresh data; per-panel age chips; suite green; fresh-context review zero CRITICAL; **Seth's look**.
- **Slice 2 — Sessions + Usage upgrades + cost honesty (A129).** Acceptance: a completed factory drain and an interactive session both findable + replayable; latency tile live; est. markers; review + look.
- **Slice 3 — Flow drill-in + SSE consolidation.** Acceptance: clicking a stage node with a live run opens its span tree from a real trace; polling removed where SSE covers; review + look.
- **Slice 4 — Files read-only + Open-in-Cursor + Inventory panel.** Acceptance: write endpoints gone (`grep` shown), deep link opens the file in Cursor on this machine (demonstrated), Inventory lists all 12 skills + MCP names with zero secret material; review + look.
- **Terminal gate:** the "operating UI" week (ruling 5) — tracked as the A109 publish-unblock, not a slice.
