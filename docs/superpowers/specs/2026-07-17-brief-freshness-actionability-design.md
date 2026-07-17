# Brief freshness + actionability — design

**Date:** 2026-07-17 · **Status:** approved by Seth (chat, this date) · **Owner:** aios engine
**Origin incident:** the 2026-07-17 afternoon brief rendered the 07:02 nightly cache as current —
two long-overdue tasks the owner had completed that day (both flipped Done in Notion 16:05Z/17:03Z)
re-rendered as overdue cards, a held panel that had been fully approved in the morning walk
re-rendered as pending, and two always-on health counters (the economic-figures header, the
pipeline-anomalies count) printed with no action path. Root cause: `cache-status` freshness is a pure 720-minute timer, and
the fresh-path delta check looks only for *newly added* urgent Notion tasks, never completions.

## Decisions (made with Seth, 2026-07-17)

1. **Refresh rule → event-based invalidation** (over tighter timer / always-re-gather).
2. **Health counters → must earn their line** (delta-or-silence; extend A91 to also remove the
   advisory economic header).
3. **Progression → show the movement** (cleared-since-last-brief + now-in-Act markers).

Plus two straight bugs fixed regardless: carryover-deferral revalidation, and live-held-panel
enforcement / citation honesty.

## §1 — Event-based cache staleness (`brief_session.py cache-status` v2)

`cache-status` keeps its contract (`fresh | stale | degraded | missing`) but the verdict stops
being a pure timer. It compares the cache's `generated_utc` against three change signals; ANY one
newer than the cache flips the verdict to `stale`:

- **Walk ledger** — `brief-session.json` `updated_utc` (a decision/deferral recorded after the
  gather means the board moved). Would alone have caught the origin incident (walk 15:39Z vs
  cache 11:02Z).
- **Write-back receipts** — newest timestamp in the `notion-changelog.jsonl` tail.
- **Notion watermark** — one cheap query: max `last_edited_time` across the allowlisted task DBs
  (the `notion.write.writable` set, read via the same gather path). Catches direct-in-Notion edits.
  Skipped (with the existing `degraded` semantics) when Notion is unreachable or the install has
  no Notion.

`max_age_min` survives only as a backstop ceiling (a cache older than it is stale even with no
signal). The logic lives in the tested tool — new inputs, same tested-boolean contract; the skill
prose does not re-derive it. New flags: `--session <brief-session.json>`,
`--changelog <notion-changelog.jsonl>`, `--notion-watermark <iso>` (the gatherer passes the value;
the tool stays fact-free and offline-testable).

## §2 — Render-path guards (bugs, not features)

- **Carryover-deferral revalidation.** When `resume_or_new` / walk seeding carries prior
  deferrals forward, any whose task is Done (or absent) in the *fresh gather* is dropped from the
  walk and reported as one line: `✅ auto-cleared: {title} (completed since deferral)`. A deferral
  never re-renders a completed task as a card.
- **Live held-panel enforcement.** `validate_cache` gains an assertion that the held panel about
  to render matches a fresh `queue_tx.py select --stage awaiting` count at render time — the
  already-mandated live recompute becomes enforced (INVALID on mismatch), not advisory prose.
- **Citation honesty.** A card may carry "queried live {date}" only when the fact was queried in
  THIS run; cache-sourced facts render "as of {generated_utc}". Enforced in `brief_render.py`
  (the cite string is derived from the fact's source tag in the cache JSON, not authored prose).

## §3 — Visible progression (the movement line)

Before a full gather overwrites `brief-cache.json`, it snapshots the incumbent (reuse the existing
`state/brief-sessions/` archive dir; keep exactly one `brief-cache.prev.json`). The render-time
masthead then adds, both engine-emitted by `brief_render.py` and lifted verbatim:

- `✅ N cleared since last brief — {titles}` (collapse past ~5 titles to a count + "expand");
  an item counts as cleared when it was in the prior cache's stations/Act and is absent (Done)
  in the fresh gather.
- `↑ now in Act` tag on any Act row that was not in the prior cache's Act slice.

Zero-delta renders nothing (no "0 cleared" line — same earn-your-line rule as §4). This is the
progression metric Seth asked for: the brief visibly registers completed work, then shows what
moved up — done items drain, the next urgency tier rises, all tiers eventually surface.

## §4 — Health lines earn their place

The masthead health lines (`pipeline_health.py`, factory-health, resolve header) become
**delta-gated**: the cache JSON stores each line's last-rendered fingerprint (normalized text
hash); at render, a line prints only if its content changed since the last brief (first
appearance counts as changed). Steady-state prints nothing. When the pipeline-anomalies line does
print, it carries an expand affordance — "show the N new anomalies" — listing the underlying
context-log rows with a per-item triage choice (dismiss / open thread), instead of a bare counter.

**A91 scope extension (per Seth 2026-07-17):** A91 additionally removes the `economic_header`
advisory line ("⚠ N economic figures with no paper") and whatever of the
`entities_dir`/`cache_dir`/sweep chain then has no remaining consumer — reversing A91's current
KEEP note. The whole resolve surface retires, not just the INCOMPLETE check; A75 (paper-evidence
verifier) remains the named successor for the underlying need.

## Out of scope

- No new store, surface, or dashboard; everything lands in `brief_session.py`, `brief_render.py`,
  `references/gather.md`, and `skills/brief/SKILL.md`.
- A91 itself (already backlogged) executes separately; this spec only widens its scope.
- The walk UX, station order, two-layer voice, and gate approval flow are unchanged.

## Acceptance

- `cache-status` returns `stale` when any of: walk ledger newer than cache, changelog tail newer,
  supplied Notion watermark newer, or age > `max_age_min` — unit-tested offline for each signal.
- A deferral whose task is Done in the fresh gather never renders as a card (test: seeded ledger +
  gather fixture).
- `validate_cache` INVALID when rendered-held ≠ live awaiting count.
- Masthead shows `✅ N cleared` + `↑ now in Act` from a prev-cache diff fixture; zero-delta emits
  neither line.
- Health lines suppressed when fingerprint unchanged; anomalies line expandable to per-item rows.
- Suite stays green.

## Ecosystem check

Capabilities: **C1** event-based cache staleness · **C2** render-path guards (deferral
revalidation, live-held assertion, citation honesty) · **C3** board diff / movement line ·
**C4** delta-gated health lines.

### Leg 1 — Anthropic-first

```
$ ls C:/Users/sethh/.claude/plugins/cache/claude-plugins-official/
superpowers
# Session skill roster (system prompt, 2026-07-17) includes anthropic-skills:morning
# ("Render the user's morning brief as a styled HTML artifact...") and anthropic-skills:schedule.
```

`anthropic-skills:morning` is the only adjacent Anthropic surface — a calendar-driven HTML-artifact
morning brief. Reference-only: our brief is chat-native, pipeline/queue-backed, and no-artifact by
design (2026-07-12 decision). Native Claude Code offers no cache-freshness primitive for a skill's
own cache; session hooks could signal "a session happened" but are strictly weaker than the three
in-band signals chosen (ledger/changelog/watermark), which are already written by our own tools.

### Leg 2 — Public marketplace

```
$ npx skills find cache invalidation staleness
vtex/skills@headless-caching-strategy            524 installs
vtexdocs/ai-skills@headless-caching-strategy      77 installs
oimiragieo/agent-studio@next-cache-components     34 installs
yanko-belov/code-craft@caching                    33 installs
claude-dev-suite/claude-dev-suite@caching-strategies 27 installs
$ npx skills find daily brief dashboard
alinaqi/claude-bootstrap@maggy                    87 installs
mohitagw15856/pm-claude-skills@dashboard-brief    49 installs
```

All hits are web/CDN caching guidance or unrelated PM templates — nothing addresses a local
pipeline cache's staleness contract or board-diff rendering. No adoptable skill.

### Leg 3 — Own skills/tools (the richest leg)

```
$ ls Projects/aios/engine/tools/
brief_render.py  brief_session.py  brief_threads.py  ... pipeline_health.py  queue_tx.py
resolve_brief.py  settle_reconcile.py  ...  (39 .py tools; full listing captured 2026-07-17)
```

Every capability lands as an **extension of an existing own tool** — zero new files:
C1 extends `brief_session.py cache-status` (the tested boolean already exists; new inputs only).
C2 extends `brief_session.py` walk seeding + `validate_cache` + `brief_render.py`; the live-held
read is the existing `queue_tx.py select --stage awaiting`. C3 reuses the `state/brief-sessions/`
archive dir and `brief_render.py`'s masthead emitters. C4's delta-gating has an in-house
precedent: `resolve_brief.py`'s A60 steady-state suppression (`ℹ resolve steady-state`) is exactly
"a known ceiling is not fresh news" — C4 generalizes that pattern to all health lines.
`settle_reconcile.py` is the existing prior-state-vs-now diff pattern C3 follows.

### Leg 4 — Full-service replacement

```
$ cat _tools/ecosystem-check/references/platforms.md   (validated 2026-07-10)
Wired MCP connectors: Notion, Google Drive, Gmail, Google Calendar, Slack, Granola, Replit,
Supabase, Moodtrip. Platforms: Composio, Pipedream, Zapier, Make, n8n, Activepieces, ...
```

Not service-replaceable: these are render/consistency semantics *inside* our own engine's brief,
over our own queue/ledger files. Notion (already wired) stays the data plane; no automation
platform owns the brief's freshness contract without owning the whole pipeline — which is the
product itself. No cost/lock-in decision here, so no `deep-research` pass required.

### Verdict table

| Capability | Verdict | Source | Why |
|---|---|---|---|
| C1 event-based staleness | adapt-skill | own `brief_session.py cache-status` | tested boolean exists; add 3 signal inputs |
| C2 render-path guards | adapt-skill | own `brief_session.py` / `validate_cache` / `queue_tx.py` | enforcement of already-mandated behavior |
| C3 movement line | adapt-skill | own `brief_render.py` + `brief-sessions/` archive; `settle_reconcile.py` pattern | diff-then-render pattern already in-house |
| C4 delta-gated health | adapt-skill | own A60 steady-state pattern (`resolve_brief.py`) generalized | in-house precedent; `anthropic-skills:morning` reference-only |
