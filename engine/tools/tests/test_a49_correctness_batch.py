#!/usr/bin/env python3
"""A49 — review-surfaced correctness batch: one regression per bug (all silent-until-it-bites).

  (bug 1, resolve sweep staleness, retired with the resolve surface — A91, 2026-07-18)
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
