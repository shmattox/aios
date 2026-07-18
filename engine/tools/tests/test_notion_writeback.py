"""notion_writeback.py offline tests (A7) — the tactical write-back fence, pure layer only.
No network: payload construction, the economic content gate, the flip-type fence, the
allowlist check, and receipt shape. The live path is exercised only up to argument/fence
handling (fences fire BEFORE token resolution, so refusals are testable offline).

The four write-back.md rules as code (each pinned here):
  1. allowlist   — a db not passed via --writable is refused (exit 3), read-only by default.
  2. content gate — lane_policy's ECONOMIC_TRIPWIRE_RE over every outgoing STRING (field
                    names AND values AND the title): a hit refuses the WHOLE call, naming the
                    offending fields (fail-loud; the caller drops the economic term or routes
                    it through explicit approval — pause_economic).
  3. act-then-tell — every write appends one receipt row {ts, db, page_id, field, old, new,
                    by, run_id} to the change log; receipt is REQUIRED (no --change-log, no write).
  4. one write, typed — page creation writes only {title, rich_text, date, status, select,
                    checkbox} properties (a number/relation/people/rollup property is refused by
                    TYPE — that's where amounts live); `flip` may target only
                    {status, select, checkbox, date} (it can never rewrite content fields).
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import notion_writeback as nw  # noqa: E402


SCHEMA = {  # name -> type, as _schema_map extracts from a database/data-source GET
    "Name": "title", "Status": "select", "Note": "rich_text", "Date": "date",
    "Done?": "checkbox", "Amount": "number", "Owner": "people",
}


# ── build_properties (rule 4, create side) ───────────────────────────────────

def test_build_properties_all_allowed_types():
    props, refusals = nw.build_properties(SCHEMA, {
        "Name": "Session log 2026-07-05", "Status": "Done", "Note": "one line",
        "Date": "2026-07-05", "Done?": "true",
    })
    assert refusals == []
    assert props["Name"] == {"title": [{"text": {"content": "Session log 2026-07-05"}}]}
    assert props["Status"] == {"select": {"name": "Done"}}
    assert props["Note"] == {"rich_text": [{"text": {"content": "one line"}}]}
    assert props["Date"] == {"date": {"start": "2026-07-05"}}
    assert props["Done?"] == {"checkbox": True}


def test_build_properties_refuses_number_and_people_by_type():
    _, refusals = nw.build_properties(SCHEMA, {"Amount": "5", "Owner": "someone"})
    assert {r["field"] for r in refusals} == {"Amount", "Owner"}
    assert all(r["reason"].startswith("type") for r in refusals)


def test_build_properties_refuses_unknown_property():
    _, refusals = nw.build_properties(SCHEMA, {"Nope": "x"})
    assert refusals and refusals[0]["field"] == "Nope" and "unknown" in refusals[0]["reason"]


def test_build_properties_refuses_bad_checkbox_value():
    _, refusals = nw.build_properties(SCHEMA, {"Done?": "maybe"})
    assert refusals and refusals[0]["field"] == "Done?"


# ── content gate (rule 2) ────────────────────────────────────────────────────

def test_content_gate_hits_economic_value():
    hits = nw.content_gate({"Note": "reviewed the cap table today"})
    assert hits == ["Note"]


def test_content_gate_hits_economic_field_name():
    assert nw.content_gate({"Purchase Price": "TBD"}) == ["Purchase Price"]


def test_content_gate_hits_transfer_amount():
    assert nw.content_gate({"Note": "wired $2.5M to escrow"}) == ["Note"]


def test_content_gate_passes_clean_tactical_content():
    assert nw.content_gate({
        "Name": "Session log 2026-07-05 — relay work", "Status": "Done",
        "Note": "flipped the resolved open item; drafted two wiki pages",
    }) == []


# ── flip fence (rule 4, flip side) ───────────────────────────────────────────

def test_flip_guard_allows_status_select_checkbox_date():
    for t in ("status", "select", "checkbox", "date"):
        assert nw.flip_guard({"type": t, t: None}) is None


def test_flip_guard_refuses_content_fields():
    for t in ("title", "rich_text", "number", "people", "relation"):
        assert nw.flip_guard({"type": t, t: None}) is not None


# ── parent alias resolution (rule 1 on the flip side) ───────────────────────
# Notion-Version 2025-09-03 reports a page's parent as its DATA-SOURCE id, while the
# allowlist may hold the DATABASE id (or vice versa) — two ids, one container. The fence
# resolves the sibling alias and accepts if EITHER is allowlisted; a resolution failure
# only shrinks the set (fail-closed).

DS = "44444444-4444-4444-4444-444444444401"
DB = "44444444-4444-4444-4444-444444444402"


def test_parent_candidates_resolves_database_from_data_source(monkeypatch):
    import notion_writeback as nw2
    monkeypatch.setattr(nw2.ng, "_request",
                        lambda *a, **k: (200, {"parent": {"database_id": DB}}))
    ids = nw2.parent_db_candidates({"type": "data_source_id", "data_source_id": DS}, "tok")
    assert ids == {DS, DB}


def test_parent_candidates_resolves_data_sources_from_database(monkeypatch):
    import notion_writeback as nw2
    monkeypatch.setattr(nw2.ng, "_request",
                        lambda *a, **k: (200, {"data_sources": [{"id": DS}]}))
    ids = nw2.parent_db_candidates({"type": "database_id", "database_id": DB}, "tok")
    assert ids == {DS, DB}


def test_parent_candidates_fail_closed_on_resolution_error(monkeypatch):
    import notion_writeback as nw2
    monkeypatch.setattr(nw2.ng, "_request", lambda *a, **k: (0, {"message": "offline"}))
    ids = nw2.parent_db_candidates({"type": "data_source_id", "data_source_id": DS}, "tok")
    assert ids == {DS}   # smaller set -> can only refuse more, never allow more


# ── receipt (rule 3) ─────────────────────────────────────────────────────────

def test_receipt_row_shape(tmp_path):
    log = tmp_path / "changelog.jsonl"
    row = nw.append_receipt(str(log), db="db-1", page_id="p-1", field="Status",
                            old="Open", new="Done", by="test", run_id="2026-07-05")
    on_disk = json.loads(log.read_text(encoding="utf-8").strip())
    assert on_disk == row
    assert set(row) >= {"ts", "db", "page_id", "field", "old", "new", "by", "run_id"}


# ── CLI fences fire offline, before any token/network (rules 1+2+3) ─────────

def test_cli_log_row_refuses_db_not_in_writable(capsys):
    rc = nw.main(["log-row", "--db", "11111111-1111-1111-1111-111111111111",
                  "--writable", "22222222-2222-2222-2222-222222222222",
                  "--title", "x", "--change-log", "unused.jsonl"])
    assert rc == 3
    assert "not in the writable allowlist" in capsys.readouterr().err


def test_cli_log_row_refuses_economic_title(capsys):
    rc = nw.main(["log-row", "--db", "11111111-1111-1111-1111-111111111111",
                  "--writable", "11111111-1111-1111-1111-111111111111",
                  "--title", "record the promissory note terms",
                  "--change-log", "unused.jsonl"])
    assert rc == 3
    assert "economic" in capsys.readouterr().err.lower()


def test_cli_flip_refuses_economic_target_value(capsys):
    rc = nw.main(["flip", "--page", "11111111-1111-1111-1111-111111111111",
                  "--field", "Status", "--to", "refinance approved",
                  "--writable", "11111111-1111-1111-1111-111111111111",
                  "--change-log", "unused.jsonl"])
    assert rc == 3
    assert "economic" in capsys.readouterr().err.lower()


def test_cli_requires_change_log():
    with pytest.raises(SystemExit):   # argparse: --change-log is required (rule 3)
        nw.main(["log-row", "--db", "x", "--writable", "x", "--title", "t"])


# ── A95 add-row (conclusion write): inherits log-row's fences ────────────────

def test_cli_add_row_refuses_db_not_in_writable(capsys):
    """A conclusion whose target log is NOT allowlisted is refused (rule 1) — the fact-free
    degrade an install with no session_log group falls back on (threads-only)."""
    rc = nw.main(["add-row", "--db", "11111111-1111-1111-1111-111111111111",
                  "--writable", "22222222-2222-2222-2222-222222222222",
                  "--title", "resolved radiology purpose", "--change-log", "unused.jsonl"])
    assert rc == 3
    assert "not in the writable allowlist" in capsys.readouterr().err


def test_cli_add_row_refuses_economic_content(capsys):
    """pause_economic guards conclusion rows exactly as it guards flips (rule 2)."""
    rc = nw.main(["add-row", "--db", "11111111-1111-1111-1111-111111111111",
                  "--writable", "11111111-1111-1111-1111-111111111111",
                  "--title", "record the promissory note payoff terms",
                  "--change-log", "unused.jsonl"])
    assert rc == 3
    assert "economic" in capsys.readouterr().err.lower()


def test_cli_create_task_refuses_db_not_in_writable(capsys):
    """A96: an approved proposal's create-task inherits log-row's allowlist fence (rule 1) — an
    unlisted task DB is refused before any network call."""
    rc = nw.main(["create-task", "--db", "11111111-1111-1111-1111-111111111111",
                  "--writable", "22222222-2222-2222-2222-222222222222",
                  "--title", "File the Labrador lab results", "--change-log", "unused.jsonl"])
    assert rc == 3
    assert "not in the writable allowlist" in capsys.readouterr().err


def test_cli_create_task_refuses_economic_content(capsys):
    rc = nw.main(["create-task", "--db", "11111111-1111-1111-1111-111111111111",
                  "--writable", "11111111-1111-1111-1111-111111111111",
                  "--title", "wire transfer the purchase price", "--change-log", "unused.jsonl"])
    assert rc == 3
    assert "economic" in capsys.readouterr().err.lower()


def test_cli_create_task_defaults_by_to_gate_proposal(monkeypatch):
    seen = {}
    monkeypatch.setattr(nw, "cmd_log_row", lambda args: (seen.update(by=args.by, cmd=args.cmd) or 0))
    nw.main(["create-task", "--db", "x", "--writable", "x", "--title", "t", "--change-log", "c.jsonl"])
    assert seen == {"by": "aios-gate-proposal", "cmd": "create-task"}


def test_cli_add_row_defaults_by_to_conclusion(monkeypatch):
    """add-row routes to cmd_log_row with --by defaulted to the conclusion writer (not the
    generic writeback author), while an explicit --by still wins."""
    seen = {}
    monkeypatch.setattr(nw, "cmd_log_row", lambda args: (seen.update(by=args.by, cmd=args.cmd) or 0))
    nw.main(["add-row", "--db", "x", "--writable", "x", "--title", "t", "--change-log", "c.jsonl"])
    assert seen == {"by": "aios-gate-conclusion", "cmd": "add-row"}
    seen.clear()
    nw.main(["add-row", "--db", "x", "--writable", "x", "--title", "t", "--change-log", "c.jsonl",
             "--by", "someone"])
    assert seen["by"] == "someone"


if __name__ == "__main__":
    # suite_test.py runs each test_*.py as a subprocess and asserts exit 0 — a pytest-style
    # file with no self-exec block would pass VACUOUSLY (define functions, exit 0). Run
    # ourselves under pytest so the checks actually execute in the suite.
    sys.exit(pytest.main([__file__, "-q"]))
