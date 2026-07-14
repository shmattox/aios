---
type: spec
project: aios
item: A57
date: 2026-07-10
status: approved-design
title: Capture DEPTH — fetch full source content at ingest, not just the headline
tags: [aios, capture, inbox-capture, extract-rich, markitdown, spec]
---

# A57 — Capture DEPTH

> **"Fetch full source content at ingest, not just the headline."**
> Implement the `inbox-capture` step-2 **"Extract rich"** contract we already specified but never
> built — one shared extractor + thin per-lane wire-ups, every one fail-soft.

## Problem

The automated capture lanes land **headline/title/link-only** stubs: the X-bookmark scraper grabs
`div[data-testid="tweetText"]` only (an X long-form Article = the headline card; a media/quote post
= empty), Chrome-bookmark stubs are title+URL, and YouTube playlists/Liked aren't pulled at all. The
value of a *smart operational idea* is then locked in the unfetched source, and the stub reads as
retire-noise. **A56 evidence (2026-07-10 backfill):** of 217 retired stubs, **42 were concept-thin**
— genuine operational concepts lost purely because capture saved a headline and never fetched the
body. The A56 go/no-go proved the leak is at **capture**, not distill.

## This is implementing an existing contract, not net-new

The `inbox-capture` SKILL already specifies the fix and is honest that it's unbuilt:
- Step 2 **"Extract rich"**: *"write **Web-Clipper-quality markdown, full content not a bare link**."*
- Build-status note (verbatim): *"The per-source adapter fleet … is **DESIGN INTENT, not code** …
  there is no `x`/`github`/`youtube`/`whatsapp` adapter tool in `engine/tools/`."* Capture today is a
  globber (`capture.py`, `reuse` mode) that enqueues whatever an upstream automation already dropped;
  it *skips* step 2. The `native` mode that does the extraction is marked **"DESIGN INTENT, NOT BUILT."**

So A57 = **build the `native`-mode "Extract rich" step**. Not a rebuild:
- The only lane that ever had rich extraction (the retired **github-stars scraper**) died with the
  deleted `harness` repo (`shmattox/harness`, removed from GitHub) — **not cheaply recoverable**, so
  we implement fresh rather than port.
- **Reuse:** `markitdown` is already vendored + security-audited (H28, `_tools/dataroom-ingest/convert.py`)
  and its core call `python -m markitdown <arg>` accepts a **URL** (HTML→markdown + YouTube transcript),
  not just a local file. `url_extract` is a thin wrapper on the exact call the env already runs.

## Design principle

One **`url_extract`** helper — the engine's `inbox-capture` step-2 "Extract rich" implementation —
that each capture lane calls at **stub-creation time** (keeps `raw/` immutable: enrich *before* the
first write, never mutate a landed raw). Success → rich stub with the real body/transcript. Failure /
timeout / paywall / auth-wall → today's headline stub. **Never worse than now.**

**One principle, two mechanisms** (an unauthenticated fetch can't read x.com):
- `url_extract` (markitdown) → **open web: bookmarks + YouTube.**
- authed Playwright (the existing X scraper's session) → **X.**

## Components (MVP — 4 units, three thin)

### Unit 1 — `url_extract` (AIOS engine)
`engine/tools/url_extract.py` — `extract(url, *, timeout=20, max_bytes=…) -> {"ok","markdown","reason"}`.
Runs `python -m markitdown <url>` (the `dataroom-ingest` call pattern: `PYTHONUTF8=1`, subprocess,
UTF-8) with a hard timeout; returns `ok=False` + a reason on non-zero exit / timeout / empty / oversize.
**Pure fail-soft** — never raises to the caller; a lane treats `ok=False` as "land the headline stub."
- **Probe first (plan-then-shop de-risk, Task 0):** confirm `markitdown` really extracts (a) a plain
  article URL and (b) a YouTube transcript. If YouTube support is weak, add `youtube-transcript-api`
  as a fallback branch inside `url_extract`; if article extraction is weak, add a readability lib.
  The probe result is recorded in the plan; no dependency is assumed on faith.
- Hermetically testable: mock the subprocess to assert timeout / non-zero / empty / oversize / success
  all map to the right `{ok, reason}` — no network in the unit tests.

### Unit 2 — YouTube-playlist lane (env-ops)
`Scripts/youtube-list-capture/` — enumerate a configured playlist / Liked list via
`yt-dlp --flat-playlist --cookies-from-browser chrome <list-url>` (no API key; reuses Seth's Chrome
auth for the private Liked list) → for each new video id (dedupe-fenced against a ledger, same shape
as `x-bookmark-capture`) → `url_extract` the transcript → write a rich stub to
`01_Personal/raw/inbox/youtube/` with capture frontmatter (`source: youtube`, `url`, `captured_utc`).
Fail-soft per video; a transcript-less video lands a title+link stub.

### Unit 3 — Chrome-bookmark enrichment (env-ops)
Wire the existing `Scripts/chrome-bookmark-sync` lane so each new bookmark's URL is run through
`url_extract` before the stub is written — bookmarked articles + any bookmarked video get full
content. Fall back to today's title+URL stub on `ok=False`. Idempotent (skip already-rich stubs).

### Unit 4 — X long-form-article enrichment (env-ops)
Extend `Scripts/x-bookmark-capture/capture.py`: when a bookmark is a long-form Article (or `tweetText`
is empty/thin), open the article/status link in the **same authed Playwright context** and extract the
rendered body; fold it into the stub. Media/quote posts capture alt-text / quoted content where present.
Fail-soft to today's headline stub.

## AIOS vs env-ops split

- **AIOS engine:** `url_extract` (the "Extract rich" helper) + the adapter contract "enrich via
  `url_extract`, fall back to the headline stub." Universally viable — any install's lanes call it.
- **env-ops (Seth's instances):** the YouTube-list lane, the chrome-bookmark wiring, the X-scraper
  extension — his specific scrapers/auth. A moved item leaves a pointer.

## Guardrails

- **Fail-soft always** — a fetch failure never blocks a capture or produces an error stub; it lands
  the headline stub exactly as today.
- **Timeout** per fetch (~20s); **size cap**; **idempotent** (skip a stub already carrying rich body).
- **Dedupe** unchanged — the existing id+URL ledger fences still gate every lane; enrichment happens
  after the dedupe decision, only for genuinely-new items.
- **No new pipeline stage or scheduled task** beyond wiring the existing lanes (the YouTube lane is a
  new env-ops scraper alongside the X one, registered like it — not a pipeline change).
- **No paywall/auth heroics** — a login-walled or paywalled URL just falls back.

## Testability (honest — differs from A56)

- **Hermetic (TDD, in the build):** `url_extract`'s logic (timeout/non-zero/empty/oversize/success →
  `{ok, reason}` via a mocked subprocess); each lane's stub-construction + fail-soft fallback + dedupe
  (with `url_extract` stubbed).
- **Live-integration (supervised, Seth present — cannot be hermetic):** the markitdown/yt-dlp probe
  against real URLs; the YouTube lane against Seth's real Liked list (his Chrome cookies); the X
  extension against a real bookmarked article (his authed profile). These are run-and-observe steps,
  not pytest — flagged as such in the plan.

## Acceptance

- **Unit 1 shipped:** `python -m pytest engine/tools/tests/test_url_extract.py` green (mocked-subprocess
  timeout/non-zero/empty/oversize/success cases) — shown.
- **Probe recorded:** the markitdown URL + YouTube-transcript probe result is in the plan; any added
  fallback (yt-transcript/readability) is justified by it.
- **Each lane (2–4):** its deterministic stub-build + fail-soft fallback is unit-tested (with
  `url_extract` stubbed) — shown; then a **supervised live run** lands ≥1 genuinely-rich stub (real
  transcript / article body) and a fail-soft case falls back cleanly — shown in chat with Seth present.
- **Never-worse invariant:** with `url_extract` forced to `ok=False`, every lane produces exactly
  today's headline stub (a regression test per lane) — shown.
- Dedupe/immutability preserved: enrichment writes the rich body into the stub at creation; no landed
  `raw/` file is mutated after the fact.

## Non-goals / YAGNI

- No `native`-mode adapters for sources Seth doesn't use (`whatsapp`, `github` — the github lane died
  with harness and isn't being resurrected here).
- No new pipeline stage; no change to sort/ingest/gate.
- No OCR / heavy media transcription beyond what markitdown/yt-dlp give for free.
- No paywalled-content circumvention.

## Risks / open questions (resolve in planning)

- **markitdown URL/YouTube support unknown until probed** — Task 0 gates the rest; if both are weak,
  the fallback libs (yt-transcript-api, a readability extractor) are the plan-B, still fail-soft.
- **yt-dlp cookies-from-browser** may need Chrome closed / a profile path; the Liked list is private
  so auth is required — verify in the supervised run.
- **X markup fragility** — opening the article link in-session depends on X's DOM; keep the extension
  narrow and fail-soft so a markup change degrades to today's behavior, never an error.
