# A109 — Dashboard v2: the operating UI (design)

- **Date:** 2026-07-22 (brainstormed with Seth across one live-mockup session; design approved same day)
- **Item:** aios **A109** (Dashboard v2 publish-readiness pass; the A63 task-8 publish leg is blocked on this)
- **Extends:** `2026-07-20-a63-dashboard-design.md` (v1 — server, security, action allowlist, 5 panels). V1 architecture stands; this spec reshapes the UI and adds two gate verbs.
- **Visual contract:** `2026-07-22-a109-dashboard-v2-mockup.html` (same dir) — an interactive, dependency-free mockup iterated 11 rounds with Seth; renders standalone in any browser. Where prose and mockup disagree, the mockup wins for look/feel, this spec wins for data/engine semantics.
- **Research:** deep-research run `wf_161c764d-670` (2026-07-22, 105 agents, 24 claims confirmed 3-0, 1 refuted) + live Linear/Plane verification (this session). See §Ecosystem-check.

## Problem

V1 (A63) shipped the working skeleton — five read panels + allowlisted gated actions. Seth's verdict 2026-07-21: "just a basic view." Before the H19 flip or any public plugin publish, the dashboard must become the *operating* surface: decide from anywhere (desktop + phone), see every flow (pipeline, factory, every project), and drill from any card to the governing document in one tap.

## Design principles (Seth-approved, 2026-07-22)

1. **The Inbox is the verb surface; everything else is a view.** Verified (3-0, LangChain HITL docs + Agent Inbox): human-in-the-loop agent control is an inbox of decision cards, not a kanban. The Board visualizes flow; decisions happen on cards.
2. **One card anatomy, one owner at a time.** A single detail component with three homes — desktop right pane, mobile inline accordion, desktop shadow-box modal — that physically moves between homes. Mode changes never move it while it is being read (the modal owns it until closed).
3. **Station-scoped verbs.** The action strip is a function of the card's station, and each action maps 1:1 to an engine capability (see §Verb matrix). The surface never offers what the engine won't do and never omits what it will.
4. **Three-layer drill-down.** Every document reference is a link tagged with its canonical home: `state` (mirror browser, in-app) · `drive` (executed paper, Google Drive) · `kb` (SecondBrain page, `obsidian://`). This is the env's canonical-roles table rendered as hyperlinks.
5. **A lane = a backlog, discovered not curated.** Board rows derive from the factory's discovery set (every `Projects/*` repo with a `BACKLOG.md` + env-ops) plus the operational silos from `domains.yaml`. A repo graduating to its own backlog gets its own lane automatically. Status badges (active / factory-paused / paused·guarded) come from factory config; archived projects sit behind a one-line "dormant" toggle — acknowledged, never silently absent. Paused lanes still surface their open decisions.
6. **Two coherent modes, one breakpoint (860px).** Desktop: collapsible left rail, two-pane inbox, swimlane grid, shadow-box modal. Mobile: right-side hamburger drawer (thumb ergonomics), full-width single column, vertical silo accordions with station labels repeated per silo and empty stations hidden, inline accordion expansion everywhere (touch never superimposes). No horizontal scrollbar at any width ≥320px, ever.

## Surfaces

| Surface | v2 phase | Content |
|---|---|---|
| **Inbox** (home) | v2a | Unified needs-you queue across all silos + a slim cockpit strip on top (headline sentence, delta-gated health lines, going-quiet note — the brief's essentials folded in; no separate Cockpit panel). Rows: silo tick, title, kind, age. Detail card per §Card anatomy. |
| **Board** | v2a | Swimlanes: columns = station (Incoming → Needs you → In motion → Review → Shipped 24h), rows = FamilyOffice / Personal / General Mgmt + the Dev group's per-repo lanes (§principle 5). `T` collapse, filter box + built-in "Needs me" saved view. Cards open per §principle 2. Drag only where the move maps to a gated CLI op (grab affordance); everything else inert. |
| **Flow** | v2b | Live pipeline DAG (capture→sort→ingest→gate→ship + factory + garden loops), d3-dag layout + hand-rolled SVG, counts on nodes/edges, SSE-animated, click-through to stage item lists. |
| **Mirror** | v1 ✓ | Read-only silo table browser; v2 adds saved views/filters. Write path waits for A64. |
| **Stats** | v2b | Spend (`total_cost_usd`-led), gate acceptance + honest revert metrics, throughput sparklines; `dataviz` skill governs charts. |

## Card anatomy (one component)

Chips (id · silo · kind · ⚖ Paper-Governs · staged date) → title → why (with inline source-tagged doclinks) → proposed-change diff (target path linked `kb`; `papered_source` linked `drive`) → paper-evidence packet when present (verdict + quote + linked executed PDF) → **Drill down** block (one `state` + `drive` + `kb` row each) → station verb strip → respond box (placeholder text varies by verb mode).

## Verb matrix (station → actions → engine backing)

| Station | Actions | Engine backing |
|---|---|---|
| Incoming | Triage now · Dismiss | run sort/ingest for the item now; route to `reference` stage (A89 lane) |
| Needs you | Approve · Reject · **Edit** · **Respond** | `ship.py ship` / `ship.py reject` / **edit = amend-draft-then-ship (new gate leg, §Engine work)** / respond = durable reply op |
| In motion | Append instruction | reply op tagged `append`; consumed at the agent's next checkpoint |
| Review | Open draft · Comment to reviewer | drill-down to staging draft; reply op tagged `comment`, attached to the review run |
| Shipped | View receipt · Revert | receipt render from ship history; `rewind` git-revert behind a confirm dialog |

**Reply plumbing is ONE op shape:** `respond | append | comment` are the same durable queue/session record with a `reply_kind` discriminator and target id; consumers differ (next gather / running drain / review run). No third write surface — it extends the existing walk-decision mechanism.

## Engine work (the non-UI legs)

1. **Edit verb** — the one ship-path change: apply an operator-amended draft through the gate with normal atomicity, revert pointer, and Paper-Governs behavior. Touches `ship.py` semantics → **review-gate tier, human pass required** (not autonomously drainable).
2. **Reply op** — the `reply_kind` record + consumer reads (gather + drain checkpoint + review run). Relates to A69's gate-apply brainstorm; keep scopes separate.
3. **Board data endpoint** — `GET /api/board`: reuse the b2g backlog parser + `standup.json` per-repo grouping + brief-cache held lanes; discovery = the factory-gate sweep set. No new collection.
4. **SSE endpoint** — `GET /api/events` on the existing `ThreadingHTTPServer` (verified viable: thread-per-connection, HTTP/1.0 default, no Content-Length needed). Server-side mtime watcher pushes change events; UI drops polling. Graduation trigger to uvicorn (connection-count degradation) becomes a **standing-check**, not a memory.

## Architecture & stack (research-verified)

- **Stay zero-toolchain:** vendored **Preact + HTM** as local ESM (officially documented no-build path) for componentization; no node anywhere in the repo or install. React Flow explicitly avoided (unverified preact/compat joint); escape hatch unchanged from A63.
- **Flow graph:** **d3-dag** (~42KB gz, layout-only) + own SVG renderer.
- **Tokens:** extend v1's `tokens.css` (dark-only stands, A63 §6); seed additions from Radix dark scales. New brand mark: the three-ring concentric SVG (inline, `stroke: currentColor`, stroke-width 62 at small sizes). Viewport meta required (v1 gap found during mockup — phones rendered at desktop virtual width).
- **Security unchanged:** 127.0.0.1 bind, exact Host validation, per-start token, allowlisted argv-built CLI actions, zero write logic in the server.
- **Mobile (v2c):** `tailscale serve` fronts the unchanged loopback server (auto Let's Encrypt → real secure context → PWA installable); identity via Serve's spoofing-protected headers; **ntfy** pings that are **content-free** (counts only — no item content ever rides a push channel) with click-through deep links; delta-gated (new needs-you only, never re-nagged). Test PWA web push over tailnet before accepting the ntfy.sh iOS APNS hop.

## Phasing

- **v2a** — Inbox (+cockpit strip) · per-repo Board · station verbs · SSE · drill-down links · reply op. Factory-drainable **except** the Edit-verb gate leg (review-gate tier).
- **v2b** — Flow view · Stats redesign · ⌘K palette · board saved views.
- **v2c** — tailscale + PWA + ntfy (env legs ride H61). Public plugin publish (the held A63 task-8 reinstall) unblocks **after v2a ships and Seth calls it the operating UI**.

## Ecosystem-check

**Leg 1 — Anthropic-first** (session skill roster, 2026-07-22):

```
dataviz — chart standards → ADOPT for Stats/Flow at build time
artifact-design — used to produce the mockup itself; not a delivery vehicle for the app
No native Claude Code dashboard/serving capability; browser pane → dev-time smoke only
```

**Leg 2 — public marketplace** (carried from A63's executed check, 2026-07-20 — re-verdict unchanged):

```
$ npx skills find dashboard   (2026-07-20, A63 spec §Ecosystem-check)
firecrawl-dashboard-reporting / kpi-dashboard-design / grafana-dashboards /
knowledge-work-plugins@build-dashboard / dashboard-builder / grafana skills
→ all build-HELPER skills, none an adoptable product; build-dashboard harvested as reference
```

**Leg 3 — our own skills/tools** (real reads, this session):

```
engine/dashboard/* (A63 v1: server 351 lines, ui 3 files, suite 614 green) → EXTEND, not rebuild
b2g backlog parser + state/factory/standup.json per-repo groups → ADOPT as Board data layer
brief-cache.json / queue.json / spend ledgers / gate-metrics.json → ADOPT (unchanged from A63)
ship.py / rewind.py / brief_session.py record_decision → ADOPT as action layer; edit verb = the one extension
```

**Leg 4 — full-service platforms** (executed this session):

```
Linear  — WebSearch 2026-07-22: cloud-only, no self-host → Paper-Governs data leaves the machine; no
          local-CLI action path; 5,000 req/hr API. FAIL (same class as the verified Notion 3-0 failure).
          Swimlanes interaction model ADOPTED as design inspiration (changelog fetched + encoded in mockup).
Plane   — GitHub README + developers.plane.so + selfhosting.sh fetched: AGPL, self-hostable, but 13
          services / 4-8GB RAM for one operator; REST + event webhooks only — no local-file source, no
          command buttons → second SSOT + sync pipe + middleware, and still none of the operating panels. FAIL.
deep-research wf_161c764d-670 (105 agents, 3-vote adversarial): inbox-over-kanban 3-0; SSE-on-stdlib 3-0;
          Preact+HTM no-build 3-0; d3-dag 3-0; tailscale serve→PWA chain 3-0 (5 claims); ntfy 3-0 (4 claims);
          design-benchmark leg produced zero surviving claims → tokens built, not researched.
```

| Option | Verdict | Why |
|---|---|---|
| Linear (SaaS) | ✗ | cloud-only; no local action path; steal the swimlane design only |
| Plane (self-host) | ✗ | no local-file source or command buttons → second SSOT + middleware; 13-service footprint |
| LangChain Agent Inbox | ✗ (pattern adopted) | LangGraph-coupled Next.js app; the four-verb card **pattern** is adopted, not the product |
| React kit now | ✗ (deferred) | unverified preact/compat joint; escape hatch stays named |
| **Extend v1 custom** | **✓ BUILD** | differentiator is ours (gated actions over local state); v1 foundation + suite already exist |

## Open questions (carried)

1. Re-verify the four-verb taxonomy against LangChain's current docs at build time (the API churned once already — one refuted claim).
2. PWA web push over a tailnet origin on iOS end-to-end — unverified; decides ntfy-hop acceptance.
3. Cockpit-strip content: exactly which brief fields fold in without recreating the full chat brief (settle in the plan).

## Acceptance (for the A109 backlog item)

Inbox renders the real unified needs-you queue with the cockpit strip, and each of the five station verb sets fires its real CLI (approve ships with revert pointer + receipt; a Paper-Governs hold renders its hold reason; edit amends-then-ships; a reply op lands and is consumed by the next gather — each shown). Board lanes are discovered from the live `BACKLOG.md` set + silos (a new fixture repo with a backlog appears without config, shown); paused/archived badges render; dormant toggle works. Drill-down links resolve on a real card to mirror route, Drive URL, and `obsidian://` URI (shown). SSE: a state-file change reflects in the open UI without reload (shown); the uvicorn graduation trigger exists as a standing-check. Mobile: at 375px and 320px — hamburger drawer, accordion expansion on both surfaces, zero horizontal overflow (shown). Full suite green; fresh-context **review-gate PASS zero CRITICAL** (edit-verb leg human-reviewed); version bump; both-scope reinstall.
