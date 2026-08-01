# Activity view — live runs across every agent surface (design)

- **Date:** 2026-08-01 (brainstormed with Seth in one session; design approved same session)
- **Item:** proposed **AIOS A-new** (dashboard v2 continuation — a sibling to A117 Flow), with a small **env-ops companion** leg for the factory adapter. See §Routing.
- **Extends:** `2026-07-20-a63-dashboard-design.md` (v1 — server, security, allowlist) and `2026-07-22-a109-dashboard-v2-design.md` (v2a — Inbox/Board/SSE/card-anatomy/drill-down). Both architectures stand unchanged; this spec adds one contract, two server routes, one view, and per-producer emit legs.
- **Distinct from A117 (Flow):** Flow visualizes *pipeline stage counts* as a DAG. This visualizes *individual agent runs* — which process is executing right now, on what, with what live log and running stats. Adjacent, not overlapping.

## Problem

The factory spools up `claude -p` drains against backlog items on its own cadence, the nightly pipeline runs unattended, and interactive sessions come and go — and Seth has **zero live insight** into any of it. The root cause is concrete: `factory_gate._launch_drain` runs each drain as a blocking `subprocess.run(..., capture_output=True)`, so the drain's output exists only *after* the whole `claude -p` returns. Nothing on disk answers "what is running right now, on what, and what is it doing." The only live signals today are `.session-lock` PID files, `gate.lock` mtime, the `.worktrees/factory-*` dirs, an append-log written *after* each drain, and the nightly-regenerated `standup.json` (post-hoc backlog groups). None of it is a run monitor.

The goal (Seth-approved this session): a **full "who and what is touching the env right now" board** — human sessions and machine agents alike — with **live-streamed logs and running stats** for each run.

## Design principles (approved 2026-08-01)

1. **One run-record contract, many producers.** A single schema every surface writes; the view reads only the contract, never each producer's internals. Adding a surface = writing a record, nothing in the view changes.
2. **Liveness is computed, never asserted.** A record's own `status` is not trusted alone — "live" = `running` AND (pid alive OR heartbeat fresh). A `running` record with a dead pid and cold heartbeat renders **loud as crashed**, never silently dropped (the env's fail-loud-on-stale discipline).
3. **Emit is best-effort and never load-bearing.** Every activity write is wrapped so it can never raise into a producer's real work — the factory's exit-0 collector contract and the pipeline's stage integrity are sacred (same discipline as `_append_log`).
4. **Our subprocesses stream; harness surfaces get a fenced best-effort tail.** Factory and pipeline are our processes → true tee'd live logs. Sessions/`/goal`/Workflows run inside Claude Code → their `log_path` points at the transcript/journal JSONL they already write, rendered format-tolerantly. If that undocumented format churns, only that one adapter degrades — factory/pipeline streaming and the whole view keep working.
5. **No second server, no new security surface.** Everything rides the existing dashboard `ThreadingHTTPServer`: 127.0.0.1 bind, exact Host validation, per-start token, zero write logic in the server. Log tailing is read-only file I/O confined to an allowlisted dir.

## The run-record contract

A tiny module — `engine/tools/activity.py` in aios — is the single definition every producer imports. One JSON file per run at `state/activity/<run_id>.json`; live logs at `state/activity/logs/<run_id>.log`. The whole `state/activity/` tree is **machine-local and gitignored** — live PIDs and worktree paths are meaningless on the other machine, and syncing them would be noise (contrast the rest of `state/`, which is synced runtime; this subtree is the exception, like `Artifacts/` and per-machine logs).

Record fields:

| field | meaning |
|---|---|
| `id` | surface-prefixed, filesystem-safe, stable across heartbeats (e.g. `factory-OP53-OP55-1785585268`, `session-Claude-12648`, `pipeline-2026-08-01`) |
| `surface` | `factory` \| `pipeline` \| `session` \| `goal` \| `workflow` |
| `title` | human label ("Draining OP53, OP55, OP56", "Nightly pipeline", "Interactive session (dev)") |
| `item_ids[]` | backlog ids (factory); empty otherwise |
| `repo` | repo slug (factory/pipeline); cwd label (sessions) |
| `pid` | OS pid for the liveness check; `null` where a surface has no obtainable pid (then heartbeat-only) |
| `started` / `heartbeat` / `ended` | ISO timestamps |
| `status` | `running` \| `shipped` \| `parked` \| `no-op` \| `failed` \| `ended` (terminal set varies by surface) |
| `tokens` / `cost_usd` | running/accumulated, best-effort |
| `log_path` | absolute path to the file the server tails; nullable |
| `detail` | short freeform status line (last log line / merge outcome / current stage) |
| `worktree` | worktree path (factory); nullable |

API (four functions, all best-effort, atomic writes via A108's `atomic_write`):

- `start_run(surface, id, **fields) -> record` — writes the initial `running` record.
- `heartbeat(id, **updates)` — refreshes `heartbeat` and cheap fields (`tokens`, `detail`).
- `finish_run(id, status, **fields)` — writes terminal status + final stats + `ended`; the file lingers for the retention window so the view shows "recently finished."
- `prune(older_than)` — removes terminal records + orphan logs past retention (24h default).

Liveness helper `is_live(record, now)` encodes principle 2; the server and view both call it rather than reimplementing.

## Producers (priority order)

1. **Factory — the centerpiece and the real work.** `_launch_drain` changes from blocking `subprocess.run(capture_output=True)` to `Popen` with a reader thread that tees stdout/stderr **line-by-line to the run's `.log`** *and* into an in-memory buffer — so the existing `_parse_output_tokens`, `_drain_result_health`, exit-code handling, and `_merge_back` logic operate on the buffer exactly as today (no behavior change to token accounting or merge-back). `start_run(surface="factory", …)` fires before launch; the existing `_refresh_lock_periodically` watchdog thread also `heartbeat`s the record; `finish_run` records the merge outcome (`shipped`/`parked`/`no-op`/`failed`). The tee reader thread must drain the pipe continuously to avoid a full-pipe deadlock. **Guard-layer note:** `factory_gate.py` is in `guard_freeze.GUARD_PATHS` (H91), so this leg is **not autonomously drainable** — it needs Seth's guard-bless + a review-gate pass.
2. **Nightly pipeline.** Wrap the pipeline entry point: `start_run(surface="pipeline")`, `heartbeat` per stage (capture→sort→ingest→gate) with `detail` = current stage, `finish_run`. Our own Python — clean, no subprocess plumbing.
3. **Sessions.** Extend the existing `SessionStart`/`SessionEnd` hooks (which already write `.session-lock`) to also `start_run(surface="session", pid, log_path=<transcript jsonl>)` and `finish_run`. The throttled `Stop` hook `heartbeat`s. Reuses infra that already fires every session; the transcript path is derivable from the session dir.
4. **/goal & Workflow (v1 = coarse, deferred fine-grain).** Both run inside a session, so v1 shows them at the **session-record** level (log = that session's transcript). When a Workflow `journal.jsonl` is detected in the session dir, it is attached as the run's `log_path`. Per-agent Workflow breakout is explicitly deferred — that is the brittle harness-coupled part and must not be load-bearing.

**Log adapter = one dumb question per surface:** *"which file does the server tail?"* — our tee'd `.log` (factory/pipeline) or the transcript/journal `.jsonl` (harness surfaces, rendered format-tolerantly: light parse, fall back to raw lines).

## Server (two routes on the existing server)

- `GET /api/activity` — the record list (live + recently-finished within retention), each annotated with computed liveness. Same JSON-serving shape as `/api/board`.
- `GET /api/activity/<id>/log?tail=N` — returns the last `N` lines now; new lines then stream over the **existing** `/api/events` SSE channel, tagged with the run id. The mtime watcher that already backs SSE extends its watch set to `state/activity/` + the active log files.
- **Security unchanged:** read-only; `<id>` must resolve to a known record whose `log_path` is under `state/activity/logs/` (or a validated transcript path) — no arbitrary paths, no `..` traversal.

## The Activity view (new nav entry; reuses A109 card-anatomy)

- **Live strip** on top: *"3 running: 1 factory · 1 session · 1 pipeline."*
- **Run cards:** surface icon, title/items, **live-ticking elapsed**, status chip (running / stale / shipped / parked / failed), running tokens + cost, health dot (pid alive / heartbeat fresh).
- **Detail pane** (the A109 card home pattern — right pane / mobile accordion / modal): **live log tail** (auto-scrolling monospace, SSE-fed) + run stats + drill-down links (worktree, the backlog item via the existing ref system, the governing doc). Crashed/stale runs render loud with "last seen" + a dismiss control.
- Dark-only, `dataviz` tokens, per A63.

## Error handling / staleness / retention

- Stale-running (dead pid + cold heartbeat) → shown as crashed, not dropped.
- `prune` on server start + periodically clears terminal records + orphan logs past the 24h retention window (the view therefore also shows "recently finished," not only live).
- Log tailing tolerates missing / rotated / harness-locked files → renders "log unavailable," never an error.

## Testing

- **Contract module:** unit tests for start/heartbeat/finish/prune and `is_live` with fake pids + a frozen clock (`aios` tests already inject time this way).
- **Factory:** extend `test_factory_worktree.py` — the Popen/tee path writes a record + log, tokens are still parsed from the buffer, merge-back still fires; a simulated crash leaves a stale record.
- **Server:** extend `test_a63_dashboard_*` — `/api/activity` returns live+recent with correct liveness; the log route tails and **rejects traversal + unknown id**.
- **UI:** smoke via the existing dashboard test pattern; an SSE log line reflects in an open view.

## Phasing (one spec, three legs against the fixed contract)

- **Leg 1** — `activity.py` contract + **factory** instrumentation + Activity view reading factory runs live. The core value; the factory-adapter sub-leg is the guard-layer/review-gate part.
- **Leg 2** — session + pipeline adapters.
- **Leg 3** — Workflow/`/goal` journal breakout, retention polish, mobile parity (rides H61/A118 mobile work).

## Routing (two homes, one design)

The **contract + server route + view + pipeline/session adapters** are shippable AIOS dashboard work → a **new AIOS backlog item** (sibling to A117 Flow), this spec in `Projects/aios/docs/superpowers/specs/`. The **factory adapter** edits `Scripts/factory-gate/factory_gate.py` — Seth's-instance infra *and* a guard-layer file — so that sub-leg is a small **env-ops companion item** that imports the aios `activity.py` (the factory already imports aios/b2g tooling via `sys.path`). Placement of the two items is Seth's call at plan time.

## Ecosystem-check

**Leg 1 — Anthropic-first** (native session capabilities, 2026-08-01):

```
/workflows            — live per-run progress, but IN-SESSION only; nothing persists to disk for an
                        always-on external dashboard, and the unattended factory has no session.
TaskList/TaskGet/
  TaskOutput          — observe background tasks WITHIN a session; not cross-surface, not on disk.
Monitor               — streams one script's stdout as chat events; in-session, single-process.
→ native covers in-session observability; NONE gives a persistent, cross-surface, machine-wide
  run registry for an unattended always-on board. Adopt /workflows' per-run live-log UX as inspiration.
```

**Leg 2 — public marketplace / open-source** (WebSearch 2026-08-01):

```
$ WebSearch "self-hosted process monitor live log streaming subprocess dashboard SSE python"
syswatch / Glances / NexusCtrl / PM2-class → OS/system monitors (CPU, mem, process table) or DevOps
  log aggregators. Wrong altitude: none models "an agent run on a backlog item with a governing doc
  + gated actions." They DO confirm SSE line-tailing is the standard streaming pattern (validates the
  stack). Harvest the SSE-tail pattern; none adoptable as a product.
```

**Leg 3 — our own skills/tools** (real reads, this session):

```
engine/dashboard/* (A63 v1 + A109 v2a: server, SSE /api/events + mtime watcher, card anatomy,
  security, 5 panels + Inbox/Board) → EXTEND (two routes + one view; server & SSE already exist).
Scripts/factory-gate/factory_gate.py (_launch_drain, _refresh_lock_periodically watchdog,
  _parse_output_tokens, _drain_result_health) → INSTRUMENT (Popen/tee; token/merge logic reused).
.session-lock writers (SessionStart/End + Stop hooks) → EXTEND to emit session records.
state/factory/{standup.json, spend-*.json, gate-metrics.json} → partial post-hoc signals, superseded
  for LIVE state by the contract; b2g parser + brief_refs drill-down → ADOPT for card links.
A108 atomic_write → ADOPT as the record writer.
```

**Leg 4 — full-service platforms** (WebSearch 2026-08-01):

```
$ WebSearch "Langfuse Langsmith Helicone self-hosted local LLM agent run observability 2026"
Langfuse (MIT, self-host)  — observes LLM TRACES via OTel GenAI spans; needs the SDK call sites
  instrumented. We do NOT control the call sites for `claude -p` subprocesses or harness sessions, so
  token/trace capture can't be wired. Self-host = Docker Compose + ClickHouse (multi-service footprint,
  same class as the A109 Plane FAIL). Shows traces, NOT "which drain is running in which worktree on
  which items with this live stdout." FAIL for the core need.
LangSmith                  — proprietary; self-host is Enterprise-only; LangChain/LangGraph-native.
  Cloud data path + not our framework. FAIL.
Helicone                   — wire-level proxy; weakest agent visibility; requires routing LLM calls
  through the proxy (impossible for `claude -p`). FAIL.
Arize Phoenix              — same trace-altitude + self-host footprint story. FAIL.
→ ALL are LLM-trace observers, not local multi-surface PROCESS/run monitors, and none can instrument
  our uninstrumentable `claude -p`/harness call sites. Trace-view UX is design inspiration only.
```

| Option | Verdict | Why |
|---|---|---|
| Langfuse / Phoenix (self-host) | ✗ | trace-altitude, not a process monitor; can't instrument `claude -p`/harness call sites; Docker+ClickHouse footprint |
| LangSmith (SaaS) | ✗ | proprietary, cloud data path, LangChain-native, Enterprise-only self-host |
| Helicone (proxy) | ✗ | wire-proxy; requires routing LLM calls it can't see |
| OS process monitors (syswatch/Glances) | ✗ (pattern adopted) | wrong altitude (CPU/mem); SSE-tail pattern harvested |
| Native `/workflows`, `TaskList` | ✗ (UX inspiration) | in-session only; no persistent cross-surface on-disk registry |
| **Extend our dashboard + new contract** | **✓ BUILD** | domain-specific run monitor over local, gated, multi-surface agent activity that no platform models; server + SSE + card anatomy already ours |

## Open questions (settle at plan time)

1. **Session pid/heartbeat cadence** — the throttled `Stop` hook's interval sets how fresh a live session's heartbeat is; confirm it's frequent enough for the liveness window without adding hook overhead.
2. **Transcript-tail rendering** — how much to parse the session/Workflow JSONL vs. show raw lines; start raw-tolerant, refine only if noisy.
3. **`state/activity/` gitignore vs. a keep-file** — confirm the subtree is ignored while `state/`'s tracked zones are unaffected (no accidental sync of live PIDs).

## Acceptance

`activity.py` writes/heartbeats/finishes a record and computes liveness (unit tests, frozen clock, fake pids — shown). A live factory drain appears in `/api/activity` as `running` with a tee'd log that streams new lines into the open Activity view over SSE, running tokens update, and it flips to `shipped`/`parked`/`no-op` on merge — with token accounting and merge-back unchanged from today (shown; factory leg guard-blessed + review-gate PASS). A live session and a pipeline run each appear with their surface, elapsed, and log. A `running` record with a dead pid + cold heartbeat renders as crashed, not dropped (shown). The log route rejects traversal and unknown ids (shown). `prune` clears terminal records past retention. Full suite green; fresh-context review-gate PASS zero CRITICAL; version bump; both-scope reinstall.
