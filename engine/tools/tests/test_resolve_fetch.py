import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import resolve_fetch as rf

ENTITY = """---
title: Bayview property
kb: familyoffice
links:
  drive:
    - "1AbCdeclarationID — 2024 insurance declaration"
    - "1XyzHUD — purchase HUD"
  trello:
    - "https://trello.com/c/abc — Bayview weekly OPS"
  notion:
    - "page-123 — Bayview (Assets & Liabilities row)"
  aliases: ["Bayview", "the Bayview deal"]
tags: [property, florida]
---

Body text about Bayview.
"""

NO_LINKS = """---
title: Some entity
kb: familyoffice
---
Body.
"""

def test_reads_block_lists_and_inline_lists():
    blk = rf.read_links_block(ENTITY)
    assert blk["drive"][0].startswith("1AbCdeclarationID")
    assert len(blk["drive"]) == 2
    assert blk["trello"] == ["https://trello.com/c/abc — Bayview weekly OPS"]
    assert blk["aliases"] == ["Bayview", "the Bayview deal"]

def test_candidates_split_ref_from_desc():
    out = rf.candidates_for(ENTITY)
    assert out["has_crosswalk"] is True
    drive = [c for c in out["candidates"] if c["source"] == "drive"]
    assert {"source": "drive", "ref": "1AbCdeclarationID", "desc": "2024 insurance declaration"} in drive
    assert "Bayview" in out["aliases"]

def test_no_links_block_signals_fallback():
    out = rf.candidates_for(NO_LINKS)
    assert out["has_crosswalk"] is False
    assert out["candidates"] == []
    assert out["aliases"] == []

INLINE_COMMA = """---
title: X
links:
  drive: ["fileA — declaration, as amended", "fileB — HUD"]
---
body
"""

def test_inline_list_handles_comma_in_description():
    blk = rf.read_links_block(INLINE_COMMA)
    assert blk["drive"] == ["fileA — declaration, as amended", "fileB — HUD"]
    out = rf.candidates_for(INLINE_COMMA)
    refs = [(c["ref"], c["desc"]) for c in out["candidates"]]
    assert ("fileA", "declaration, as amended") in refs
    assert ("fileB", "HUD") in refs


def test_a39_reads_top_level_obsidian_aliases_inline():
    # A39: an entity with ONLY a top-level `aliases:` (the Obsidian-native key, no duplicate
    # links.aliases) must still resolve those aliases.
    ent = '---\ntitle: Bayview\naliases: [Bayview Flats, BVF]\nlinks:\n  drive:\n    - "id — a doc"\n---\n'
    out = rf.candidates_for(ent)
    assert out["aliases"] == ["Bayview Flats", "BVF"], out["aliases"]


def test_a39_reads_top_level_aliases_block_list():
    ent = "---\ntitle: X\naliases:\n  - Alpha\n  - Beta\nlinks:\n  notion:\n    - \"p — page\"\n---\n"
    assert rf.candidates_for(ent)["aliases"] == ["Alpha", "Beta"]


def test_a39_unions_block_and_top_level_aliases_deduped():
    # both present -> union, order-preserving, de-duplicated (no double-count of a shared alias)
    ent = ("---\ntitle: X\naliases: [BVF, Shared]\n"
           "links:\n  aliases: [Shared, Legacy]\n  drive:\n    - \"id — d\"\n---\n")
    al = rf.candidates_for(ent)["aliases"]
    assert al == ["Shared", "Legacy", "BVF"], al          # block first, then the new top-level, deduped
    assert al.count("Shared") == 1


def test_a39_no_aliases_anywhere_is_empty():
    assert rf.candidates_for("---\ntitle: X\nlinks:\n  drive:\n    - \"id — d\"\n---\n")["aliases"] == []


if __name__ == "__main__":
    # also runnable without pytest, matching the repo's other test files (suite_test.py runs each as a subprocess)
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print("  ok  " + fn.__name__)
        except Exception:
            failed += 1; print(" FAIL " + fn.__name__); traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
