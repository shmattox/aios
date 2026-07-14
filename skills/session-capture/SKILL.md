---
name: session-capture
description: Autonomic stage — mine each work session's evidence into a domain-tagged record in raw/sessions/, mark evidence synthesized; the record rides the normal pipeline.
---

**§0 Resolve the install first:** `${CLAUDE_PLUGIN_ROOT}/engine/pipeline/RESOLVE-INSTALL.md` — markers first (walk up for `state/`+`profile/`), config second; only when BOTH fail is the install absent, and even then STOP with guidance, never auto-`/aios:setup`.

You are the **session-capture** stage (design history lives in the engine's source repo). You turn the cheap
mechanical *evidence* a work session leaves behind into one real, domain-tagged **session record** —
the Focus / Outcome / **Why** the raw trace never captured. **Source A (Claude Code hook evidence) only as of H22a Slice-3 (2026-07-01)** —
the Source-B Cowork session-store reader is **retired** (Cowork is no longer a work-capture surface); this
stage runs as a native desktop `claude -p` task.
Writing the record is **autonomic**
(recording what happened is not a judgment, same stance as the old `dev-session-capture`): it lands
in `raw/sessions/` ungated, then **rides the normal pipeline** — `capture`'s `sessions` adapter
enqueues the record (`python "${CLAUDE_PLUGIN_ROOT}/engine/tools/session_synth.py" records <dir>` discovers it, filtering out the
evidence files), `sort` honors its carried `kb`/`conflict_key`/`domain` (pass-through), `ingest`
drafts the wiki/journal updates it implies into staging, and the `gate` reviews + ships those. So the
operational record is ungated; the *knowledge* distilled from it rides the same human gate as every
other ingest.

> **Wiring status (G16b, complete):** synthesis (this skill) → `inbox-capture` `sessions` adapter →
> `sort` pass-through → `ingest` → `gate` are all wired. The synthesis + evidence-release +
> garden-sweep (G16c) loop is tested (`${CLAUDE_PLUGIN_ROOT}/engine/tools/tests/test_session_capture.py`). Live at the G12 cutover.

You do the **narrative** (judgment); the deterministic scaffolding is `"${CLAUDE_PLUGIN_ROOT}/engine/tools/session_synth.py"`.

# Inputs (all profile facts — this skill is fact-free)
- `profile: session_capture.evidence_dir` — **Source A only**: where the Layer-1 hook collector deposits
  `sess-<id>.md` / `intents-<id>.md` / `activity-<date>.md`. This is the only evidence source now.
- `profile: session_capture.seen_ledger`, `session_capture.automation_titles` — **RETIRED (Slice-3):**
  these fenced the Source-B (Cowork) reader, which is removed. Left in the profile only as tombstones.
- `profile: session_capture.domain_map` — `project → kb` (with `_default`). **THE mis-homing fix**:
  a `Projects/family-office` session maps to `familyoffice`, not dumped into `dev`.
- `profile: session_capture.ttl_days` — handed to garden (G16c), not used here.
- `profile: vault` + `vault.live_kb_map` — where the record is written: the real vault at `<vault>/<vault.live_kb_map[kb]>`. A `kb` NOT in `vault.live_kb_map` is an error — hold + flag, never a fallback vault.
# Run
1. **Find work — Source A hook evidence** (Claude Code; the hook fires per session):
   `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/session_synth.py" scan <evidence_dir> --stale-hours <session_capture.stale_hours>`
   → the bundles ready to synthesize: sessions ENDED (or crashed-but-stale past `stale_hours`) and not
   yet `synthesized`. Live sessions are skipped and caught next run (dedupe-fenced on the `synthesized`
   flag). **EMPTY bundles (no intents/files/tools — aborted/instant sessions) and MACHINE runs
   (`machine_run: true` — the fleet's own headless sessions, stamped by the evidence hook when the
   deploy runner exports `AIOS_MACHINE_RUN`; A16) are excluded from this work list by `scan` and
   released separately in step 5 (`prune-empty`); never synthesize a stub or a fleet self-record.**
   Each carries `source: claude-code`. *(Source B — the hookless Cowork session-store reader — is
   retired as of Slice-3; there is no second source.)*
2. **Mine each bundle into a record.** Read the session's `transcript_path` (this is the signal that
   was captured but never read) + the `intents` (the user's verbatim prompts = the Why) + the
   tool/file counts. **Correlate commits**: `git log` per touched repo over the session's time window,
   matching `files` — record the real `<hash> <subject>` (heuristic; good enough for a narrative).
   Derive `kb` by looking up `project` in `domain_map` (fall back to `_default`).
3. **Write the record** to `<vault>/<vault.live_kb_map[kb]>/raw/sessions/<source>-<date>-<id8>.md` with the
   schema (design history lives in the engine's source repo): `type: session-record`, `source`, `id`, `domain`, `project`,
   started/ended, `intents`, `files`, `commits`, `tool_counts`, `tools_failed`, then a real
   **Focus / Outcome / Why** mined from the evidence — never a stub. `conflict_key` =
   `{kb}/wiki/journal/<date>.md` (a day's sessions in one domain serialize on that day's note; the key
   is canonical, `domain`/`kb` are derived from it — if they disagree, the key wins).
4. **VERIFY** (Stage Contract #3): re-read every record — exists, non-empty, valid frontmatter, a real
   Focus/Outcome/Why (not a stub), and `domain` matches the evidence. Atomic write (tmp → validate →
   replace). Any check fails → post the ⚠ variant; **never report success on a partial run.**
5. **Release / dedupe — per source.** **Only release what you actually wrote** — the release flag is the
   "already captured / safe to reclaim" signal; releasing without a real record loses the session.
   - **Source A:** `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/session_synth.py" mark <evidence_dir> --id <id> …` for every record
     VERIFIED in step 4 — flips its evidence `synthesized: true` (and a day's `activity-` log once all
     that day's sessions are done) so garden (G16c) can TTL-sweep it. Then
     `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/session_synth.py" prune-empty <evidence_dir> --stale-hours <session_capture.stale_hours>`
     to release no-record ready bundles for the same sweep — EMPTY bundles, fanned-out
     evaluator/judge SUB-RUNS (a canned prompt repeated across ≥2 siblings, no files/tools — e.g. the
     genome-scorer judges; ON2), **and** MACHINE runs (`machine_run: true` fleet sessions; their
     durable record is the context-log line the fleet stage itself wrote — A16). These are the ONLY
     marks-without-a-record that are correct (none has work to lose; a LONE tool-free session is NOT
     a sub-run and is recorded normally), and it is what keeps all three from re-appearing in `scan`
     every run. Log ALL returned counts — `pruned_empty`, `pruned_subruns`, `pruned_machine` — in the
     anomalies/note.
   *(Source B dedupe-ledger release is retired with Source B, Slice-3.)*
6. **Context-log** (#7): append one line to `state/context-log.jsonl` —
   `ts · stage:session-capture · run_id · {synthesized:n, by_kb:{…}} · repairs · anomalies · note`.

# Discipline
- Obeys the **Stage Contract** (`${CLAUDE_PLUGIN_ROOT}/engine/pipeline/STAGE-CONTRACT.md`) — fact-free · self-contained ·
  VERIFY · atomic-write · self-heal (torn evidence → rebuild from the source of truth, fix-then-tell) ·
  re-sync-on-edit · context-log.
- **Autonomic, not gated:** writes a raw source; it does NOT enqueue or touch the queue (`queue_tx`).
  The record's knowledge is gated *downstream* by the normal pipeline, not here.
- **Fail loud rather than fabricate.** Never write a narrative for evidence you didn't collect; a
  session still `running` is skipped, not guessed.
- Cadence: overnight (~01:30), **ahead of the cloud-ingest capture window** (so the record is pushed to
  git and in `raw/sessions/` when cloud-ingest sweeps).
