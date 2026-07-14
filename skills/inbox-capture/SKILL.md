---
name: inbox-capture
description: Stage 1 — pull raw inputs from every enabled source into the raw inbox and enqueue them as `captured` items; per-source adapters, dedupe-fenced, fact-free.
---

**§0 Resolve the install first:** `${CLAUDE_PLUGIN_ROOT}/engine/pipeline/RESOLVE-INSTALL.md` — markers first (walk up for `state/`+`profile/`), config second; only when BOTH fail is the install absent, and even then STOP with guidance, never auto-`/aios:setup`.

You are Stage 1 of the pipeline (`${CLAUDE_PLUGIN_ROOT}/engine/pipeline/PIPELINE.md`). Pull new items from each enabled
source into the raw inbox and write a `captured` queue item per the contract
(`${CLAUDE_PLUGIN_ROOT}/engine/pipeline/QUEUE.md`). Zero judgment here — capture is content-flagging + rich extraction,
not sorting.

# Sources (enabled per `profile/connectors.yaml`)

**Build status (honest — do not read this list as shipped adapters).** Capture today is ONE
source-agnostic globber (`capture.py`, `reuse` mode) over `<vault>/00_Inbox/auto/{source}/` plus the
`sessions` source and the session-run MCP `email`/`drive` pull — that is the whole built surface. The
per-source **adapter fleet below is DESIGN INTENT, not code**: there is no `x`/`github`/`youtube`/
`whatsapp` adapter tool in `engine/tools/`. In practice a user's existing automations drop raw files
into the intake dirs and the globber enqueues them (that is what `reuse` mode is); adding a genuinely
new pull-source means WRITING that adapter, not just enabling a connector.

- **email / drive** — inbound mail + attachments + owned documents; money/legal-event flagging. *(built as a session-run MCP pull — see `aios-inbox-capture` manual task; scheduling needs authenticated MCP.)*
- **x** · **bookmarks** · **github** · **youtube** · **whatsapp** — *(DESIGN INTENT — no adapter built; a `reuse`-mode install ingests these only if some other automation already drops their raws into the intake dir.)*
- **sessions** — the `session-capture` stage's synthesized work-session **records** in
  `<vault>/{kb}/raw/sessions/`. Discover them with `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/session_synth.py" records <dir>` (it
  filters to `type: session-record`, so the cheap evidence files `sess-`/`intents-`/`activity-` are
  never enqueued). Enqueue each like any raw — `source: session`, `kb` from the path, `conflict_key:
  null`. The record already carries its own `conflict_key` (`{kb}/wiki/journal/<date>.md`) + `domain`;
  **Sort honors that carried key (pass-through)** rather than re-deriving it. This is what makes a
  session record "ride the normal pipeline" — it is a raw source like any other from here on.

Add a source = add an adapter + a connector; the rest of the pipeline is unchanged.

# Install modes (reuse before re-scrape — `pipeline.capture_mode` in profile)

- **`reuse`** — the install ALREADY runs a capture pipeline (the owner's existing mail/clipper
  automations already fill `<vault>/00_Inbox/auto/{source}/`). Do NOT re-scrape — just
  **enqueue** new raws from that existing inbox into the queue (steps 1, 3, 4; skip step 2,
  extraction already happened). Leanest path; reuses proven capture. The enqueue is fully
  deterministic, so it lives in a tested, fact-free, stdlib-only tool — **`"${CLAUDE_PLUGIN_ROOT}/engine/tools/capture.py"`**
  (`scan`/`run`: id + URL fences, item build, `queue_tx.py add`, append-only ledger, VERIFY). A
  reuse-mode scheduled task is a thin wrapper that resolves the profile paths and invokes it (see
  the scheduled capture stage as the reference instance; tests in `${CLAUDE_PLUGIN_ROOT}/engine/tools/tests/test_capture.py`).
  Capture has zero judgment — keep it in the tool, never re-derive the walk/fence/build in the prompt.

  **The scheduled reuse-mode invocation** (one call does the whole enqueue — scan inboxes + session
  dirs, id/URL dedupe fences against the ledger, build `captured` items tagged with `kb`, atomic
  enqueue via `queue_tx.py add`, ledger append-after-add, VERIFY; run it once and report what it prints):
  ```
  python "${CLAUDE_PLUGIN_ROOT}/engine/tools/capture.py" run \
    --queue   "<env_root>/state/queue.json" \
    --ledger  "<env_root>/state/captured-ids.json" \
    --vault-root "<vault>" \
    <one --kb <kb>=<folder> flag per entry in the profile's vault.live_kb_map> \
    --cap 50 \
    --context-log "<env_root>/state/context-log.jsonl"
  ```
  Non-zero exit → do NOT proceed to a bad state; report the failure and STOP (queue/ledger stay
  consistent — with items in play the ledger appends only after a successful add; a URL-dupe-only delta appends with no add, by design).
- **`native`** — **DESIGN INTENT, NOT BUILT.** The intended fresh-install mode where adapters pull
  from the sources directly (all four steps). No adapter fleet exists yet (see the build-status note
  above), so a fresh install runs `reuse` mode against whatever fills its intake dirs. Do not present
  `native` as a working mode.

The profile selects: `pipeline.capture_mode` — today only **`reuse`** is functional; `native` is reserved.

# Run (per adapter, parallel)

1. **Dedupe fence #1 (id + URL).** Read the all-time dedupe ledger `state/captured-ids.json`
   (append-only; two parallel lists — per-source `ids` and normalized `urls`). Skip an item
   if its id is already present **OR** its normalized URL already appears from *any* source — this is
   what stops the same page captured by two adapters (e.g. a GitHub star **and** a browser bookmark
   of the same repo) from landing as two copies. Normalize before comparing: lowercase the host,
   drop the scheme, a trailing slash, `www.`, and tracking params (`utm_*`, `ref`, `fbclid`, …) — so
   `https://github.com/a/b/` and `github.com/a/b` collapse to one key. **Never truncate the ledger.**
   *(URL-dedup is intake-only: a deliberate downstream cross-KB **fork** — same URL mirrored e.g.
   Personal→Dev — is created by Ingest or by hand and never re-enters Capture, so this fence does not
   govern forks.)*
2. **Extract rich.** For each new item, write Web-Clipper-quality markdown to
   `<vault>/raw/inbox/{source}/` (full content, not a bare link) with capture frontmatter
   (`source`, `captured_utc`, `url`).
3. **Enqueue.** Write a `captured` queue item: `stage:captured`, `source`, `payload_path` = the raw
   file **as a VAULT-RELATIVE, forward-slashed path** (e.g. `<kb-folder>/raw/inbox/gmail/x.md` — NOT an
   absolute path; readers resolve it against their own vault-root, so items stay portable across
   desktop/cloud mounts — 2026-07-01 Slice-1), `claimed_by:null`. Append the new id to the ledger's
   `ids` and its normalized URL to `urls`
   so both fences hold next run. (`queue_tx.py add`'s native id-fence is the queue-level backstop;
   the ledger is the all-time fence — queue items leave once shipped, the ledger never forgets.)
4. **VERIFY.** Re-read each written raw file + the queue; confirm counts match intake; any mismatch
   → post the ⚠ variant, never report success; self-heal the ledger if torn (fix-then-tell).

# Discipline

- Obeys the **Stage Contract** (`${CLAUDE_PLUGIN_ROOT}/engine/pipeline/STAGE-CONTRACT.md`) — fact-free · self-contained · VERIFY · atomic-write · self-heal · re-sync-on-edit.
- Stage-specific: capture writes raw + queue only; it never routes or drafts (that's Sort / Ingest). Cadence: overnight (~00:00).
