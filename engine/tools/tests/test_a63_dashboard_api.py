import json, os, threading, urllib.request, urllib.error, urllib.parse
from pathlib import Path
import pytest, sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # engine/tools on path
from dashboard_server import make_server
import activity


@pytest.fixture()
def env_root(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "state" / "factory").mkdir(parents=True)
    draft = tmp_path / "SecondBrain" / "01_Personal" / "wiki" / "staging" / "d.md"
    draft.parent.mkdir(parents=True)
    draft.write_text("# Draft body", encoding="utf-8")
    (tmp_path / "state" / "brief-cache.json").write_text(json.dumps({
        "generated_utc": "2026-07-20T06:50:00Z",
        "act": [{"id": "t1", "title": "Do the thing", "domain": "personal",
                 "urgency": "high", "system_voice": "sv", "claude_voice": "cv"}],
        "held": [{"id": "q1", "kb": "personal", "lane": "review",
                  "recommended": "ship", "title": "Held item",
                  "draft_path": str(draft)}],
        "health_lines": {"pipeline": "ok"},
    }), encoding="utf-8")
    (tmp_path / "state" / "factory" / "standup.json").write_text(json.dumps({
        "generated": "2026-07-20", "groups": {"veto": [], "needs_you": [],
        "handed_off": [], "stuck": []}, "totals": {}}), encoding="utf-8")
    (tmp_path / "state" / "factory" / "spend-2026-07-20.json").write_text(
        json.dumps({"output_tokens": 1, "cost_usd": 2.5, "drains": 1,
                    "date": "2026-07-20"}), encoding="utf-8")
    (tmp_path / "state" / "factory" / "gate-metrics.json").write_text(
        json.dumps({"generated": "2026-07-20", "windows": {}}), encoding="utf-8")
    (tmp_path / "state" / "queue.json").write_text(json.dumps({"queue": []}),
                                                  encoding="utf-8")
    return tmp_path


@pytest.fixture()
def server(env_root):
    srv = make_server(env_root, port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


def _get_json(srv, path):
    port = srv.server_address[1]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return json.loads(r.read())


def test_mtimes_lists_watched(server):
    m = _get_json(server, "/api/mtimes")
    assert set(m) == {"brief", "standup", "gate_metrics", "spend", "queue", "board"}
    assert m["brief"] is not None


def test_brief_carries_age(server):
    b = _get_json(server, "/api/brief")
    assert b["act"][0]["id"] == "t1"
    assert b["_age_s"] >= 0


def test_standup(server):
    s = _get_json(server, "/api/standup")
    assert "groups" in s


def test_spend_aggregates(server):
    s = _get_json(server, "/api/spend")
    assert s["days"][0]["cost_usd"] == 2.5
    assert s["gate_metrics"]["generated"] == "2026-07-20"


def test_held_and_draft(server):
    h = _get_json(server, "/api/held")
    assert h["held"][0]["id"] == "q1"
    d = _get_json(server, "/api/draft?i=0")
    assert d["markdown"] == "# Draft body"


def test_draft_surfaces_paper_evidence(server, env_root):
    # A75 Paper-Governs — the pipeline attaches a paper_evidence packet to the
    # queue item; /api/draft surfaces it (render-only) so the card can show it.
    (env_root / "state" / "queue.json").write_text(json.dumps({"queue": [
        {"id": "q1", "stage": "awaiting", "paper_evidence": {
            "verdict": "matches", "quote": "rate of 6.875%",
            "doc": "Loan Mod.pdf", "section": "2.1",
            "checked_utc": "2026-07-29T04:10:00Z"}}]}), encoding="utf-8")
    d = _get_json(server, "/api/draft?i=0")
    assert d["paper_evidence"]["verdict"] == "matches"
    assert d["paper_evidence"]["doc"] == "Loan Mod.pdf"


def test_draft_no_paper_evidence_key_when_absent(server):
    # queue item has no packet (fixture queue is empty) → key simply omitted.
    d = _get_json(server, "/api/draft?i=0")
    assert "paper_evidence" not in d


def test_draft_bad_index_404(server):
    port = server.server_address[1]
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/draft?i=99", timeout=5)
    assert e.value.code == 404


def test_missing_file_is_null_not_error(server, env_root):
    (env_root / "state" / "brief-cache.json").unlink()
    m = _get_json(server, "/api/mtimes")
    assert m["brief"] is None


# --- Task 3: mirror browser (read-only domains) ---------------------------

@pytest.fixture()
def env_with_domains(env_root):
    t = env_root / "state" / "domains" / "personal" / "tables" / "tasks"
    t.mkdir(parents=True)
    (env_root / "state" / "domains" / "personal" / "schema.yaml").write_text(
        "silo: personal\n", encoding="utf-8")
    (t / "walk-dog.md").write_text(
        "---\nstatus: open\npriority: Urgent\n---\nWalk the dog daily.\n",
        encoding="utf-8")
    return env_root


@pytest.fixture()
def dserver(env_with_domains):
    srv = make_server(env_with_domains, port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


def test_domains_index(dserver):
    d = _get_json(dserver, "/api/domains")
    assert d["silos"][0]["silo"] == "personal"
    assert d["silos"][0]["tables"] == [{"name": "tasks", "count": 1}]


def test_domains_table_and_record(dserver):
    recs = _get_json(dserver, "/api/domains/personal/tasks")["records"]
    assert recs[0]["slug"] == "walk-dog"
    assert recs[0]["fields"]["priority"] == "Urgent"
    rec = _get_json(dserver, "/api/domains/personal/tasks/walk-dog")
    assert "Walk the dog" in rec["body"]


def test_domains_traversal_rejected(dserver):
    port = dserver.server_address[1]
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/domains/personal/..%2F..%2Fsecret", timeout=5)
    assert e.value.code in (400, 404)


def test_domains_raw_dotdot_rejected(dserver):
    # raw (non-%2F) dot-segment — the SAFE_SEG char class contains '.', so this is
    # only blocked by the explicit '.'/'..' rejection in _domains.
    port = dserver.server_address[1]
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/domains/personal/../secret", timeout=5)
    assert e.value.code in (400, 404)


# --- A120: /api/activity + log tail + SSE signal --------------------------

def test_api_activity_lists_runs_with_liveness(server, env_root):
    activity.start_run(env_root, id="factory-A1-1", surface="factory",
                       title="Draining A1", item_ids=["A1"], pid=os.getpid())
    body = _get_json(server, "/api/activity")
    ids = {r["id"] for r in body["runs"]}
    assert "factory-A1-1" in ids
    run = next(r for r in body["runs"] if r["id"] == "factory-A1-1")
    assert run["live"] is True and run["surface"] == "factory"
    assert "_now" in body


def test_api_activity_log_tail_returns_last_lines(server, env_root):
    logs = activity.LOGS_DIR(env_root)
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "factory-A1-1.log").write_text("l1\nl2\nl3\n", encoding="utf-8")
    activity.start_run(env_root, id="factory-A1-1", surface="factory", title="d",
                       log_path=str(logs / "factory-A1-1.log"))
    body = _get_json(server, "/api/activity/factory-A1-1/log?tail=2")
    assert body["available"] is True
    assert body["lines"] == ["l2", "l3"]


def test_api_activity_log_unknown_id_not_available(server):
    body = _get_json(server, "/api/activity/does-not-exist/log")
    assert body["available"] is False and body["lines"] == []


def test_api_activity_log_rejects_traversal(server):
    port = server.server_address[1]
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/activity/..%2f..%2fsecret/log", timeout=5)
    assert e.value.code in (400, 403, 404)


def test_make_server_prunes_terminal_activity_past_retention(env_root):
    # A122: server start prunes terminal-past-retention activity records so
    # state/activity/ doesn't grow unbounded across restarts.
    activity.start_run(env_root, id="old", surface="factory", title="d", now=0.0)
    activity.finish_run(env_root, "old", "shipped", now=0.0)
    make_server(env_root, port=0)
    ids = {r["id"] for r in activity.read_all(env_root)}
    assert "old" not in ids


def test_events_fingerprint_reacts_to_activity(server, env_root):
    port = server.server_address[1]
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/events",
                                 headers={"Host": f"127.0.0.1:{port}"})
    # Long-lived SSE stream: the blocking readline()s below wait on the server's poll loop,
    # which is thread-starved under full-suite load — a tight timeout flakes here (unlike the
    # quick request/response tests). Generous headroom keeps it deterministic without changing
    # what's asserted.
    stream = urllib.request.urlopen(req, timeout=20)
    stream.readline()  # 'event: hello'
    stream.readline()  # 'data: {}'
    stream.readline()  # blank
    activity.start_run(env_root, id="factory-A9-9", surface="factory", title="d", pid=os.getpid())
    seen = ""
    for _ in range(40):
        line = stream.readline().decode()
        seen += line
        if "activity" in seen:
            break
    stream.close()
    assert "activity" in seen
