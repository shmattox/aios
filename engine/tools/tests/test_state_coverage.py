"""Tests for state_coverage.py (C1) — the gate: every live Notion property must be mapped or
declared ignored. pytest-native (`def test_*`, `assert`) so this module can be collected DIRECTLY
by pytest (tools/tests/conftest.py only ignores files with no module-level `def test_*`, and a
module-level FAIL-list + unguarded sys.exit would abort pytest's import with an INTERNALERROR —
see test_task_manifest.py / test_state_validate.py for the same shape). `suite_test.py` also runs
this file as a subprocess and asserts exit 0 via the `__main__` runner below.
"""
import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path

_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _TOOLS)
import state_coverage as sc

_TABLE = {"name": "state-thing", "source_db": "things",
          "fields": [("name", "title", None, "Name", None),
                     ("silo_filter", "select", None, "Silo (filter)", None)],
          "ignored": {"Rollup Total": "derived rollup"}}


def test_metadata_is_covered():
    for p in ("Created on", "Last edited by", "Last edited time", "Last edit",
              "date:Due:end", "date:Due:is_datetime"):
        assert sc.is_metadata(p), p
    assert not sc.is_metadata("Collateral Value")


def test_twin_rule():
    # the twin rule is load-bearing (spec §2): a mapped flat `<name> (filter)` twin covers the
    # relation it mirrors — without it this check reports 58 findings where the truth is 30 (21
    # of the noise being Personal's `Silo`), and a gate nobody trusts gets switched off.
    rows = [{"url": "u", "Name": "n", "Silo": "rel", "Silo (filter)": "01_Health"}]
    assert "Silo" not in sc.uncovered(_TABLE, rows)


def test_declared_ignore_is_covered():
    rows = [{"url": "u", "Name": "n", "Rollup Total": 5}]
    assert sc.uncovered(_TABLE, rows) == []


def test_genuine_gap_is_reported():
    rows = [{"url": "u", "Name": "n", "Collateral Value": 1200000}]
    assert sc.uncovered(_TABLE, rows) == ["Collateral Value"]


def test_empty_snapshot_is_an_error_not_a_pass():
    # the vacuous-pass mode: scanning nothing must never read as clean
    try:
        sc.check_scanned_or_raise(0, "demo")
        assert False, "zero properties seen must raise, not pass"
    except sc.CoverageError:
        pass
    sc.check_scanned_or_raise(7, "demo")  # a silo that DID see properties passes


# ── check_direction_coherent has a caller here (task-4-brief.md §Interfaces): `check_silo` takes
# `tasks_enabled` explicitly (default `()`) and folds the coherence check's problems into the
# returned dict. A caller passing none gets `direction_unchecked: true` rather than a coherence
# check that reads clean only because it never actually ran. ────────────────────────────────────
def _scratch_silo(direction):
    root = Path(tempfile.mkdtemp()).resolve()
    (root / "profile").mkdir()
    (root / "profile" / "domains.yaml").write_text("brief:\n  trigger: go\n", encoding="utf-8")
    sd = root / "state" / "domains" / "demo"
    (sd / "tables").mkdir(parents=True)
    (sd / "schema.yaml").write_text(
        "direction: %s\n" % direction + textwrap.dedent("""\
            state-thing:
              required: [name, type, notion_id]
              notion_source_db: things
              notion_fields:
                name: [Name, title]
            """), encoding="utf-8")
    snap = sd / "_snap"
    snap.mkdir()
    (snap / "things-export.json").write_text(
        json.dumps({"_meta": {}, "url_to_slug": {}, "rows": [{"Name": "Widget", "url": "u1"}]}),
        encoding="utf-8")
    return root, snap


def test_no_task_list_is_unchecked_not_a_silent_pass():
    root, snap = _scratch_silo("publish")
    r = sc.check_silo(root, "demo", snap)
    assert r.get("direction_unchecked") is True
    assert "direction_problems" not in r


def test_incoherent_direction_is_reported():
    root, snap = _scratch_silo("publish")
    r = sc.check_silo(root, "demo", snap, tasks_enabled=("domain-sync-demo",))
    assert len(r.get("direction_problems", [])) == 1
    assert "direction_unchecked" not in r


def test_coherent_direction_is_not_reported():
    root, snap = _scratch_silo("import")
    r = sc.check_silo(root, "demo", snap, tasks_enabled=("domain-sync-demo",))
    assert r.get("direction_problems") == []


def test_zero_mapping_silo_raises_not_a_vacuous_pass():
    # gm's real shape (C1 Task 1 finding): a silo with zero `notion_fields` tables has nothing to
    # scan. `checked` never advances past 0, so this must raise rather than report a clean 0-of-0.
    root = Path(tempfile.mkdtemp()).resolve()
    (root / "profile").mkdir()
    (root / "profile" / "domains.yaml").write_text("brief:\n  trigger: go\n", encoding="utf-8")
    sd = root / "state" / "domains" / "empty"
    (sd / "tables").mkdir(parents=True)
    (sd / "schema.yaml").write_text("direction: import\n", encoding="utf-8")
    try:
        sc.check_silo(root, "empty")
        assert False, "a silo with no notion_fields tables must raise, not pass"
    except sc.CoverageError:
        pass


if __name__ == "__main__":
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
