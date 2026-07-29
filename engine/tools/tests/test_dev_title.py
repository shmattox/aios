"""clean_dev_title — the dashboard's dev-card title/badge extraction (A109 inbox fold-in)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))
from dashboard_server import clean_dev_title  # noqa: E402


def test_park_drops_date_and_source_meta():
    t, b = clean_dev_title("⚠[park: 2026-07-16 Seth — YouTube refuses to flip the playlist; skip]")
    assert b == "parked"
    assert t == "YouTube refuses to flip the playlist; skip"


def test_block_keeps_real_subject():
    # "guard-freeze" is the subject, not a date/source — must NOT be dropped.
    t, b = clean_dev_title("⛔[block: guard-freeze — needs-you: decide]")
    assert b == "blocked"
    assert t == "guard-freeze — needs-you: decide"


def test_unclosed_decided_bracket_takes_content_after_colon():
    t, b = clean_dev_title("▶[DECIDED 2026-07-22 (Seth, needs-you walk): per-family canonical semantics")
    assert b == "decided"
    assert t == "per-family canonical semantics"


def test_nested_bracket_is_depth_safe():
    # the inner `[GATE: human]` must not truncate the outer park bracket.
    t, b = clean_dev_title("⚠[park: 2026-07-18 relay — economic feed `[GATE: human]` and more]")
    assert b == "parked"
    assert "economic feed" in t and "and more" in t


def test_no_annotation_passes_through():
    t, b = clean_dev_title("SSOT flip (state-consolidation)")
    assert b is None
    assert t == "SSOT flip (state-consolidation)"


def test_veto_group_badges_without_glyph():
    t, b = clean_dev_title("Trello fetch/route made atomic 2026-07-28", group="veto")
    assert b == "veto"
    assert t.startswith("Trello fetch/route made atomic")


def test_titleless_annotation_falls_back_not_empty():
    # headline is pure annotation with no readable remainder — never return empty/too-short.
    t, b = clean_dev_title("▶[DECIDED 2026-07-22 (Seth, needs-you walk): (i)")
    assert b == "decided"
    assert len(t) >= 6  # falls back to the raw (glyph-stripped) text
