"""notion_gather.py offline tests (A18) — pure functions + token resolution.
No network: the query path is exercised only up to argument/token handling."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import notion_gather as ng  # noqa: E402


# ── normalize_id ─────────────────────────────────────────────────────────────

def test_normalize_id_accepts_collection_ref():
    assert (ng.normalize_id("collection://11111111-1111-1111-1111-111111111111")
            == "11111111-1111-1111-1111-111111111111")


def test_normalize_id_accepts_bare_undashed_and_uppercases_down():
    assert (ng.normalize_id("11111111111111111111111111111111")
            == "11111111-1111-1111-1111-111111111111")


def test_normalize_id_rejects_garbage():
    for bad in ("", "not-an-id", "collection://short", "1111111111111111111111111111111"):
        with pytest.raises(ValueError):
            ng.normalize_id(bad)


# ── property collapse + page normalization ───────────────────────────────────

CANNED_PAGE = {
    "id": "page-1",
    "url": "https://notion.so/page-1",
    "last_edited_time": "2026-07-04T12:00:00.000Z",
    "properties": {
        "Name": {"type": "title", "title": [
            {"plain_text": "Confirm the Lakeside "}, {"plain_text": "2024 property-tax payment"}]},
        "Status": {"type": "status", "status": {"name": "In progress"}},
        "Priority": {"type": "select", "select": {"name": "Urgent"}},
        "Due": {"type": "date", "date": {"start": "2026-07-03"}},
        "Notes": {"type": "rich_text", "rich_text": [{"plain_text": "banks reopen Monday"}]},
        "Done?": {"type": "checkbox", "checkbox": False},
        "Empty": {"type": "select", "select": None},
    },
}


def test_normalize_page_extracts_core_fields():
    rec = ng.normalize_page(CANNED_PAGE)
    assert rec["title"] == "Confirm the Lakeside 2024 property-tax payment"
    assert rec["status"] == "In progress"
    assert rec["priority"] == "Urgent"
    assert rec["due"] == "2026-07-03"
    assert rec["url"] == "https://notion.so/page-1"
    assert rec["props"]["Notes"] == "banks reopen Monday"
    assert "Empty" not in rec["props"]  # None values dropped


def test_normalize_page_status_from_select_named_status():
    page = {"id": "p", "properties": {
        "Task": {"type": "title", "title": [{"plain_text": "t"}]},
        "Task Status": {"type": "select", "select": {"name": "Open"}},
    }}
    assert ng.normalize_page(page)["status"] == "Open"


def test_filter_items_is_case_insensitive_and_keeps_statusless():
    items = [{"status": "Done"}, {"status": "done"}, {"status": "Open"}, {"status": None}]
    kept = ng.filter_items(items, ["Done"])
    assert [i["status"] for i in kept] == ["Open", None]
    assert ng.filter_items(items, []) == items


# ── token resolution ─────────────────────────────────────────────────────────

def test_resolve_token_prefers_env(monkeypatch):
    monkeypatch.setenv("AIOS_NG_TEST_TOKEN", "  secret_abc  ")
    tok, source = ng.resolve_token("AIOS_NG_TEST_TOKEN")
    assert tok == "secret_abc" and source == "env"


def test_no_token_exits_2_with_setup_hint(monkeypatch, capsys):
    name = "AIOS_NG_TEST_TOKEN_ABSENT_ZZZ"  # never set; CredMan lookup misses too
    monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as e:
        ng.main(["--token-env", name, "check"])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "cmdkey /generic:" in err and name in err


def test_query_source_clamps_page_size_to_api_max(monkeypatch):
    sent = {}

    def fake_request(method, url, token, version, body=None, timeout=30):
        sent["page_size"] = body["page_size"]
        return 200, {"results": [], "has_more": False}

    monkeypatch.setattr(ng, "_request", fake_request)
    endpoint, pages, err = ng._query_source("11111111-1111-1111-1111-111111111111", "tok", 500)
    assert sent["page_size"] == 100 and err is None


def test_query_source_reports_midpagination_error_not_fallback_404(monkeypatch):
    calls = {"n": 0}

    def fake_request(method, url, token, version, body=None, timeout=30):
        calls["n"] += 1
        if calls["n"] == 1:  # page 1 OK on the data-source endpoint
            return 200, {"results": [{"id": "p1"}], "has_more": True, "next_cursor": "c2"}
        return 429, {"message": "rate limited"}  # page 2 fails

    monkeypatch.setattr(ng, "_request", fake_request)
    endpoint, pages, err = ng._query_source("11111111-1111-1111-1111-111111111111", "tok", 200)
    assert endpoint is None and pages == []
    assert "429" in err and "rate limited" in err and "after 1 pages" in err
    assert calls["n"] == 2  # never fell through to the database endpoint


def test_tasks_output_shape_offline(monkeypatch, capsys):
    """tasks with a live token but an unreachable network path must still emit the
    JSON envelope (ok:false + error) and exit 1 — per-source errors never raise."""
    monkeypatch.setenv("AIOS_NG_TEST_TOKEN", "secret_abc")
    monkeypatch.setattr(ng, "_request", lambda *a, **k: (0, {"message": "network error: offline"}))
    rc = ng.main(["--token-env", "AIOS_NG_TEST_TOKEN", "tasks",
                  "--db", "collection://11111111-1111-1111-1111-111111111111"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["live"] is False
    assert out["sources"][0]["ok"] is False
    assert "network error" in out["sources"][0]["error"]


# ── A95 retrieve (Done-vs-vanished direct read) ──────────────────────────────
_PID = "22222222-2222-2222-2222-222222222222"


def _page(status, archived=False, in_trash=False):
    return {"id": _PID, "url": "https://n/x", "archived": archived, "in_trash": in_trash,
            "last_edited_time": "2026-07-18T00:00:00Z",
            "properties": {"Name": {"type": "title", "title": [{"plain_text": "Druid Fund Q1"}]},
                           "Status": {"type": "status", "status": {"name": status}}}}


def test_retrieve_found_done_page(monkeypatch):
    """A page present with Status=Done → found:True and the status readable → 'completed', not a hedge."""
    monkeypatch.setattr(ng, "_request", lambda m, u, t, v, body=None, timeout=30: (200, _page("Done")))
    r = ng.retrieve_page(_PID, "tok")
    assert r["ok"] is True and r["found"] is True and r["archived"] is False
    assert r["page"]["status"] == "Done" and r["page"]["title"] == "Druid Fund Q1"


def test_retrieve_archived_flag(monkeypatch):
    monkeypatch.setattr(ng, "_request", lambda *a, **k: (200, _page("Done", in_trash=True)))
    assert ng.retrieve_page(_PID, "tok")["archived"] is True


def test_retrieve_404_is_genuinely_absent(monkeypatch):
    """HTTP 404 → found:False — the only verdict that licenses 'no longer reachable'."""
    monkeypatch.setattr(ng, "_request", lambda *a, **k: (404, {"message": "Could not find page"}))
    r = ng.retrieve_page(_PID, "tok")
    assert r["ok"] is False and r["found"] is False and r["http"] == 404


def test_retrieve_network_error_is_undecided_never_absent(monkeypatch):
    """A network/degraded read must NOT report absent (found:None) — the caller degrades to 'unverified'."""
    monkeypatch.setattr(ng, "_request", lambda *a, **k: (0, {"message": "network error: offline"}))
    r = ng.retrieve_page(_PID, "tok")
    assert r["ok"] is False and r["found"] is None  # NOT False


def test_retrieve_falls_back_to_db_version(monkeypatch):
    """A page the data-source version 404s but the database version serves is still found (like flip)."""
    calls = {"n": 0}
    def fake(m, u, t, v, body=None, timeout=30):
        calls["n"] += 1
        return (404, {"message": "x"}) if calls["n"] == 1 else (200, _page("Open"))
    monkeypatch.setattr(ng, "_request", fake)
    r = ng.retrieve_page(_PID, "tok")
    assert r["found"] is True and r["page"]["status"] == "Open" and calls["n"] == 2


def test_retrieve_transient_then_404_is_undecided_not_absent(monkeypatch):
    """A DS-endpoint transient (500) followed by a DB-endpoint 404 must NOT report 'absent' — only
    BOTH endpoints agreeing 404 licenses found:False, else a blip would render a false 'gone' line."""
    calls = {"n": 0}
    def fake(m, u, t, v, body=None, timeout=30):
        calls["n"] += 1
        return (500, {"message": "server error"}) if calls["n"] == 1 else (404, {"message": "x"})
    monkeypatch.setattr(ng, "_request", fake)
    r = ng.retrieve_page(_PID, "tok")
    assert r["found"] is None and r["ok"] is False   # undecided, NOT a false absent


def test_retrieve_cli_offline_envelope(monkeypatch, capsys):
    monkeypatch.setenv("AIOS_NG_TEST_TOKEN", "secret_abc")
    monkeypatch.setattr(ng, "_request", lambda *a, **k: (404, {"message": "gone"}))
    rc = ng.main(["--token-env", "AIOS_NG_TEST_TOKEN", "retrieve", "--page", _PID])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["found"] is False and out["http"] == 404


if __name__ == "__main__":
    # suite_test.py runs each test_*.py as a subprocess and asserts exit 0 - without this
    # block a pytest-style file passes VACUOUSLY (defines functions, exits 0; A7 finding).
    sys.exit(pytest.main([__file__, "-q"]))
