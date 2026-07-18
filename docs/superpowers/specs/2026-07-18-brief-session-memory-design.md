# Brief session memory (A95) — design

**Date:** 2026-07-18 · **Status:** approved by Seth (chat, this date — "spec A95 with the info from this chat") · **Owner:** aios engine
**Origin:** the first post-A93 morning. The cache-writer gathered a rich, correct picture from
threads + Notion, yet the rendered brief still (a) hedged "status unconfirmed — no longer in the
view" for a task that was simply Done, (b) re-surfaced two items whose resolutions the owner had
given in the prior day's walk, and (c) emitted every station/act item with `id: null`. The owner's
verdict: "the brief doesn't have awareness of what we actually worked on the day before — that's
our biggest gap."

## Decisions already made (chat, 2026-07-17/18)

1. **Conclusions land in the owner's EXISTING per-teamspace Session/Decision Logs** — the profile's
   `notion.write.writable.session_log` / `decision_log` groups. A new cross-silo table was built,
   caught as re-owning what those logs own, and folded back the same day. Never create a new
   Notion surface for this (the env now carries a standing fold-in-first rule).
2. **The content-field fence stays.** `notion_writeback` keeps refusing prose/title fields. The
   defect class is *refused-write-with-no-fallback*, not insufficient rights.
3. **Threads are the proven fallback** — the gather demonstrably reads `state/threads/*.md` well;
   four hand-written thread records were the manual patch this spec automates.

## §1 — Done-vs-vanished: never hedge on a hidden row

The gather reads open tasks through a filtered view (`Status ≠ Done`), so a completed task simply
*vanishes* between gathers, and today's gather writes "status unconfirmed / dropped". Fix: during
the gather, for each task id present in the prior cache (`brief-cache.prev.json`, kept by A93 §3)
but absent from the fresh open-view pull, make ONE direct task query — a **new by-page-id
`retrieve` op added to `notion_gather.py`**, reusing its existing `_request`/token/`normalize_page`
machinery (verified 2026-07-18: the tool today exposes only data-source/database *query* endpoints,
no `GET /pages/{id}` — the reviewer leg caught this spec's first draft citing the reader as
already existing). Verdict:
- **Done** → the item renders as completed — it feeds A93's `✅ cleared since last brief` movement
  line with its completion date, and never renders as a card or a hedge;
- **genuinely absent** (deleted/moved/permission) → one explicit line
  `⚠ {title} — no longer reachable (was open on {prev date})`, because silent disappearance from
  the SSOT IS news.
Degrade: if the direct query fails (offline/degraded gather), keep the item with an honest
`unverified since {prev generated_utc}` tag — never "unconfirmed" without saying why.

## §2 — Session-resolution ingest + the conclusion-write mandate

Two directions, one loop:

**Write side (the mandate).** The brief SKILL's decision step requires that EVERY walk/session
conclusion lands in a durable, gather-readable home at decision time:
- flip the task field when the target is flippable (unchanged, already works);
- write a row to the owning silo's **session log** (or **decision log** for decisions) via
  `notion_writeback.py` — new `add-row` support for the allowlisted `session_log`/`decision_log`
  groups (append-only page-create; the `pause_economic` content gate applies to row text exactly
  as it does to flips);
- AND, when the natural Notion target is fenced (content field, unlisted DB), also write/update
  `state/threads/{id}.md` (schema unchanged). A refused write with no recorded fallback is a bug,
  not an outcome — the walk must never conclude an item into thin air.
- An install whose profile lacks a `session_log` group degrades to threads-only (fact-free — read
  the lever, never assume the reference profile's tables).

**Read side (the ingest).** The gather reads the current walk ledger (`brief-session.json`) plus
the recent archives (`state/brief-sessions/`, last N days, default 3) and folds decisions onto
matching items by `item_id`: a decision with `executed: true` (or an explicit resolved/closed
action) suppresses or reframes the matching card — rendered as "resolved by {who} {date}:
{action}" in the movement/cleared section — never re-surfaced cold. The precedent is
`settle_reconcile.py`, which already reads ledger decisions with `notion_write` intents; this
generalizes the read to all decisions. Deterministic, offline-testable (ledger fixtures).

## §3 — Stable item ids

The cache contract makes `id` REQUIRED and non-null on every station/act item: `validate_cache`
asserts it (same INVALID semantics as the A93 held-parity assert). Source: the Notion task page id
when the item is a task; else a deterministic slug of (domain, title). The 2026-07-18 all-null-id
cache silently broke the thread join, the A93 movement diff (`_all_item_ids` keys by id), and
standup delta matching — a required-field assert makes that class impossible.

## Out of scope

- No new Notion surfaces of any kind (standing rule).
- The env-ops sibling H86 (daily-sync findings → proposed Notion tasks) is a separate spec.
- Walk UX, station order, graded voice, gate flow: unchanged.

## Acceptance

- A fixture where a prior-cache task is absent from the open view but Done on direct query renders
  "completed {date}" and appears in the movement cleared-line — never "unconfirmed" (shown); the
  genuinely-absent branch renders the ⚠ line (shown).
- A fixture walk ledger carrying a content-fenced resolution suppresses/reframes the matching card
  in the next render (shown).
- `notion_writeback.py add-row` writes an allowlisted session-log row with a changelog receipt and
  REFUSES an unlisted DB and economic content (both shown, offline via the tool's dry-run/test
  seams).
- `validate_cache` INVALID on any station/act item lacking a non-null `id` (shown).
- The brief SKILL text carries the conclusion-write mandate; `references/gather.md` carries the
  ledger-ingest and Done-vs-vanished steps.
- `notion_gather.py retrieve <page_id>` returns a normalized page dict, offline-tested against a
  fixture response (the op is net-new; the plumbing is existing).
- Full suite green; fresh-context review subagent (not the builder) reports zero CRITICAL.

## Ecosystem check

Capabilities: **C1** Done-vs-vanished direct query · **C2** conclusion-write mandate
(`add-row`) + session-resolution ingest · **C3** required stable ids.

### Leg 1 — Anthropic-first

```
$ ls C:/Users/sethh/.claude/plugins/cache/claude-plugins-official/
superpowers
```

No native Claude Code capability addresses a skill-cache's session-continuity semantics; the
official plugin surface is Superpowers (process skills). Session hooks can signal "a session
happened" but carry no per-item resolution data — strictly weaker than reading the walk ledger our
own tools already write.

### Leg 2 — Public marketplace

```
$ npx skills find session memory task sync
promptingcompany/nv-skills@nemo-rl-session-memory     39 installs
sundial-org/awesome-openclaw-skills@git-notes-memory  36 installs
catlog22/claude-code-workflow@session-sync            30 installs
$ npx skills find notion task status sync
claude-office-skills/skills@notion-automation         3.7K installs
```

The session-memory hits are agent-memory scaffolds (<40 installs each), not pipeline-cache
semantics. `notion-automation` (3.7K) is generic Notion-API prompting guidance — our write path is
already a tested tool (`notion_writeback.py`) with fences the generic skill lacks. Nothing
adoptable.

### Leg 3 — Own skills/tools (the richest leg)

```
$ grep -n "def annotate\|def main" engine/tools/brief_threads.py | head -4
147:def annotate_cache(cache, threads):
195:def main(argv=None):
$ grep -n "notion_write\|def reconcile" engine/tools/settle_reconcile.py | head -6
4:A prior brief decision may record executed=True with an intended notion_write, yet the flip may
16:    """Executed decisions with a notion_write intent that has no matching (page_id, field, new) receipt."""
```

Every capability extends an existing tool: **C1** adds a small by-page-id `retrieve` op to
`notion_gather.py` (its `_request`/token/`normalize_page` machinery is the reuse; the tool has no
by-id read today — corrected after the reviewer leg refuted the first draft's claim) + A93's
`brief-cache.prev.json`; **C2 read** generalizes `settle_reconcile.py`'s existing
ledger-decision scan (the in-house precedent, verified above) and folds via
`brief_threads.annotate_cache`'s existing join shape; **C2 write** extends `notion_writeback.py`
(allowlist + `pause_economic` + changelog receipts already tested — `add-row` is a new op on the
same fences) against the profile's `session_log`/`decision_log` groups added 2026-07-18; **C3**
extends `brief_session.validate_cache` (the A93 held-parity assert is the pattern). Zero new
files beyond tests.

### Leg 4 — Full-service replacement

```
$ cat _tools/ecosystem-check/references/platforms.md   (validated 2026-07-10)
Wired MCP connectors: Notion, Google Drive, Gmail, Google Calendar, Slack, ... Platforms:
Composio, Pipedream, Zapier, Make, n8n, ...
```

Not service-replaceable: this is continuity logic between our own cache, ledger, and threads.
Notion (already wired) remains the data plane; no automation platform can read the walk ledger or
enforce the fence contract without owning the pipeline — which is the product. No cost/lock-in
decision → no `deep-research` pass required.

### Verdict table

| Capability | Verdict | Source | Why |
|---|---|---|---|
| C1 Done-vs-vanished | adapt-skill | own `notion_gather.py` `_request`/`normalize_page` machinery + A93 prev-cache | new small `retrieve` op on existing plumbing (no by-id read exists today) |
| C2 conclusion write (`add-row`) | adapt-skill | own `notion_writeback.py` fences + 2026-07-18 profile groups | new op, existing allowlist/pause/receipt machinery |
| C2 resolution ingest | adapt-skill | own `settle_reconcile.py` ledger scan + `brief_threads.annotate_cache` join | generalize a tested read |
| C3 required ids | adapt-skill | own `brief_session.validate_cache` (A93 assert pattern) | one more required-field gate |
