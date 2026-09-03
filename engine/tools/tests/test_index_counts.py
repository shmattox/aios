#!/usr/bin/env python3
"""A7926: the wiki index's count frontmatter gets a writer. Hermetic — a tmp wiki tree, no vault."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import index_counts as ic

INDEX = """---
type: index
kb: Dev
last_updated: 2026-07-21
journal_count: 76
page_count: 165
explored: true
---

# Wiki Index

Prose that must survive untouched, including a colon: and a *stray* emphasis.

**Counts (as of 2026-07-21):** 76 journal + 2 knowledge = **165 content pages** (`people/` empty; \
`staging/` scaffolding excluded).

## Entities

More prose.
"""


def _wiki(tmp_path, pages=None, index=INDEX):
    w = tmp_path / "wiki"
    for folder, n in (pages or {"journal": 3, "knowledge": 2, "staging": 9}).items():
        d = w / folder
        d.mkdir(parents=True)
        for i in range(n):
            (d / ("p%d.md" % i)).write_text("x", encoding="utf-8")
    w.mkdir(exist_ok=True)
    (w / "index.md").write_text(index, encoding="utf-8", newline="")
    return str(w)


def test_counts_come_from_disk_and_exclude_structural(tmp_path):
    vals, by = ic.compute(_wiki(tmp_path), today="2026-09-03")
    assert vals["journal_count"] == 3
    assert vals["page_count"] == 5          # 3 journal + 2 knowledge; staging's 9 excluded
    assert "staging" not in by


def test_an_empty_folder_counts_as_zero_not_as_absent(tmp_path):
    w = _wiki(tmp_path, {"journal": 1, "people": 0})
    _, by = ic.compute(w, today="2026-09-03")
    assert by["people"] == 0 and by["journal"] == 1


def test_golden_diff_only_the_count_lines_change(tmp_path):
    """The acceptance criterion: nothing but the count frontmatter and the Counts numbers move."""
    w = _wiki(tmp_path)
    before = open(os.path.join(w, "index.md"), encoding="utf-8").read()
    ic.recompute(w, apply=True, today="2026-09-03")
    after = open(os.path.join(w, "index.md"), encoding="utf-8").read()
    b, a = before.split("\n"), after.split("\n")
    assert len(b) == len(a), "line count must not change"
    moved = [i for i in range(len(b)) if b[i] != a[i]]
    keys = [b[i].split(":", 1)[0].strip() for i in moved if b[i].startswith(("last_", "journal_", "page_"))]
    assert sorted(keys) == ["journal_count", "last_updated", "page_count"]
    assert len(moved) == 4, "3 frontmatter keys + the Counts sentence, nothing else"


def test_prose_and_key_order_survive_verbatim(tmp_path):
    w = _wiki(tmp_path)
    ic.recompute(w, apply=True, today="2026-09-03")
    after = open(os.path.join(w, "index.md"), encoding="utf-8").read()
    assert "Prose that must survive untouched, including a colon: and a *stray* emphasis." in after
    assert "## Entities" in after and after.endswith("More prose.\n")
    assert after.index("last_updated") < after.index("journal_count") < after.index("page_count")


def test_the_counts_sentence_keeps_its_editorial_clause(tmp_path):
    w = _wiki(tmp_path)
    ic.recompute(w, apply=True, today="2026-09-03")
    after = open(os.path.join(w, "index.md"), encoding="utf-8").read()
    assert "3 journal + 2 knowledge = **5 content pages**" in after
    assert "(`people/` empty; `staging/` scaffolding excluded)" in after
    assert "(as of 2026-09-03)" in after


def test_an_unrecognized_counts_sentence_is_left_alone_not_guessed_at(tmp_path):
    odd = INDEX.replace("**Counts (as of 2026-07-21):** 76 journal + 2 knowledge = "
                        "**165 content pages**", "**Counts:** roughly a lot")
    w = _wiki(tmp_path, index=odd)
    r = ic.recompute(w, apply=True, today="2026-09-03")
    after = open(os.path.join(w, "index.md"), encoding="utf-8").read()
    assert r["counts_line_refreshed"] is False
    assert "**Counts:** roughly a lot" in after      # untouched, never regenerated
    assert "journal_count: 3" in after               # frontmatter still reconciled


def test_dry_run_writes_nothing(tmp_path):
    w = _wiki(tmp_path)
    before = open(os.path.join(w, "index.md"), encoding="utf-8").read()
    r = ic.recompute(w, apply=False, today="2026-09-03")
    assert r["dirty"] is True and r["applied"] is False
    assert open(os.path.join(w, "index.md"), encoding="utf-8").read() == before


def test_rerun_is_idempotent(tmp_path):
    w = _wiki(tmp_path)
    ic.recompute(w, apply=True, today="2026-09-03")
    once = open(os.path.join(w, "index.md"), encoding="utf-8").read()
    r = ic.recompute(w, apply=True, today="2026-09-03")
    assert r["dirty"] is False and r["changed_keys"] == []
    assert open(os.path.join(w, "index.md"), encoding="utf-8").read() == once


def test_absent_keys_are_not_invented(tmp_path):
    """Reconciles an existing catalog; never bolts a schema onto an index that lacks one."""
    w = _wiki(tmp_path, index="---\ntype: index\n---\n\n# I\n")
    ic.recompute(w, apply=True, today="2026-09-03")
    after = open(os.path.join(w, "index.md"), encoding="utf-8").read()
    assert "journal_count" not in after and after == "---\ntype: index\n---\n\n# I\n"


def test_missing_index_and_frontmatterless_index_fail_loud(tmp_path):
    import pytest
    (tmp_path / "wiki").mkdir()
    with pytest.raises(SystemExit):
        ic.recompute(str(tmp_path / "wiki"), apply=True)
    (tmp_path / "wiki" / "index.md").write_text("# no frontmatter\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        ic.recompute(str(tmp_path / "wiki"), apply=True)
