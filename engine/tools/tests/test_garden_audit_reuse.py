#!/usr/bin/env python3
r"""A65 Task 1 — garden_audit exposes walk_pages() + inbound + adjacency for reuse.
Hermetic. Run: python engine/tools/tests/test_garden_audit_reuse.py"""
import os, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)
import garden_audit

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok   {name}")
    else: FAIL += 1; print(f"  FAIL {name}")

def w(root, rel, text=""):
    p = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)

def main():
    vault = tempfile.mkdtemp(prefix="reuse-vault-")
    try:
        B = "03_Dev/wiki/"
        w(vault, B + "projects/hub.md", "# hub\nsee [[knowledge/leaf]]\n")
        w(vault, B + "knowledge/leaf.md", "# leaf\nback to [[projects/hub]]\n")
        w(vault, B + "entities/lonely.md", "# lonely\n")
        w(vault, B + "index.md", "- [[projects/hub]]\n")

        # walk_pages: returns rel->abspath for all content pages, excludes staging/.templates
        w(vault, B + "staging/draft.md", "x")
        pages = garden_audit.walk_pages(vault, "03_Dev")
        check("walk_pages finds content pages", "knowledge/leaf.md" in pages and "projects/hub.md" in pages)
        check("walk_pages returns abspaths", os.path.isabs(pages["projects/hub.md"]))
        check("walk_pages excludes staging/", "staging/draft.md" not in pages)

        a = garden_audit.audit_kb(vault, "03_Dev")
        # backward-compatible keys unchanged
        check("orphans unchanged (lonely is orphan)", a["orphans"] == ["entities/lonely.md"])
        check("dead_links unchanged (none)", a["dead_links"] == [])
        # new inbound key
        check("inbound: hub gets index+leaf = 2", a["inbound"]["projects/hub.md"] == 2)
        check("inbound: leaf gets hub = 1", a["inbound"]["knowledge/leaf.md"] == 1)
        check("inbound: lonely = 0", a["inbound"]["entities/lonely.md"] == 0)
        # new adjacency key (outbound, in-KB, no self-links)
        check("adjacency: hub -> leaf", a["adjacency"]["projects/hub.md"] == ["knowledge/leaf.md"])
        check("adjacency: lonely -> []", a["adjacency"]["entities/lonely.md"] == [])

        # stem-collision (bare-stem) link resolves adjacency + inbound via the stem branch
        vault2 = tempfile.mkdtemp(prefix="reuse-stem-")
        try:
            w(vault2, B + "knowledge/widget.md", "# widget\n")
            w(vault2, B + "projects/refs.md", "# refs\nuses [[widget]]\n")   # bare stem, no path
            s = garden_audit.audit_kb(vault2, "03_Dev")
            check("stem-link credits inbound", s["inbound"]["knowledge/widget.md"] == 1)
            check("stem-link credits adjacency", s["adjacency"]["projects/refs.md"] == ["knowledge/widget.md"])
        finally:
            shutil.rmtree(vault2, ignore_errors=True)
    finally:
        shutil.rmtree(vault, ignore_errors=True)
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
