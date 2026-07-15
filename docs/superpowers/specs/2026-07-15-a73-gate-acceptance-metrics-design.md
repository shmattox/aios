# A73 — Gate + factory acceptance metrics ($/accepted-result) — design

**Date:** 2026-07-15 · **Item:** aios A73 (+ env-leg in `Scripts/factory-gate`) · **Status:** approved
by Seth (approach B, full-both-loops scope) · **Origin:** the 2026-07-15 loop-engineering review
(env repo `docs/superpowers/findings/2026-07-15-loop-engineering-review.md`): the env has spend
metering (H62) but no *outcome* metering, so no loop can answer "what does an accepted result cost?"
and A74's graduation logic has no measured substrate.

## Problem

Every gate decision and factory drain outcome already persists — 811 shipped + 335 rejected terminal
items in the live `state/queue.json` (99.8% carrying the `recommended` ballot; `approved_by` present
since ~mid-June), backlog `## Done` lines carry closing commit SHAs, and H62 writes per-run spend
ledgers — but nothing reads them as metrics. We cannot compute acceptance rate per (kb, lane),
human-vs-auto decision mix, recommendation-override rate, or cost per accepted result.

## Decisions (from the brainstorm, Seth-approved)

- **Scope: both loops.** Gate (queue decisions) AND factory (drain ships vs git reverts).
- **Approach B:** read-only collectors + minimal write-path normalization. No new storage
  (rejected C's `decisions.jsonl` — terminal items persist; nothing prunes them). No new scheduled
  task, no new surface (H51): env collects, aios renders, model lifts verbatim — the H62 pattern.
- **"Accepted" means survived:** gate ship later `reverted`/undo-shipped counts against acceptance,
  exactly like a factory ship that gets git-reverted. `retired: true` stays accepted (lifecycle exit).

## Components

### 1 · `engine/tools/gate_metrics.py` (aios — new, stdlib-only, fact-free)

Read-only reader over `state/queue.json` terminal items (`stage` ∈ shipped/rejected/reverted), loaded
via `queue_tx` (never hand-parsed).

- **Outcome:** shipped → accepted · rejected → rejected · reverted → accepted-then-reverted.
- **Decider class** `human|auto|scheduled|unknown`: prefer the normalized `decided_by` history field
  (component 3); else classify legacy `approved_by` by documented prefix rules (case-insensitive
  `seth*` → human; `auto-ship-scheduled` → scheduled; `auto-ship` → auto; absent → unknown).
  Unknowns are counted and rendered, never dropped.
- **Recommendation agreement:** item `recommended` vs outcome — `approve`→shipped agree ·
  `approve`→rejected override · `reject`→rejected agree · `reject`→shipped override · `hold`→(either)
  tallied as its own bucket. Overrides are the A74 signal.
- **Windows:** all-time / 30d / 7d, keyed on the terminal history entry's `ts` (fallback: item's
  latest history ts; an item with no ts lands in `unknown_ts`, surfaced).
- **Ops:** `report` (JSON rollups per (kb, lane) × decider × window) and `render` (fixed-format lines
  for the brief — deterministic-render rule; the model lifts verbatim).

### 2 · `factory_standup.py` extension (env repo `Scripts/factory-gate`)

New `collect_acceptance(root, repos, window_days)` beside the existing VETO/stuck parsing and H62
`collect_spend`:

- Extract closing commit SHAs (`\b[0-9a-f]{7,40}\b`) from windowed `## Done` lines per managed
  backlog; a Done line with no SHA → `unknown` bucket (rendered, not hidden — e.g. `<see aios git>`).
- One `git -C <repo> log --grep "This reverts commit"` scan per repo (single call, parse all revert
  targets), set-match against the Done SHAs → `accepted` vs `reverted` counts per repo.
- Join with the spend block already collected → **$/accepted-result**: factory = window drain spend ÷
  surviving drain ships; gate = window pipeline-task spend (capture/sort/ingest/gate task ledgers) ÷
  surviving gate ships (count supplied by component 1's JSON if present, else omitted with a note).
- `standup.json` gains an `acceptance` block; the aios-side standup/spend renderer adds one line.

### 3 · Write-path normalization (aios `ship.py`, ~5 lines + tests)

- `ship()`: history entry gains `decided_by` derived at flip: `human_approved` → `human`;
  `approved_by == "auto-ship-scheduled"` → `scheduled`; else `auto`. Free-text `approved_by` stays
  (audit); history never rewritten.
- `reject()`: gains optional `decided_by` (gate SKILL passes `human` on Seth's walk rejections;
  review-BLOCK rejects default `auto`). One-line gate SKILL.md prose update.

## Data flow

Existing ticks only: the factory tick / nightly standup collector writes `standup.json` (now with
`acceptance`); the brief gather runs `gate_metrics.py render` + reads `standup.json`; SKILL lifts the
lines verbatim. On-demand: both tools runnable directly (`report --json`).

## Error handling

Fail-soft with loud lines: missing queue/spend/standup → "metrics unavailable" (never silent zeros);
git absent/unreadable repo → factory acceptance skips with a rendered note; malformed rows → surfaced
`unknown` buckets (no-silent-caps rule).

## Testing

TDD both legs. aios: legacy-vocabulary classifier (real observed values incl. `Seth`/`seth-manual-gate`/
`seth-brief-2026-07-08`), agreement matrix, windowing, unknown buckets, render determinism, `decided_by`
flip derivation. env: SHA extraction (incl. no-SHA lines), revert-scan matching, spend join, acceptance
block shape. Fresh-context review + differential-review on the diff before merge. **Known-red caveat:**
the aios suite currently fails on the pre-existing A72 FO-drift test — A73's gate is "no NEW failures +
all touched/new tests green, shown"; A72 is tracked separately, not absorbed.

## Acceptance (run-and-shown)

- `python engine/tools/gate_metrics.py report --queue <env>/state/queue.json` → real rollups over the
  ~1,146 historical decisions (shown).
- `python Scripts/factory-gate/factory_standup.py --json` → `acceptance` block live against the real
  repos (revert scan run; unknowns listed) (shown).
- The brief render line(s) shown; aios new/touched tests green + env factory-gate suite green (shown).

## Ecosystem check

Run live 2026-07-15 (this session). Capabilities: **C1** queue decision-outcome reader · **C2** factory
revert detection · **C3** spend-join / $-per-accepted-result · **C4** surface rendering · **C5**
decision-stamp normalization.

### Leg 1 — Anthropic-first

```
> Get-ChildItem ~\.claude\plugins\cache\claude-plugins-official -Directory
superpowers
# In-session skill inventory (system skill list): anthropic-skills:* = backlog-to-goals, brand-guidelines,
# canvas-design, consolidate-memory, doc-coauthoring, docx, mcp-builder, morning, pdf, pptx, schedule,
# setup-cowork, skill-creator, web-artifacts-builder, xlsx — no metrics/observability skill.
# Native Claude Code: `claude -p --output-format json` exposes usage/total_cost_usd (H62 already
# harvests this into spend ledgers); OTel metrics export covers session cost/tokens — neither reads
# queue decision outcomes or backlog/git ship survival.
```

Result: nothing native or Anthropic-shipped covers C1/C2; the native cost surface is already our C3
substrate via H62.

### Leg 2 — Public marketplace (find-skills)

```
> npx skills find "agent metrics observability"
elastic/agent-skills@observability-llm-obs  1.8K installs
ruvnet/ruflo@observe-metrics                 622 installs
bobmatnyc/claude-mpm-skills@datadog-observability  143 installs
bobmatnyc/claude-mpm-skills@datadog           54 installs
ruvnet/claude-flow@observe-metrics            53 installs
cekura-ai/cekura-skills@cekura-onboarding     44 installs
> npx skills find "approval rate acceptance tracking"
asgard-ai-platform/skills@algo-risk-credit    40 installs
mohitagw15856/pm-claude-skills@influencer-brief 30 installs
```

Result: the hits are LLM-call/APM observability for other stacks (Elastic APM, ruflo's own swarm
metrics, Datadog) — none reads human-approval outcomes from a local queue or ship-survival from git.
Reference-only.

### Leg 3 — Own skills/tools (the richest leg)

```
> Glob **/SKILL.md (C:\Users\sethh\Documents\Claude) → 60+ results, relevant subset:
Scripts/factory-gate/factory_standup.py   # VETO/stuck Done-line parser + H62 collect_spend — C2/C3 host
Scripts/env-tasks/run-task.ps1            # writes state/task-logs/<id>/spend-<date>.json — C3 substrate
Projects/aios engine: queue_tx.py (loader — C1), brief_render.py (render + H62 spend line — C4),
  ship.py (_flip history writer — C5), context_log.py (run aggregates)
Scripts/env-health-collect + GM _tools/factory-health  # per-run FAILURE sweep — adjacent axis
  (failures, not decisions); its exit-0/errors-as-findings envelope contract is the pattern C2 follows
_tools/usage-audit                        # transcript retrospective — different axis (friction, not outcomes)
> python -c "...state/queue.json..." → total 1194; shipped 811, rejected 335; recommended on 810/811
  shipped + 333/335 rejected
# approved_by vocabulary — exact command + verbatim output (re-run 2026-07-15 after the reviewer leg
# flagged the first paste as method-less/irreproducible; the METHOD is part of the result):
> python -c "
import json
from collections import Counter
q=json.load(open(r'state/queue.json',encoding='utf-8'))['queue']
sh=[i for i in q if i.get('stage')=='shipped']
c=Counter(next((h.get('approved_by') for h in reversed(i.get('history',[])) if 'approved_by' in h), 'MISSING') for i in sh)
print(c.most_common())
"
shipped-only; most recent history entry carrying approved_by (reverse scan):
[('auto-ship-scheduled', 336), ('MISSING', 165), ('seth-manual-gate', 105), ('auto-ship', 71),
 ('seth', 69), ('seth-brief-2026-07-08', 31), ('seth-batch-hygiene', 11), ('Seth', 9),
 ('seth-brief-2026-07-07', 8), ('seth-x-to-gm-ref', 6)]
# NOTE for C1's classifier: counts are method-sensitive (reverse-scan-for-carrying-entry vs
# last-entry-only diverge). gate_metrics.py must define + test ONE documented extraction method.
```

Result: **the build is mostly reuse** — C2/C3/C4 extend `factory_standup.py` + H62 ledgers + the
existing brief render pipeline; C1's data already persists in full; C5 is a 5-line addition to the
existing `_flip` writer. The only genuinely new code is the `gate_metrics.py` reader.

### Leg 4 — Full-service replacement

```
Wired MCP connectors (references/platforms.md): Notion, Drive, Gmail, Calendar, Slack, Granola,
Replit, Supabase, Moodtrip → none is an observability platform.
Platform registry: Composio/Pipedream/Zapier/Make/n8n → integration gateways, not metrics stores.
> WebSearch "LLM observability platform human review approval rate metrics annotation queue …"
→ category = Langfuse (self-hosted, annotation queues "basic"), Braintrust (human review inside
eval/CI workflows), Arize Phoenix (local-first traces), LangSmith, Helicone
(braintrust.dev/articles/best-human-in-the-loop-llm-evaluation-platforms-2026;
confident-ai.com/knowledge-base/compare/top-7-llm-observability-tools; latitude.so comparisons)
```

Result: these platforms instrument **LLM API calls + eval scores**; none reads terminal states off a
local file queue or ship-survival from git history. Adoption would add an SDK to a stdlib-only
fact-free engine, put Paper-Governs-adjacent decision data in an external store, and still require
custom glue to map queue.json/backlog records into their trace model — a heavier dependency than the
~200-line reader it replaces. Partial-service (they'd cover LLM-call tracing we don't need), not a
replacement.

### Verdict table

| Capability | Verdict | Source / why |
|---|---|---|
| C1 queue outcome reader | build-because-none | data persists locally; no skill/service reads it; ~small stdlib reader |
| C2 factory revert detection | adapt-skill | extend our own `factory_standup.py` (Done-line parser already exists); git grep join is new glue |
| C3 spend-join $/accepted | adapt-skill | H62 `collect_spend` + task ledgers already collect the $ side; join is new glue |
| C4 surface rendering | adapt-skill | existing brief/standup render pipeline (H62 spend line precedent); one line each |
| C5 decided_by normalization | adapt-skill | 5 lines in our own `ship.py` `_flip` writer |

Reviewer leg: fresh-context anti-fabrication subagent run on this section (see checklist step 4);
verdict recorded in the spec commit.
