# A57 — Capture Depth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Fetch full source content at capture time (article body / YouTube transcript / X article) instead of landing headline-only stubs — implementing the `inbox-capture` step-2 "Extract rich" contract.

**Architecture:** One shared `url_extract` helper (markitdown URL wrapper, fail-soft) that the open-web lanes call at stub-creation time; X uses its existing authed Playwright session. Two AIOS-engine units (`url_extract`, `chrome_stub` enrichment) are hermetically TDD'd; two env-ops lanes (YouTube-playlist, X extension) have deterministic cores TDD'd + live legs verified under supervision.

**Tech Stack:** Python 3 stdlib in-process; `markitdown` via subprocess (already vendored — `_tools/dataroom-ingest/convert.py` pattern); `yt-dlp` (env-ops YouTube lane); Playwright (existing X lane). pytest (hermetic tests only).

## Global Constraints

- **Fail-soft ALWAYS** — no fetch failure ever raises to a lane, blocks a capture, or writes an error stub. On any failure the lane lands **exactly today's headline stub** (the "never-worse invariant" — a regression test per touched lane).
- **`url_extract` never raises** — it returns `{"ok": bool, "markdown": str, "reason": str}` for every input.
- **Stdlib only in-process** for engine tools; `markitdown`/`yt-dlp` run as subprocesses (not imports), matching `convert.py`.
- **`raw/` immutable** — enrich BEFORE the stub's first write; never mutate a landed raw file.
- **Open web only through `url_extract`** — x.com/auth-walled URLs are NOT fetched by markitdown (login wall); X is handled solely by its authed Playwright lane.
- **Timeout** per fetch = **20s**; **max_bytes** = **5_000_000**; **idempotent** (never re-fetch a stub already carrying rich body).
- **Dedupe unchanged** — the existing id+URL ledger fences still gate every lane; enrichment runs only for genuinely-new items, after the dedupe decision.
- **Live-integration legs are NOT pytest** — they are supervised run-and-observe steps (network + Seth's auth). Only the deterministic logic is unit-tested.
- **TLS middlebox (Task-0 finding):** every real markitdown/network fetch MUST inject `truststore` first (this env's AV middlebox breaks Python 3.14 strict OpenSSL — the H44 issue). `truststore` is an installed machine dep; the fetch path is best-effort about it (works as a no-op where there's no middlebox).

---

### Task 0: Probe markitdown URL + YouTube support (gates the fallback decision)

**Files:** none (a documented probe; record the result in this plan's Task-0 checkbox notes).

- [ ] **Step 1: Confirm markitdown is installed / installable**

Run: `python -m markitdown --help`
Expected: usage text. If ModuleNotFoundError: `pip install "markitdown[all]"` (dataroom-ingest already requires `markitdown[pdf,docx,xlsx,pptx]`; `[all]` adds the URL/YouTube extras). Record which extras were needed.

- [ ] **Step 2: Probe a plain article URL**

Run: `python -m markitdown https://example.com`
Expected: markdown text of the page (non-empty). Record: did it return real content?

- [ ] **Step 3: Probe a YouTube transcript**

Run: `python -m markitdown "https://www.youtube.com/watch?v=dQw4w9WgXcQ"`
Expected: markdown including the video transcript/description. Record: transcript present, or only metadata?

- [ ] **Step 4: Decide fallbacks (record in plan)**

**PROBE RESULT (run 2026-07-10, recorded):** markitdown IS installed and DOES accept a URL argument (it attempts an HTTP fetch). BUT a bare `python -m markitdown <url>` **fails with an SSL cert error** — this env is behind a TLS-inspection middlebox and Python 3.14's strict OpenSSL rejects the injected CA (the exact H44 Trello issue). **FIX (verified live):** inject `truststore` before the fetch — `python -c "import truststore; truststore.inject_into_ssl(); from markitdown import MarkItDown; print(MarkItDown().convert(url).text_content)"` returns real markdown. `truststore` is already installed (H44 machine dep). **Decision:** Task 1's `_default_run` MUST use a truststore-bootstrap subprocess (below), NOT bare `-m markitdown`. YouTube-transcript depth (Step 3) is deferred to the supervised Task 3.6 live run (needs a real video + cookies); if markitdown's YT transcript is weak there, the YouTube lane uses `yt-dlp --write-auto-sub --skip-download`. No dependency assumed on faith — the SSL blocker is real and now handled.

- [ ] **Step 5: Commit the probe record**

```bash
git add docs/superpowers/plans/2026-07-10-a57-capture-depth.md
git commit -m "A57 Task 0: markitdown URL+YouTube probe result recorded"
```

---

### Task 1: `url_extract` — the "Extract rich" helper (AIOS)

**Files:**
- Create: `engine/tools/url_extract.py`
- Test: `engine/tools/tests/test_url_extract.py`

**Interfaces:**
- Produces: `url_extract.extract(url, timeout=20, max_bytes=5_000_000, _run=None) -> {"ok","markdown","reason"}`. Consumed by Task 2 (`chrome_stub`) and Task 3 (YouTube lane). `_run` is the test seam (defaults to the real markitdown subprocess).

- [ ] **Step 1: Write the failing tests**

Create `engine/tools/tests/test_url_extract.py`:

```python
#!/usr/bin/env python3
"""Hermetic tests for url_extract — the subprocess is mocked; NO network."""
import os, sys, subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import url_extract as ux

class _R:                      # a fake CompletedProcess
    def __init__(self, returncode, stdout): self.returncode, self.stdout = returncode, stdout

def _run_ok(url, timeout):     return _R(0, "# Title\n\nreal body content\n")
def _run_empty(url, timeout):  return _R(0, "   \n")
def _run_fail(url, timeout):   return _R(1, "")
def _run_timeout(url, timeout): raise subprocess.TimeoutExpired(cmd="markitdown", timeout=timeout)
def _run_boom(url, timeout):   raise OSError("spawn failed")

def test_success_returns_ok_and_markdown():
    r = ux.extract("http://x", _run=_run_ok)
    assert r["ok"] is True and "real body content" in r["markdown"] and r["reason"] == "ok"

def test_empty_output_is_not_ok():
    r = ux.extract("http://x", _run=_run_empty)
    assert r["ok"] is False and r["reason"] == "empty" and r["markdown"] == ""

def test_nonzero_exit_is_not_ok():
    r = ux.extract("http://x", _run=_run_fail)
    assert r["ok"] is False and r["reason"].startswith("exit")

def test_timeout_is_not_ok_and_never_raises():
    r = ux.extract("http://x", timeout=5, _run=_run_timeout)
    assert r["ok"] is False and "timeout" in r["reason"]

def test_arbitrary_exception_is_swallowed():
    r = ux.extract("http://x", _run=_run_boom)
    assert r["ok"] is False and r["reason"].startswith("error:")

def test_oversize_output_is_not_ok():
    big = "x" * 20
    r = ux.extract("http://x", max_bytes=5, _run=lambda u, t: _R(0, big))
    assert r["ok"] is False and r["reason"] == "oversize"
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python -m pytest engine/tools/tests/test_url_extract.py -q`
Expected: FAIL (ModuleNotFoundError: url_extract).

- [ ] **Step 3: Implement `url_extract.py`**

Create `engine/tools/url_extract.py`:

```python
#!/usr/bin/env python3
"""url_extract.py — the inbox-capture step-2 "Extract rich" helper (A57).

Fetch a URL's full content as markdown via markitdown (the dataroom-ingest subprocess pattern) and
return it FAIL-SOFT: this function NEVER raises — it returns {"ok","markdown","reason"} for every
input, so a caller treats ok=False as "land the headline stub" and is never worse off than before.

Open web only (articles + YouTube transcripts). Auth-walled sources (x.com) return ok=False here —
they are handled by their own authed lanes, never by markitdown. Stdlib only in-process; markitdown
runs as a subprocess exactly as _tools/dataroom-ingest/convert.py does.
"""
import os, subprocess, sys

DEFAULT_TIMEOUT = 20
DEFAULT_MAX_BYTES = 5_000_000


# Truststore-bootstrap subprocess (Task-0 probe finding): a bare `python -m markitdown <url>` dies
# with an SSL cert error behind this env's TLS-inspection middlebox (the H44 issue). Inject
# truststore first so Python trusts the OS cert store, THEN run markitdown. truststore-missing or
# markitdown-missing -> the subprocess raises -> non-zero exit -> extract() returns ok=False
# (fail-soft), never worse than today. The `try/except pass` means truststore is best-effort: on a
# machine with no middlebox it's a harmless no-op.
_BOOTSTRAP = (
    "import sys\n"
    "try:\n"
    "    import truststore; truststore.inject_into_ssl()\n"
    "except Exception:\n"
    "    pass\n"
    "from markitdown import MarkItDown\n"
    "sys.stdout.reconfigure(encoding='utf-8')\n"
    "print(MarkItDown().convert(sys.argv[1]).text_content)\n"
)


def _default_run(url, timeout):
    """Real markitdown fetch via a truststore-bootstrap subprocess (see _BOOTSTRAP). PYTHONUTF8=1 so
    em-dashes don't become U+FFFD on Windows. Returns a CompletedProcess (returncode + stdout)."""
    env = dict(os.environ, PYTHONUTF8="1")
    return subprocess.run([sys.executable, "-c", _BOOTSTRAP, url],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=timeout)


def extract(url, timeout=DEFAULT_TIMEOUT, max_bytes=DEFAULT_MAX_BYTES, _run=None):
    """Fetch `url` -> {"ok": bool, "markdown": str, "reason": str}. NEVER raises. `_run` is the test
    seam (defaults to the real markitdown subprocess)."""
    run = _run or _default_run
    try:
        r = run(url, timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "markdown": "", "reason": f"timeout>{timeout}s"}
    except Exception as e:
        return {"ok": False, "markdown": "", "reason": f"error:{type(e).__name__}"}
    if getattr(r, "returncode", 1) != 0:
        return {"ok": False, "markdown": "", "reason": f"exit{r.returncode}"}
    md = getattr(r, "stdout", "") or ""
    if not md.strip():
        return {"ok": False, "markdown": "", "reason": "empty"}
    if len(md.encode("utf-8", "replace")) > max_bytes:
        return {"ok": False, "markdown": "", "reason": "oversize"}
    return {"ok": True, "markdown": md, "reason": "ok"}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: url_extract.py <url>"); sys.exit(1)
    res = extract(sys.argv[1])
    if res["ok"]:
        print(res["markdown"])
    else:
        print(f"(no rich extract: {res['reason']})"); sys.exit(2)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python -m pytest engine/tools/tests/test_url_extract.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add engine/tools/url_extract.py engine/tools/tests/test_url_extract.py
git commit -m "A57 Task 1: url_extract — fail-soft markitdown URL 'Extract rich' helper"
```

---

### Task 2: `chrome_stub` enrichment (AIOS)

**Files:**
- Modify: `engine/tools/capture_router.py` (`chrome_stub`, line ~237; and its caller — pass the enricher)
- Test: `engine/tools/tests/test_capture_router.py` (add cases)

**Interfaces:**
- Consumes: `url_extract.extract` (Task 1).
- Produces: `chrome_stub(rec, kb, rule, now_iso, enrich=None)` — when `enrich` is a callable, the stub body carries the fetched markdown on `ok`, else today's body verbatim. `enrich=None` = today's behavior exactly (default).

- [ ] **Step 1: Write the failing tests**

Add to `engine/tools/tests/test_capture_router.py` (match its existing import/style):

```python
def _rec():
    return {"host": "example.com", "url": "https://example.com/a", "date_added": "2026-07-10",
            "title": "A Thing", "folder_path": "General Mgmt", "guid": "g1"}

def test_chrome_stub_unenriched_is_todays_shape():
    import capture_router as cr
    s = cr.chrome_stub(_rec(), "gm", "default", "2026-07-10T00:00:00Z")   # no enrich
    assert "open the source for full content" in s        # legacy body preserved
    assert "url: https://example.com/a" in s

def test_chrome_stub_enriched_folds_in_fetched_markdown():
    import capture_router as cr
    enrich = lambda url: {"ok": True, "markdown": "## Real Heading\n\nfull article body", "reason": "ok"}
    s = cr.chrome_stub(_rec(), "gm", "default", "2026-07-10T00:00:00Z", enrich=enrich)
    assert "full article body" in s                       # rich content folded in
    assert "url: https://example.com/a" in s              # frontmatter intact
    assert "[Source](https://example.com/a)" in s         # source link kept

def test_chrome_stub_enrich_failsoft_is_never_worse():
    import capture_router as cr
    enrich = lambda url: {"ok": False, "markdown": "", "reason": "timeout>20s"}
    s = cr.chrome_stub(_rec(), "gm", "default", "2026-07-10T00:00:00Z", enrich=enrich)
    assert "open the source for full content" in s        # falls back to today's body exactly
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python -m pytest engine/tools/tests/test_capture_router.py -k chrome_stub -v`
Expected: the two new enrich tests FAIL (chrome_stub takes no `enrich`); the unenriched test passes.

- [ ] **Step 3: Add the `enrich` parameter to `chrome_stub`**

In `engine/tools/capture_router.py`, change `chrome_stub`'s signature and body. Keep the `fm` block and the legacy `body` unchanged; add enrichment before returning:

```python
def chrome_stub(rec, kb, rule, now_iso, enrich=None):
    """... (existing docstring) ... A57: when `enrich` (a url_extract-shaped callable) is supplied,
    a successful fetch replaces the placeholder body with the real content; a failed/absent fetch
    keeps today's stub exactly (never-worse). enrich=None preserves legacy behavior."""
    host, url, date = rec["host"], rec["url"], rec["date_added"]
    title = rec["title"].replace(chr(34), chr(39)).replace("\n", " ").replace("\r", " ")
    fp = rec["folder_path"].replace("\n", " ").replace("\r", " ")
    fm = ("---\nsource: chrome\n"
          f"captured_utc: {now_iso}\nurl: {url}\n"
          f"bookmark_id: {rec['guid']}\n"
          f'title: "{title}"\nauthor: "{host}"\n'
          f'folder_path: "{fp}"\ndate_added: {date}\n'
          f"routed: true\nrouted_to_kb: {kb}\nrouted_at_utc: {now_iso}\nrouted_by_rule: {rule}\n---\n\n")
    legacy_body = (f"# {title}\n\n{host} · bookmarked {date}\n\n"
                   f"Bookmarked link to **{host}**. Saved {date} in Chrome folder `{fp}`. This is a "
                   f"tool/app/reference bookmark captured as a stub; open the source for full content.\n\n"
                   f"[Source]({url})\n")
    if enrich is not None:
        res = enrich(url)
        if res.get("ok") and res.get("markdown", "").strip():
            body = (f"# {title}\n\n{host} · bookmarked {date} · Chrome folder `{fp}`\n\n"
                    f"{res['markdown'].strip()}\n\n[Source]({url})\n")
            return fm + body
    return fm + legacy_body
```

- [ ] **Step 4: Wire the router's caller to pass the enricher**

Find where `chrome_stub(` is called (in `_chrome_candidates` / the routing pass). Add at the top of `capture_router.py` imports: `import url_extract` (sibling; sys.path already includes the tools dir). At the call site, pass `enrich=url_extract.extract`. If the call site is inside a function that a test drives without network, thread an `enrich` param down defaulting to `url_extract.extract` so tests can override with a stub. Show the exact edited call line in the commit.

- [ ] **Step 5: Run tests, verify pass + no regression**

Run: `python -m pytest engine/tools/tests/test_capture_router.py -q`
Expected: PASS (existing + 3 new).

- [ ] **Step 6: Commit**

```bash
git add engine/tools/capture_router.py engine/tools/tests/test_capture_router.py
git commit -m "A57 Task 2: chrome bookmark stubs enrich via url_extract (fail-soft)"
```

---

### Task 3: YouTube-playlist lane (env-ops)

**Files:**
- Create: `Scripts/youtube-list-capture/capture.py`, `Scripts/youtube-list-capture/README.md`
- Test: `Scripts/youtube-list-capture/test_capture.py`

**Interfaces:**
- Consumes: `yt-dlp` (subprocess, enumerate); `url_extract.extract` (transcript — imported from the aios engine tools, or a local copy of the same subprocess call to keep the script self-contained: **copy the call, cite A57** — Scripts/ is env-ops and must not import from a sibling repo path). Mirrors `Scripts/x-bookmark-capture/capture.py` shape (ledger, `yaml_safe`, `build_filename`, fail-loud exit codes).

- [ ] **Step 1: Write the failing tests (deterministic core only)**

Create `Scripts/youtube-list-capture/test_capture.py` testing the pure helpers (NOT the live enumeration/transcript):

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture as c

def test_build_stub_with_transcript_folds_body():
    stub = c.build_stub("2026-07-10", "vid123", "How to X", "https://youtu.be/vid123",
                        transcript="the full transcript text")
    assert "the full transcript text" in stub
    assert "source: youtube" in stub and "url: https://youtu.be/vid123" in stub

def test_build_stub_without_transcript_is_headline_failsoft():
    stub = c.build_stub("2026-07-10", "vid123", "How to X", "https://youtu.be/vid123", transcript="")
    assert "How to X" in stub and "https://youtu.be/vid123" in stub
    assert "transcript" not in stub.lower() or "no transcript" in stub.lower()   # never-worse: title+link stub

def test_new_ids_filters_seen_ledger():
    assert c.new_ids(["a", "b", "c"], seen={"b"}) == ["a", "c"]
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python Scripts/youtube-list-capture/test_capture.py`
Expected: FAIL (module/functions absent).

- [ ] **Step 3: Implement the deterministic core + live enumeration**

Create `Scripts/youtube-list-capture/capture.py`. Include: `load_ledger`/`save_ledger` (copy the x-lane pattern), `new_ids(ids, seen)`, `yaml_safe`, `build_filename`, and `build_stub(date, vid, title, url, transcript)` (fail-soft: transcript present → fold it in; empty → title+link stub). Live pieces (not unit-tested): `enumerate_list(list_url)` shelling `yt-dlp --flat-playlist --print id --print title --cookies-from-browser chrome <list_url>`, and `fetch_transcript(url)` calling the same markitdown subprocess as `url_extract` (copy the call; on failure return ""). `main()` mirrors the x-lane: enumerate → `new_ids` vs ledger → per video fetch transcript + `build_stub` → write to `01_Personal/raw/inbox/youtube/` → save ledger; fail-loud exit codes (0 ran, 2 auth/cookies, 1 other). Write the exact `build_stub` so the tests pass:

```python
def build_stub(date, vid, title, url, transcript):
    safe = yaml_safe(title)
    fm = ("---\nsource: youtube\nkb: personal\n"
          f"captured_utc: {date}T00:00:00Z\n"
          f'url: {url}\nvideo_id: {vid}\n'
          f'title: "{safe}"\nsource_tier: tertiary\nrouted: true\n---\n\n'
          f"# {safe}\n\n[Watch]({url})\n\n")
    if transcript.strip():
        return fm + "## Transcript\n\n" + transcript.strip() + "\n"
    return fm + "_(no transcript available — open the link)_\n"
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python Scripts/youtube-list-capture/test_capture.py`
Expected: PASS (3).

- [ ] **Step 5: Commit (deterministic core)**

```bash
git add Scripts/youtube-list-capture/
git commit -m "A57 Task 3: YouTube-playlist capture lane (deterministic core + yt-dlp enum)"
```

- [ ] **Step 6: [SUPERVISED — Seth present] live run against the real Liked list**

Run: `python Scripts/youtube-list-capture/capture.py --list "<Seth's Liked/playlist URL>" --max-new 3 --dry-run`
Expected: enumerates 3 videos, fetches ≥1 real transcript, prints the rich stub. Resolve `--cookies-from-browser` auth here (Chrome may need to be closed). If yt-dlp transcript works better than markitdown for YouTube (per Task 0), use it. Record the outcome; only then wire the scheduled task (env-ops, a follow-up like the X lane's registration).

---

### Task 4: X long-form-article enrichment (env-ops)

**Files:**
- Modify: `Scripts/x-bookmark-capture/capture.py` (the `scrape` loop + stub build in `main`)
- Test: `Scripts/x-bookmark-capture/test_filename.py` (add a stub-fold case) or a new `test_enrich.py`

**Interfaces:**
- Consumes: the existing authed Playwright `ctx`. Produces: a stub carrying the article body when a bookmark is a long-form Article; today's headline stub otherwise (never-worse).

- [ ] **Step 1: Write the failing test (deterministic fold only)**

Add to a test in `Scripts/x-bookmark-capture/`:

```python
import capture as c
def test_stub_body_uses_article_when_present():
    body = c.compose_body("headline text", article="full article body text")
    assert "full article body text" in body
def test_stub_body_failsoft_to_headline():
    body = c.compose_body("headline text", article="")
    assert "headline text" in body and "full article" not in body
```

- [ ] **Step 2: Run, verify fail**

Run: `python -m pytest Scripts/x-bookmark-capture/ -k stub_body -q` (or run the test file directly)
Expected: FAIL (`compose_body` absent).

- [ ] **Step 3: Extract `compose_body` + add in-session article follow**

Refactor the stub text-building in `main()` into `compose_body(text, article="")` (article present → append `\n\n## Article\n\n{article}`; else just the headline text as today). In `scrape()`, when an item's `tweetText` is empty OR the tweet card is a long-form Article (detect the article link `a[href*="/i/article/"]` or an empty `tweetText` with an outbound status link), open that link in the SAME `ctx` (new page, `wait_for_selector` on the article body with a short timeout), read `inner_text`, store as `item["article"]`; on any timeout/exception set `item["article"]=""` (fail-soft). Thread `article` into `compose_body`. Keep `FNAME_MAX`/`build_filename` unchanged.

- [ ] **Step 4: Run tests, verify pass**

Run: `python Scripts/x-bookmark-capture/test_filename.py` and the new stub-body test.
Expected: PASS (existing filename tests + 2 new).

- [ ] **Step 5: Commit (deterministic core)**

```bash
git add Scripts/x-bookmark-capture/
git commit -m "A57 Task 4: X capture folds long-form article body via authed session (fail-soft)"
```

- [ ] **Step 6: [SUPERVISED — Seth present] live run against a real bookmarked article**

Run: `python Scripts/x-bookmark-capture/capture.py --headed --max-new 3 --dry-run`
Expected: for a bookmarked long-form Article, the stub now carries the article body; a normal tweet is unchanged; a media/quote post falls back cleanly. Uses Seth's `claude-capture` X profile (his auth). Record the outcome.

---

### Task 5: Full suite green + supervised acceptance

- [ ] **Step 1: Full engine suite**

Run: `python -m pytest engine/tools/tests/ -q`
Expected: all green (pre-existing + `test_url_extract` + new `capture_router` cases). If any pre-existing test broke, STOP and fix.

- [ ] **Step 2: Env-ops lane tests**

Run: `python Scripts/youtube-list-capture/test_capture.py` and `python Scripts/x-bookmark-capture/test_filename.py`
Expected: green.

- [ ] **Step 3: [SUPERVISED] Acceptance summary in chat**

With Seth present, show: (a) `url_extract` on a real article + a YouTube URL returning rich markdown; (b) one enriched chrome stub, one YouTube stub with a real transcript, one X stub with an article body; (c) each lane's fail-soft fallback landing today's headline stub. State that scheduled-task registration for the YouTube lane is an env-ops follow-up (like the X lane), not part of this plan.

---

## Self-Review

**Spec coverage:** `url_extract` "Extract rich" helper → Task 1 ✅ · chrome enrichment → Task 2 ✅ · YouTube-playlist lane → Task 3 ✅ · X article-follow → Task 4 ✅ · probe/plan-then-shop → Task 0 ✅ · fail-soft never-worse invariant → regression tests in Tasks 2/3/4 ✅ · AIOS/env-ops split → Tasks 1-2 (AIOS) vs 3-4 (env-ops) ✅ · supervised live legs → Tasks 3.6/4.6/5.3 ✅.

**Placeholder scan:** concrete values throughout (timeout 20, max_bytes 5_000_000). The Liked-list URL and Task-0 probe outcomes are legitimately filled in at run time (supervised) — flagged as such, not TODOs.

**Type consistency:** `extract(...) -> {"ok","markdown","reason"}` is produced in Task 1 and consumed identically in Task 2 (`enrich(url)`) and Task 3 (`fetch_transcript`). `chrome_stub(..., enrich=None)` signature matches its tests. `compose_body(text, article="")` matches its tests.

**Scope note:** one subsystem (capture depth); Tasks 1-2 hermetic, 3-4 deterministic-core + supervised live. Scheduled-task registration deliberately deferred (env-ops follow-up).
