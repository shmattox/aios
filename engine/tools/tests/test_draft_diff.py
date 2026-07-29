"""draft_diff — line-level diff for the dashboard 'Proposed change' section."""
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import draft_diff  # noqa: E402


def test_replace_shows_del_then_add():
    cur = "## Note terms\n- rate: 7.25%\n- balance: $438,000\n"
    new = "## Note terms\n- rate: 6.875%\n- balance: $412,500\n"
    ops = [(r["op"], r["text"]) for r in draft_diff.compute_diff(cur, new)]
    assert ("ctx", "## Note terms") in ops
    assert ("del", "- rate: 7.25%") in ops
    assert ("add", "- rate: 6.875%") in ops
    assert ("del", "- balance: $438,000") in ops
    assert ("add", "- balance: $412,500") in ops


def test_new_page_is_all_add():
    rows = draft_diff.compute_diff("", "# New\n\nbody\n")
    assert rows and all(r["op"] == "add" for r in rows)


def test_long_unchanged_run_collapses():
    t = "\n".join(f"line {i}" for i in range(40)) + "\n"
    rows = draft_diff.compute_diff(t, t, context=2)
    assert all(r["op"] == "ctx" for r in rows)
    assert any("unchanged lines" in r["text"] for r in rows)
    assert len(rows) < 40  # collapsed, not the full 40 lines


def test_summarize_counts():
    rows = draft_diff.compute_diff("a\nb\n", "a\nc\n")
    assert draft_diff.summarize(rows) == {"added": 1, "removed": 1}


def test_pure_insert_and_delete():
    ins = draft_diff.compute_diff("a\n", "a\nb\n")
    assert ("add", "b") in [(r["op"], r["text"]) for r in ins]
    dele = draft_diff.compute_diff("a\nb\n", "a\n")
    assert ("del", "b") in [(r["op"], r["text"]) for r in dele]
