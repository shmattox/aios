# reflect — daily work-into-knowledge distillation (the Reflect phase)

- **Status:** design (brainstormed 2026-07-11)
- **Repo / backlog home:** `Projects/aios` (AIOS engine — universally-viable pipeline stage). Backlog item to be opened in `Projects/aios/BACKLOG.md`.
- **Owning lifecycle rung:** Plan → Build → Review → Ship → **Reflect** — the rung the four-phase lifecycle names but the pipeline never operationalized.

## Problem

A day of work — the *conversation*, what we learned, decided, and did — is captured but never actively **reflected on to grow what we know**. Today:

- `session-capture` mines each session into a record (Focus / Outcome / Why) → `raw/sessions/`.
- `ingest` folds each record into that day's **journal note** (chronological log).
- `garden` F8 (`passes-reflection.md`) promotes session evidence into `knowledge/`/`decisions/` — but only **weekly (Sundays 3:10 AM)**, over a **30-day window**, and it **explicitly never touches `CLAUDE.md`/`SKILL.md`**.

So two gaps are real: (1) there is **no daily beat** — a genuinely durable insight from today waits up to six days for Sunday's garden; and (2) the **self-learning loop** — turning a correction or a confirmed working-approach into a rule in the owning `CLAUDE.md` "Lessons" block or Memory — is **100% manual**, firing only when Seth corrects Claude in-session. Nothing distills the day's *conversation* into a behavior rule automatically.

The trigger case: Seth pastes an x.com post into a chat across three terminals expecting the *ideas* to grow the KB; investigation showed the link only lands as a breadcrumb in the session `intents` file, and — more fundamentally — the *conversation itself* is not being reflected into evergreen knowledge or lessons.

## Goals

1. A **once-daily** reflection pass over the day's work that proposes KB growth of four kinds, all human-gated.
2. Own the genuine gap: **daily Lessons/behavior-rule** proposals to the owning `CLAUDE.md` / Memory.
3. Own a **daily journal reflection** ("what we learned today").
4. Surface **same-day durable knowledge/decisions** promptly — without waiting for Sunday garden — by **reusing** garden's F8 judgment scoped to a 1-day window, **not** rebuilding it.
5. Add minimal new surface: everything downstream (gate, lanes, routing, journal path, F8 rulebook) is reused.

## Non-goals

- **Not** a new cross-session knowledge-clustering engine. garden F8 stays the authority for the 30-day window (emergent themes ≥3-recurrence, merges, contradictions).
- **Not** a URL/link fetcher. The originating example was a link, but the capability is conversation-reflection; open-web link extraction already exists (`url_extract.py`, A57) and x.com has its own authed bookmark lane (`x-bookmark-capture`, H29). Out of scope here.
- **Not** auto-ship. Like `ingest`, reflect drafts only; knowledge/`CLAUDE.md`/Memory writes are human-gated.
- **Not** a replacement for the manual in-session lessons loop — it complements it (catches what wasn't corrected live).

## Design

### Stage placement & cadence

New Phase-A stage `reflect`, running **once daily after `ingest`** (~02:30; session-capture ~01:30, ingest ~02:00 precede it, so the day's records + journal notes already exist). Drafts only; ships nothing. Scheduling: **its own `AIOS aios-reflect` native task** for an isolated run log (rejected alternative: appended to the ingest task body — cheaper but muddies ingest's per-item contract and loses a clean log). The stage is a thin `claude -p` harness run of the `reflect` skill over the `reflect.py` scaffolding, mirroring `session-capture`/`session_synth.py`.

### Inputs (fact-free; all from profile)

For the **target day** (default: yesterday at nightly run; a `--day`/`--since` override for backfill), across **all** domains:

- The day's **session records** — `<vault>/<kb>/raw/sessions/<source>-<YYYY-MM-DD>-*.md` (Focus/Outcome/Why + intents + files + commits + tool_counts).
- That day's **journal notes** — `<vault>/<kb>/wiki/journal/<YYYY-MM-DD>.md`.
- Optionally a record's `transcript_path`, consulted **only** for a record that reads as high-signal (bounded — cost control; most runs never open a transcript).
- Profile: `session_capture.domain_map` (route a learning to its KB), `vault` + `vault.live_kb_map`, `lane_policy` (gate resolution). Reused, never re-owned.

`raw/` is **immutable evidence** — reflect reads it, never edits it (F2.9 discipline; the journal note may receive a stub link, the source record never).

### The four reflection passes

Over the day's arc, reflect runs these and drafts **only where there is genuine growth** (see Signal bar):

1. **Lessons pass (NEW — the differentiator).** Did the day's conversation contain a *correction* ("no, do it this way", a reverted approach) or a *confirmed working-approach* worth codifying? If so, propose a **one-line rule** for the owning `CLAUDE.md` "Lessons" block (routed by `domain_map` → the right project/subsystem `CLAUDE.md`) or a Memory `feedback` entry (with **Why** / **How to apply**). Output is a **proposed diff** (anchor line + insertion), never a silent edit. This is the pass garden F8 forbids by contract; no skill/service does it (see §Ecosystem-check).
2. **Knowledge pass (REUSE, 1-day scope).** Run garden's existing F8.4/F8.5 judgment (`passes-reflection.md`) scoped to **today's** records only — a same-day self-evidently durable concept → a `knowledge/` draft using ingest's A56 deep-stub schema (Core idea / How to apply / Proposed target + neighbours). It does **not** implement F8's ≥3-recurrence clustering — that stays garden's weekly job.
3. **Decisions pass (REUSE).** A method/architecture decision made today → a `wiki/decisions/<date>-<slug>.md` ADR draft (Dev) or a proposed `Memory/decisions.md` line (env-method). Business/economic decisions are **surfaced for the human** to log in Notion — never auto-written (operational-state canonical rule).
4. **Journal reflection pass (REUSE journal path).** Append a "What we learned" reflective section to the day's journal note (merge, not overwrite — the ingest clobber-guard applies).

### Boundary with garden F8 (keep them from fighting)

| Axis | `reflect` (new) | `garden` F8 (existing) |
|---|---|---|
| Cadence | Daily (~02:30) | Weekly (Sun 3:10) |
| Window | 1 day | 30 days |
| Threshold | Immediate (today's signal) | ≥3-recurrence (F8.4) / strong single passage (F8.5) |
| `CLAUDE.md`/Memory Lessons | **Owns** | Forbidden (out of scope) |
| Cross-session clustering/merge | Defers to F8 | **Owns** |
| Ships? | No — drafts to gate | No — drafts to gate |

**De-dup handshake (reuses an existing guard):** reflect enqueues its knowledge/decision drafts as normal queue items (`lane: review`, `awaiting`). garden already **skips items still `awaiting` in the queue and does not re-propose pending garden proposals** (verified in its run log: *"yesterday's 4 pending garden proposals were not re-proposed"*). So a reflect draft in the queue is visible to Sunday's garden and will not be duplicated. No new de-dup code.

### Routing, lanes, and the gate

- Each candidate routes to its KB / `CLAUDE.md` via `domain_map`.
- **All reflect outputs default to the human gate** — `lane: review`, `recommended: hold`. Knowledge writes, ADRs, and especially `CLAUDE.md`/Memory edits are never auto-shipped. FamilyOffice keeps Paper-Governs (economic learnings stay `legal_status: verbal`; the kb backstop holds regardless).
- Everything rides the **existing** gate (`/aios:gate` human pass). Every ship is revertible via `rewind`.
- Lane resolution reuses `lane_policy.resolve_review_gate` / `gate_to_lane` — no new lane logic.

### Signal bar (load-bearing)

Reflection that cries wolf gets ignored. Explicit discipline:

- **Prefer proposing nothing over noise.** Most days yield little; a no-op run is a success, logged as such.
- **Per-run caps** bound gate volume (e.g. ≤N lessons, ≤N knowledge drafts/day — exact N in the plan).
- **De-dup before drafting knowledge:** read the target `knowledge/` neighbourhood; prefer **updating** an existing page (merge draft) over spawning a duplicate. For lessons, check the target `CLAUDE.md` Lessons block for an existing equivalent rule before proposing.
- **Binary bias:** when unsure whether something is durable, drop it (a missed insight resurfaces; F8's weekly pass is the backstop).

### `reflect.py` — deterministic scaffolding (the tool)

Stdlib-only, fact-free, unit-tested (mirrors `session_synth.py`). Owns the mechanical parts so the skill owns only judgment:

- `discover(day, kb_map)` — find the day's session records + journal notes across KBs.
- `dedup_context(candidate, vault)` — surface the existing knowledge neighbourhood / `CLAUDE.md` Lessons block for the skill to judge against (no model call).
- `verify(drafts)` — re-read each draft: exists, non-empty, valid frontmatter, routed to a real KB in `live_kb_map`; atomic write (tmp → validate → replace).
- Enqueue via `queue_tx.py` (reused) and append one `state/context-log.jsonl` line (`stage:reflect · run_id · {lessons:n, knowledge:n, decisions:n, journal:n, by_kb:{…}} · anomalies`).

The skill (`skills/reflect/SKILL.md`) does the four passes' judgment and calls the tool for discovery/dedup-context/verify/enqueue.

### Discipline (Stage Contract)

Fact-free · self-contained · **VERIFY** every draft · atomic-write · self-heal (torn draft → rebuild from evidence, fix-then-tell) · re-sync-on-edit · context-log. Autonomic in that it *drafts* a proposal (like ingest); the *knowledge* is human-gated downstream. **Fail loud rather than fabricate** — never write a lesson/knowledge draft for evidence it didn't read; a day with no records is a clean no-op, not a guess.

## Data flow

```
session-capture (01:30)  →  raw/sessions/<kb>/<date>-*.md
ingest (02:00)           →  wiki/journal/<date>.md (+ staging drafts, queue items)
reflect (02:30)          →  reads the day's records + journal
                            ├─ lessons pass   → proposed CLAUDE.md/Memory diff  (lane: review)
                            ├─ knowledge pass → wiki/staging/<slug>.md          (lane: review)
                            ├─ decisions pass → wiki/decisions/ or Memory line  (lane: review)
                            └─ journal pass   → merge into wiki/journal/<date>  (lane: review)
                            → queue_tx enqueue + context-log line
gate (/aios:gate, human)  →  ships approved drafts; rewind-revertible
garden F8 (Sun 3:10)      →  30-day clustering; skips reflect's still-awaiting items (no dup)
```

## Error handling & edge cases

- **No records for the day** → clean no-op; context-log `{…:0}`; exit 0.
- **A KB not in `live_kb_map`** → hold + flag that candidate; never fall back to a wrong vault (session-capture rule).
- **Journal note already substantial / authored by another producer** → merge draft, `lane: review`, `rec_reason: "journal exists — confirm merge"` (ingest clobber-guard reused).
- **`CLAUDE.md` target moved/renamed** → the lessons diff anchor fails to resolve → hold + flag, propose nothing rather than mis-insert.
- **Backfill run over N days** → per-day loop; each day is an independent atomic unit; a bad day fails loud without poisoning the others.
- **Transcript unavailable / oversized** → skip the deep read, reflect from the record only (never block on it).

## Testing

- `test_reflect.py` (mirrors `test_session_capture.py`): fixture day with N session records → assert `discover` finds them; `verify` catches a torn/invalid draft; a no-record day is a clean no-op; a KB-not-in-map candidate is held; the dedup-context surfaces an existing neighbour.
- Golden-ish judgment check: a fixture record containing an explicit correction → the lessons pass proposes a rule diff against the right `CLAUDE.md`; a fixture with only chatter → proposes nothing (signal bar).
- De-dup handshake: a reflect draft left `awaiting` is not re-proposed by a simulated garden pass.
- **Gate discipline:** every reflect-produced queue item asserts `lane: review` / `recommended: hold`; nothing auto-ships; no canonical wiki/`CLAUDE.md`/Notion/Memory path is touched by reflect directly.

## Rollout

1. Build `reflect.py` + `skills/reflect/SKILL.md` + tests behind the AIOS engine.
2. **First run = one-time backfill of the last ~7 days** of session records (so recent work — including the conversation that designed this — isn't lost), then daily thereafter.
3. Register `AIOS aios-reflect` (02:30 daily) via the engine's task-registration pattern; ships **disabled/dry-run first**, enabled by a deliberate Seth decision (factory-gate precedent).
4. Validate: run in-session over the backfill window, review the drafts at the gate, confirm signal quality before enabling unattended.

## Ecosystem check

Decomposed capabilities: (C1) discover the day's session records; (C2) distill conversations → knowledge/decisions; (C3) extract **Lessons → CLAUDE.md/Memory**; (C4) route + lane + gate candidates; (C5) de-dup vs pending proposals; (C6) daily journal reflection.

**Leg 1 — Anthropic-first** (native Claude Code capability, `anthropics/skills`, official plugins):

```
$ (find-skills skill invoked) query: "daily reflection journaling session summarization knowledge distillation self-learning lessons"
→ Native Claude Code offers context-window *compaction* (older dialogue summarized into a
  CLAUDE.md-style memory once context fills) and per-session auto-memory (fact files).
  Neither is a daily, cross-session, human-GATED distillation into an Obsidian KB.
  anthropics/skills: no reflection/distillation/self-learning skill (confirmed via the
  marketplace query in Leg 2, which indexes anthropics/*).
```

**Leg 2 — Public marketplace** (`find-skills` skill → `npx skills find`):

```
$ npx --yes skills find "reflection journal knowledge distillation"
No skills found for "reflection journal knowledge distillation"
$ (exit 0)
```

**Leg 3 — Own skills/tools** (`Glob **/SKILL.md` over `Projects/aios/skills`, `_tools`; plus `Scripts/`):

```
$ Glob **/SKILL.md  (aios/skills) → rewind, session-capture, sort, setup, gate, ingest, garden, inbox-capture, brief
$ Glob **/SKILL.md  (_tools)      → sync-cowork-tasks, usage-audit, installed-skills, dataroom-ingest, env-maintenance, new-business-unit, ecosystem-check
Assessed for reuse:
- garden/rulebook/passes-reflection.md (F8): ALREADY distills raw/sessions + journal →
  knowledge/ (F8.4 emergent themes ≥3), knowledge/decisions (F8.5 promotions), contradictions
  (F8.1). Weekly (Sun 3:10, verified via schtasks), 30-day window. EXPLICITLY excludes
  CLAUDE.md/SKILL.md ("out of scope entirely"). → reflect REUSES this judgment at 1-day scope; does NOT rebuild it.
- session-capture/session_synth.py: session-record discovery/scan/mark pattern → reflect.py mirrors it.
- ingest (A56 distill_class deep-stub: Core idea/How to apply/Proposed target): reused for C2 knowledge drafts.
- gate + lane_policy.py (resolve_review_gate/gate_to_lane) + queue_tx.py + domain_map: reused wholesale for C4.
- garden's queue-awareness ("don't re-propose still-awaiting items"): reused as the C5 de-dup handshake (no new code).
- usage-audit (_tools): retrospective over transcripts for FRICTION/automation candidates — adjacent but
  different purpose (how Seth uses Claude, not growing the KB). reference-only, not reused.
```

**Leg 4 — Full-service replacement** (wired MCP connectors → bounded WebSearch):

```
$ WebSearch "AI daily work reflection distill conversations into knowledge base self-improving agent memory 2026"
→ Reflexion (verbal self-reflection in persistent memory), mem0, and 2026 agent-memory frameworks
  (Databricks, Vectorize, Towards Data Science surveys) all implement "periodically distill episodic
  → semantic memory." This VALIDATES the pattern but targets RUNTIME agent-context memory, not a
  human-GATED distillation of a person's work conversations into an Obsidian vault + CLAUDE.md rules.
  No service ingests our session records, routes by domain, and proposes through OUR review gate.
  Reflect.app/Mem/Tana (consumer PKM) can't replace an internal pipeline stage writing to our vault.
```

Verdict table:

| Capability | Verdict | Source / why |
|---|---|---|
| C1 discover day's records | `adapt-skill` | reuse `session_synth.py` scan/discover pattern |
| C2 distill conversations → knowledge/decisions | `adapt-skill` | reuse garden **F8 rulebook** + ingest **A56** deep-stub, scoped to 1 day |
| C3 extract Lessons → CLAUDE.md/Memory | `build-because-none` | F8 forbids CLAUDE.md; marketplace empty; no service does it — the genuine differentiator |
| C4 route + lane + gate | `drop-in-skill` | reuse `lane_policy` + gate + `domain_map` + `queue_tx` |
| C5 de-dup vs pending | `drop-in-skill` | reuse garden's existing "skip still-awaiting" guard |
| C6 daily journal reflection | `adapt-skill` | reuse ingest's journal merge path |

**Net:** genuinely new code = C3 (the lessons pass) + a thin `reflect.py` orchestrator + scoping the F8 judgment to a 1-day window. Everything else is reuse. Custom-build is confined to the thin differentiator the ecosystem lacks (the daily self-learning loop), consistent with Adopt-and-Extend / Shop-Before-Build.

## Open questions (resolve in the plan)

- Exact per-run caps (N lessons / N knowledge drafts).
- Whether the knowledge pass invokes the F8 rulebook prose directly or a shared extracted helper (avoid drift between garden's copy and reflect's).
- Backfill depth (7 days assumed) and whether the first backfill is one bundled gate batch or per-day.
