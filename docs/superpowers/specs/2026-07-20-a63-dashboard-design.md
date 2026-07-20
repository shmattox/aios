# A63 — Dashboard over the state engine (design)

- **Date:** 2026-07-20 (brainstormed with Seth in the env simplicity-review session; design approved same day)
- **Item:** aios **A63** (state-consolidation #4) · instance wiring env-ops **H61** · flip program context: env `Memory/decisions.md` 2026-07-20 (H19 trigger pulled)
- **Status:** design-approved; plan next (superpowers writing-plans)

## Problem

The flip off Notion (H19) needs a UI that can **read the mirror and operate the system** before any silo cuts over — the parallel-run safety net and the eventual daily front door. Today the only operating surfaces are the chat brief and Notion itself; `state/domains` has zero readers; the decision surfaces (gate queue, factory veto window) live in chat text.

## Decisions (Seth, 2026-07-20)

1. **Delivery A→B:** local web app now; hosted later ("once proven"). Phase-2 phone access = `tailscale serve` (verified: reverse-proxies localhost over the private tailnet, zero code changes, no third-party content storage).
2. **Operate boundary B:** read everything + pipeline actions that already have gated engine CLIs behind them (gate approve/reject/hold, veto revert, walk decisions). The dashboard never writes state itself. Domain-record *editing* waits for A64.
3. **Panels v1 (5):** Cockpit (brief), Gate queue, Factory standup, Mirror browser (read-only), Token cost/usage.
4. **Approach 3:** zero-dependency build (no-build static UI + stdlib Python server) **with a named escape hatch** — if keyboard-first list virtualization or command-palette complexity hurts at real record counts, swap the UI layer to Vite + shadcn/ui + TanStack; server, endpoints, and data contract unchanged. (The plain-vs-React leg was the one question adversarial research could not settle — recommended on scale reasoning, so the reversal path is named instead of assumed.)
5. **No design system first.** Linear.app style is encoded as a ~30-variable token sheet + a design-principles section in this spec; a shared design system is extracted only if/when a second AIOS surface needs it.
6. **Dark-only v1** (Linear default); light theme deferred.
7. **Veto-revert included in v1** behind a confirm dialog (`git revert` — itself revertible).

## Architecture

```
engine/dashboard/
  dashboard_server.py      # stdlib-only: http.server subclass
  ui/
    index.html             # shell + token injection
    tokens.css             # the Linear sheet (design tokens)
    app.js + panels/*.js   # ES modules, no build step
```

- **Server** — `SimpleHTTPRequestHandler` serves `ui/`; `GET /api/<surface>` reads state fresh from disk per request; `POST /api/action/<id>` runs an **allowlisted** engine CLI via list-form `subprocess.run` (never `shell=True`). Empirically verified shape on this machine (Python 3.14.2, deep-research leg 6).
- **Security (mandatory, not optional)** — binds `127.0.0.1` only; exact Host-header validation against `localhost`/`127.0.0.1`; per-start random auth token required on every POST. Defense against DNS rebinding, which reaches localhost servers from any hostile page with no misconfiguration required (GitHub Security Lab; Chrome 142+ LNA helps, Firefox/Safari do not). Token is injected into the served page — same-origin JS has it, a rebound page cannot read it.
- **Lifecycle** — launched on demand: `aios:dashboard` skill starts the server (single-instance lock), prints + opens the URL. At-logon registration, tailscale, and the laptop story are env legs (H61). Windows process-management shape (at-logon task vs on-demand only) is decided in H61, not here.
- **Refresh** — UI polls `GET /api/mtimes` (file mtimes only, cheap) every few seconds and re-fetches changed payloads. Every panel shows its data age; stale is a visible badge, never silent.
- **Paths** — resolve `env_root` the same way engine tools do; route through A103's `state_paths` seam once it lands (soft dependency: flat paths until then, one edit after).

## Panels & data contract

| Panel | Reads (all existing surfaces — no new collection) | Actions (existing gated CLIs only) |
|---|---|---|
| Cockpit | `brief-cache.json`: act rows + system/claude voices, urgency, going_quiet, health_lines + delta via health_fingerprints, headline_bubbles | open thread; record walk decision (brief_session CLI) |
| Gate queue | held lanes from brief-cache + queue (read via `queue_tx`-safe path); draft files rendered as markdown preview | approve / reject / hold → `ship.py`/gate CLI; Paper-Governs holds stay held — UI renders the hold reason |
| Factory standup | `state/factory/standup.json` four groups (veto / needs-you / handed-off / stuck) | veto = confirm-dialog `git revert`; mark-reviewed |
| Mirror browser | `state/domains/<silo>/schema.yaml` + `tables/` — silo → table → dense record list → record detail (frontmatter fields + body) | **read-only until A64** |
| Cost | `state/factory/spend-*.json`, `state/task-logs/*/spend-*.json`, `gate-metrics.json` | none. Leads with `total_cost_usd` (H62: `output_tokens` may under-count multi-turn runs) |

**Action allowlist contract:** a static table `action_id → argv template` in `dashboard_server.py`. Anything not in the table is a 403. Each action maps 1:1 to an existing CLI so every write keeps its revert pointer, atomicity, and Paper-Governs behavior — the server adds zero write logic. Failed actions return the CLI's stderr verbatim; UI shows it in a toast and re-fetches nothing (CLIs are atomic).

## Design tokens (Linear-style)

`tokens.css`: dark-first bg ramp (#0e0f11 base, elevated layers), muted gray text ramp, one indigo accent, `1px rgba(255,255,255,.08)` borders, 6px radii, Inter/system stack at 13–14px dense, 120ms ease motion, visible focus rings. Keyboard-first: `j/k` row navigation, `enter` open, `g` then key for panel jumps, filter box (a full command palette is an escape-hatch-tier feature, not v1).

## Testing

Rides `python -m pytest -q`: handler tests against disk fixtures (each GET endpoint's payload shape as a contract test), allowlist rejection of unlisted actions, Host-validation + token-enforcement tests, action dispatch test with a stub CLI, mtimes-endpoint correctness. UI smoke via the browser pane during build. Fresh-context review before ship (production-state action surface → review-gate tier).

## Boundaries

- **Engine (this repo):** server, UI, allowlist, tokens, `aios:dashboard` skill. Universal — any install gets it.
- **Env (H61):** registration/lifecycle choice, tailscale phase 2, laptop story (depends on H57 sync-channel).
- **Not this spec:** domain-record editing (A64), mirror freshness (A78's pipe), state↔wiki drift (A104), Notion demotion itself (H19 — this is its prerequisite).

## Ecosystem-check

**Leg 1 — Anthropic-first.** Session skill-roster enumeration (2026-07-20, live session listing):

```
web-artifacts-builder — claude.ai artifacts only (CSP: no local files, no localhost fetch) → NOT usable as delivery
dataviz — chart/design standards, light+dark → ADOPT at build time for the cost panel + any charts
artifact-design / brand-guidelines — artifact/brand surfaces, not a local app → n/a
No native Claude Code dashboard/serving capability exists; preview browser pane → ADOPT for dev-time smoke tests
```

**Leg 2 — public marketplace.**

```
$ npx skills find dashboard
firecrawl/firecrawl-workflows@firecrawl-dashboard-reporting  29.3K
wshobson/agents@kpi-dashboard-design                         11.7K
wshobson/agents@grafana-dashboards                           10K
anthropics/knowledge-work-plugins@build-dashboard            6.4K
affaan-m/everything-claude-code@dashboard-builder            3.9K
grafana/skills@dashboarding                                  2.7K

$ npx skills find "local web app"
(no relevant hits — app-store release-flow skills only)
```

All hits are build-*helper* skills, not adoptable products. `anthropics/knowledge-work-plugins@build-dashboard` → harvest as a reference during implementation.

**Leg 3 — our own skills/tools.** From the 2026-07-20 five-audit env review (real reads, `<env>/docs/superpowers/findings/2026-07-20-env-simplicity-review.md`):

```
brief-cache.json / standup.json / spend ledgers / gate-metrics.json → ADOPT as the data layer (already structured payloads)
queue_tx.py / ship.py / rewind.py / brief_session.py → ADOPT as the action layer (gated, atomic, revertible)
state_validate.py + schema.yaml → ADOPT as the mirror browser's table contract
brief_render.py → stays chat-markdown; NOT reused for HTML (deterministic-render rule: each surface renders from data)
A67 (in-Obsidian Dataview dashboards) → REJECTED direction for the cockpit (see Leg 4 Bases verdict); remains a separate seed
```

**Leg 4 — full-service platforms (deep-research, adversarially verified).** Run `wf_cb8dc11a-a3b`, 2026-07-20: 104 agents, 3-vote adversarial verification per claim, refuted claims excluded.

```
Obsidian Bases  — FAIL: data model is vault-note properties only (official docs; open FRs #103622/#104834 confirm
                  no external JSON/YAML source); no native action buttons; mobile shell-out architecturally
                  impossible (no Node runtime; Shell Commands plugin flagged desktop-only by its author). 3-0 ×3.
Glance-class    — FAIL: verified read-only feed renderer; custom-api surface is fetch-and-render, zero write path.
                  3-0 ×2. (Grafana/Homepage not directly verified — open question; read-only pattern likely generalizes.)
Low-code        — FAIL (Budibase/Appsmith/ToolJet): no local-file data source in any (vendor docs); Budibase file
                  path is one-time import into CouchDB, not a live disk read. Same custom API glue + platform weight. 3-0.
Notion          — FAIL, and the parked H19 premise is now VERIFIED: ~3 req/s per-integration API cap (429 beyond),
                  buttons/forms/embeds dead offline, offline databases auto-sync only 50 rows (Notion's own docs). 3-0 ×3.
stdlib server   — VERIFIED empirically on this machine (Py 3.14.2): SimpleHTTPRequestHandler + do_POST → subprocess
                  → JSON works with zero dependencies. Production caveat noted → phase-2 fronts it with tailscale
                  serve or swaps to FastAPI/uvicorn if the endpoint surface grows.
DNS rebinding   — the one mandatory hardening: exact Host validation + token auth (GitHub Security Lab; corroborated
                  Unit 42 / NCC Group / MCP spec). 3-0 ×2.
```

| Option | Verdict | Why |
|---|---|---|
| Obsidian Bases | ✗ | external data, actions, mobile all fail |
| Glance-class self-hosted | ✗ | read-only by architecture |
| Low-code self-hosted | ✗ | no local-file data source |
| Notion as host | ✗ | rate cap + offline buttons + 50-row offline (premise verified) |
| React kit now | ✗ (deferred) | unresolved leg; named escape hatch instead |
| **Custom: static UI + stdlib server** | **✓ BUILD** | only shape meeting all six hard requirements; thin differentiator over adopted data + action layers |

**Verdict: build-because-none** — no drop-in or full-service option satisfies the hard requirements; the adopted pieces are our own (data layer + gated CLIs), `dataviz` rides as a drop-in-skill at build time, and `anthropics/knowledge-work-plugins@build-dashboard` is reference-only.

**Time-sensitivity caveats (from the research):** Notion says it plans to address the 50-row offline limit; Bases has an open external-data FR — both verdicts could soften within a year. Neither changes the local-first requirement (Paper-Governs data stays off third-party clouds).

## Open questions (carried, not blocking)

1. Escape-hatch threshold: at what record count / interaction complexity does no-build break down? (Named trigger, watched during use.)
2. H61's process-management shape on Windows (at-logon task vs on-demand only) and its interaction with session locks.
3. Phase 2: stdlib behind tailscale indefinitely, or FastAPI swap when hosted?

## Acceptance (for the A63 backlog item)

`aios:dashboard` starts the server and the five panels render real data from this install (shown); a gate approve action ships an item with its normal revert pointer + receipt and a Paper-Governs item refuses with the hold reason rendered (both shown); an unlisted action returns 403 and a request with a wrong Host or missing token is rejected (tests shown); mirror browser renders every silo table that `state_validate` passes, read-only; full suite green; fresh-context review (review-gate tier) zero CRITICAL.
