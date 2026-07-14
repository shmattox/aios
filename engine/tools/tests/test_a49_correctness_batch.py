#!/usr/bin/env python3
"""A49 — review-surfaced correctness batch: one regression per bug (all silent-until-it-bites).

  1. resolve sweep staleness surfaces loudly (a permanently-degrading source can't freeze the worklist)
  2. a cp1252 (non-UTF-8) raw ingests instead of stalling 'unreadable' forever
  3. a mixed-case cwd resolves the intended silo on a case-insensitive FS
  4. brief_render tolerates an item with no title (unvalidated live-gather path) — degrades, not crashes
  5. normalize_url dedupe is query-ORDER-insensitive
  6. _frontmatter does not mis-read a nested block key as top-level

Standalone script (run: python tools/tests/test_a49_correctness_batch.py); suite_test runs it as a subprocess.
"""
import json, os, sys, tempfile, shutil

_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _TOOLS)
import resolve_sweep_task as rst
import resolve_brief as rb
import capture
import sort as sortmod
import brief_session as bs
import brief_render as br

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ok  " if cond else " FAIL ") + name)


d = tempfile.mkdtemp(prefix="a49_")
try:
    # ── bug 1: resolve sweep staleness ───────────────────────────────────────────────────────
    cache_dir = os.path.join(d, "cache")
    _orig_cfg, _orig_gather = rst.resolve_config, rst.gather_tasks
    rst.resolve_config = lambda er: {"keywords": ["mortgage"], "cache_dir": cache_dir,
                                     "task_dbs": ["x"], "entities_dirs": []}
    try:
        rst.gather_tasks = lambda cfg, tf: ([], "error")            # source unreachable -> degraded
        r1 = rst.run("envroot", now="2026-07-09T06:30:00Z")
        r2 = rst.run("envroot", now="2026-07-10T06:30:00Z")
        check("degraded run reports + increments consecutive_degraded",
              r1["status"] == "degraded" and r1["consecutive_degraded"] == 1 and r2["consecutive_degraded"] == 2)
        st = rst._read_status(cache_dir)
        check("sidecar records the degraded streak with no last_good",
              st["consecutive_degraded"] == 2 and st.get("last_good_utc") is None)
        rst.gather_tasks = lambda cfg, tf: ([{"id": "t1", "title": "pay the mortgage"}], "notion")
        r3 = rst.run("envroot", now="2026-07-11T06:30:00Z")
        check("a reached source resets the degraded streak to 0",
              r3["status"] in ("written", "warm") and rst._read_status(cache_dir)["consecutive_degraded"] == 0)
    finally:
        rst.resolve_config, rst.gather_tasks = _orig_cfg, _orig_gather

    # the brief surfaces a degraded sweep LOUDLY even when every dossier is present (sweep complete)
    json.dump({"stage": "resolve-sweep", "content_hash": "h", "flagged": []},
              open(os.path.join(cache_dir, "sweep.json"), "w", encoding="utf-8"))
    rst._write_status(cache_dir, {"last_attempt_utc": "2026-07-12T06:30:00Z", "last_source": "error",
                                  "last_good_utc": "2026-07-08T06:30:00Z", "consecutive_degraded": 3})
    res = rb.check(os.path.join(cache_dir, "sweep.json"), cache_dir)
    check("resolve_brief.check surfaces DEGRADED loudly on a complete-but-stale sweep",
          "DEGRADED" in res["line"] and res.get("stale") is True and res["complete"] is True)
    rst._write_status(cache_dir, {"consecutive_degraded": 0})
    check("a reached last sweep is quiet (no false staleness alarm)", rb._staleness_line(cache_dir) == "")
    check("no sidecar (pre-A49 cache) is quiet", rb._staleness_line(tempfile.mkdtemp()) == "")

    # ── bug 2: cp1252 raw ingests, never stalls ──────────────────────────────────────────────
    cp = os.path.join(d, "cp1252.md")
    with open(cp, "wb") as f:
        f.write("café — señor résumé\n".encode("cp1252"))   # bytes that are INVALID utf-8
    ctxt, stxt = capture._read_text(cp), sortmod._read_text(cp)
    check("capture._read_text reads a cp1252 raw (replacement, not None/crash)", ctxt is not None and "caf" in ctxt)
    check("sort._read_text reads a cp1252 raw (replacement, not None/crash)", stxt is not None and "se" in stxt)

    # ── bug 3: mixed-case cwd resolves the intended silo ─────────────────────────────────────
    dm = {"aios": "dev"}
    canonical = os.path.join("C:", os.sep, "Users", "x", "Documents", "Claude", "Projects", "aios")
    check("canonical-case cwd resolves the silo (every platform)",
          bs.resolve_scope(canonical, dm, default_scope="all") == "dev")
    if os.path.normcase("A") != "A":   # case-INSENSITIVE FS (Windows) — the platform this bug bites
        lowered = os.path.join("c:", os.sep, "users", "x", "documents", "claude", "projects", "aios")
        check("lowercase-surfaced cwd still resolves the silo on a case-insensitive FS",
              bs.resolve_scope(lowered, dm, default_scope="all") == "dev")
    else:
        check("lowercase-surfaced cwd (skipped — case-sensitive FS, bug does not apply)", True)

    # ── bug 4: brief_render tolerates a missing title ────────────────────────────────────────
    card = br.render_card({"domain": "dev", "system_voice": {"text": "x"}, "claude_voice": {"text": "y"}})
    row = br.render_overview_row({})
    check("render_card with no title degrades to a placeholder (no KeyError)", "(untitled)" in card)
    check("render_overview_row with no title degrades to a placeholder (no KeyError)", "(untitled)" in row)

    # ── bug 5: normalize_url is query-order-insensitive ──────────────────────────────────────
    check("normalize_url collapses reordered query params to one dedupe key",
          capture.normalize_url("https://x.com/a?b=2&a=1") == capture.normalize_url("https://x.com/a?a=1&b=2"))

    # ── bug 6: _frontmatter does not mis-read a nested key as top-level ───────────────────────
    fm = capture._frontmatter("---\nurl: http://x\nlinks:\n  aliases: [a, b]\n---\nbody")
    check("_frontmatter reads the real top-level key", fm.get("url") == "http://x")
    check("_frontmatter does NOT mis-read the nested 'aliases' as a top-level key", "aliases" not in fm)

finally:
    shutil.rmtree(d, ignore_errors=True)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
sys.exit(1 if FAIL else 0)
