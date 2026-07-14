#!/usr/bin/env python3
"""frontmatter.py — the one guarded flat-frontmatter reader (A51 win ii: consolidates the 4 drifted
`_frontmatter`/`read_frontmatter` readers in capture/capture_router/sort/garden_distill).

Locks the A49-hardened contract so a future edit to one call site can't re-drift it: None/empty
guard, nested/comment/internal-space-key skip, quote-strip. Standalone; run:
python tools/tests/test_frontmatter.py"""
import os, sys

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../engine/tools
sys.path.insert(0, HARNESS)
from frontmatter import read_frontmatter as rf

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ok  " if cond else " FAIL ") + name)

# ── guards: the A49 minor (garden_distill crashed on these) ──
check("None text -> {} (no crash)", rf(None) == {})
check("empty text -> {}", rf("") == {})
check("no leading --- -> {}", rf("body only\n") == {})
check("no closing --- -> {}", rf("---\nkey: v\nbody without close\n") == {})

# ── basic flat scalars ──
fm = rf("---\ntype: source\nslug: my-thing\nn: 3\n---\nbody\n")
check("reads a top-level scalar", fm.get("type") == "source")
check("reads multiple scalars", fm.get("slug") == "my-thing" and fm.get("n") == "3")

# ── quote-strip (both quote styles) — garden_distill did NOT strip; the hardened reader does ──
check('double-quoted scalar stripped', rf('---\ntype: "source"\n---\n').get("type") == "source")
check("single-quoted scalar stripped", rf("---\ntype: 'source'\n---\n").get("type") == "source")

# ── nested / comment / internal-space-key are SKIPPED (not misread as top-level keys) ──
nested = rf("---\ntype: source\nlinks:\n  aliases: [a, b]\n  nested: x\n---\n")
check("indented/nested keys are skipped", "aliases" not in nested and "nested" not in nested)
check("top-level key alongside nesting still read", nested.get("type") == "source")
check("comment line (#) skipped", rf("---\n# note: x\ntype: source\n---\n") == {"type": "source"})
# a colon-bearing line whose KEY carries an internal space is not a real scalar key -> skipped
# (exercises the `" " not in k.strip()` guard specifically, not the no-colon guard)
check("key with an internal space skipped (not a real scalar key)",
      rf("---\nsome key: value\ntype: source\n---\n") == {"type": "source"})

# ── a value containing a colon keeps everything after the first colon ──
check("value may contain a colon (split on first only)",
      rf("---\nurl: https://x.com/a\n---\n").get("url") == "https://x.com/a")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
sys.exit(1 if FAIL else 0)
