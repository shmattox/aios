import json, os, time
from pathlib import Path
import pytest, sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # engine/tools on path
import activity


def test_start_run_writes_readable_record(tmp_path):
    activity.start_run(tmp_path, id="factory-A1-100", surface="factory",
                       title="Draining A1", item_ids=["A1"], pid=os.getpid(),
                       log_path=str(tmp_path / "state/activity/logs/factory-A1-100.log"),
                       now=1000.0)
    recs = activity.read_all(tmp_path)
    assert len(recs) == 1
    r = recs[0]
    assert r["id"] == "factory-A1-100"
    assert r["surface"] == "factory"
    assert r["status"] == "running"
    assert r["item_ids"] == ["A1"]
    assert r["started"] == 1000.0 and r["heartbeat"] == 1000.0


def test_is_live_pid_alive_current_process(tmp_path):
    r = activity.start_run(tmp_path, id="s-1", surface="session", title="sess",
                           pid=os.getpid(), now=0.0)
    # heartbeat cold (now is far ahead) but pid alive -> still live
    assert activity.is_live(r, now=10_000.0) is True


def test_is_live_stale_when_pid_dead_and_heartbeat_cold(tmp_path):
    r = activity.start_run(tmp_path, id="s-2", surface="session", title="sess",
                           pid=2_000_000_000, now=0.0)  # pid that cannot exist
    assert activity._pid_alive(2_000_000_000) is False
    assert activity.is_live(r, now=10_000.0) is False


def test_finish_run_sets_terminal_and_not_live(tmp_path):
    activity.start_run(tmp_path, id="f-1", surface="factory", title="d", pid=os.getpid(), now=0.0)
    activity.finish_run(tmp_path, "f-1", "shipped", tokens=1234, cost_usd=0.5, now=5.0)
    r = activity.read_all(tmp_path)[0]
    assert r["status"] == "shipped" and r["ended"] == 5.0 and r["tokens"] == 1234
    assert activity.is_live(r, now=6.0) is False  # terminal is never live


def test_session_run_id_is_safe(tmp_path):
    rid = activity.session_run_id("open place/dev", 12648)
    assert activity._safe_id(rid) and rid.endswith("-12648")


def test_prune_removes_terminal_past_retention_only(tmp_path):
    activity.start_run(tmp_path, id="old", surface="factory", title="d", now=0.0)
    activity.finish_run(tmp_path, "old", "shipped", now=0.0)
    activity.start_run(tmp_path, id="live", surface="factory", title="d", pid=os.getpid(), now=0.0)
    removed = activity.prune(tmp_path, retain_s=100, now=1000.0)  # old ended long ago
    ids = {r["id"] for r in activity.read_all(tmp_path)}
    assert removed == 1 and ids == {"live"}


def test_run_with_activity_records_stages(tmp_path):
    seen = []

    def stage(name):
        seen.append(name)

    ok = activity.run_with_activity(
        tmp_path, id="pipeline-2026-08-01", title="Nightly pipeline",
        stages=[("capture", lambda: stage("capture")),
                ("sort", lambda: stage("sort"))], now=0.0)
    r = activity.read_all(tmp_path)[0]
    assert ok and seen == ["capture", "sort"]
    assert r["status"] == "ended" and r["surface"] == "pipeline"


def test_run_with_activity_finishes_failed_and_reraises(tmp_path):
    def boom():
        raise ValueError("stage blew up")

    with pytest.raises(ValueError):
        activity.run_with_activity(tmp_path, id="pipeline-fail", title="Nightly pipeline",
                                   stages=[("capture", boom)], now=0.0)
    r = activity.read_all(tmp_path)[0]
    assert r["status"] == "failed" and r["ended"] is not None


# ─── CLI (the seam the deploy runners call to bracket each stage's `claude -p`) ───

def test_cli_start_records_running_pipeline_with_detail_and_pid(tmp_path):
    rc = activity.main(["start", "--env-root", str(tmp_path), "--id", "pipeline-ingest-100",
                        "--surface", "pipeline", "--title", "aios-ingest",
                        "--detail", "ingest", "--pid", str(os.getpid()),
                        "--repo", "aios", "--item-ids", "A1,A2"])
    r = activity.read_all(tmp_path)[0]
    assert rc == 0
    assert r["status"] == "running" and r["surface"] == "pipeline"
    assert r["detail"] == "ingest" and r["pid"] == os.getpid()
    assert r["repo"] == "aios" and r["item_ids"] == ["A1", "A2"]
    assert r["live"] is True  # own live pid => live without a periodic heartbeat


def test_cli_finish_sets_terminal_status(tmp_path):
    activity.main(["start", "--env-root", str(tmp_path), "--id", "pipeline-gate-100",
                   "--surface", "pipeline", "--title", "aios-gate-auto", "--pid", str(os.getpid())])
    rc = activity.main(["finish", "--env-root", str(tmp_path), "--id", "pipeline-gate-100",
                        "--status", "failed"])
    r = activity.read_all(tmp_path)[0]
    assert rc == 0 and r["status"] == "failed" and r["ended"] is not None
    assert r["live"] is False  # terminal is never live even with a live pid


def test_cli_heartbeat_updates_tokens_and_detail(tmp_path):
    activity.main(["start", "--env-root", str(tmp_path), "--id", "pipeline-x-100",
                   "--surface", "pipeline", "--title", "t"])
    activity.main(["heartbeat", "--env-root", str(tmp_path), "--id", "pipeline-x-100",
                   "--detail", "sort", "--tokens", "1234"])
    r = activity.read_all(tmp_path)[0]
    assert r["detail"] == "sort" and r["tokens"] == 1234


def test_cli_prune_removes_terminal_past_retention(tmp_path):
    activity.start_run(tmp_path, id="old", surface="pipeline", title="t", now=0.0)
    activity.finish_run(tmp_path, "old", "ended", now=0.0)
    activity.start_run(tmp_path, id="fresh", surface="pipeline", title="t")
    rc = activity.main(["prune", "--env-root", str(tmp_path), "--retain-s", "100"])
    ids = {r["id"] for r in activity.read_all(tmp_path)}
    assert rc == 0 and ids == {"fresh"}


def test_cli_invalid_surface_is_noop_never_breaks_runner(tmp_path):
    # a bad surface must not crash the runner: no record, clean exit
    rc = activity.main(["start", "--env-root", str(tmp_path), "--id", "bad-1",
                        "--surface", "nonsense", "--title", "t"])
    assert rc == 0 and activity.read_all(tmp_path) == []


def test_cli_no_subcommand_is_usage_error(tmp_path):
    assert activity.main([]) == 2


def test_cli_swallows_handler_exception_never_breaks_runner(tmp_path, monkeypatch):
    # THE contract: a runtime failure inside a handler must exit 0, never propagate a
    # traceback/nonzero into the scheduled runner. Force start_run to raise and assert exit 0.
    def boom(*a, **k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(activity, "start_run", boom)
    rc = activity.main(["start", "--env-root", str(tmp_path), "--id", "pipeline-x-1",
                        "--surface", "pipeline", "--title", "t"])
    assert rc == 0


def test_cli_finish_defaults_to_ended_when_no_status(tmp_path):
    activity.start_run(tmp_path, id="pipeline-d-1", surface="pipeline", title="t", pid=os.getpid())
    activity.main(["finish", "--env-root", str(tmp_path), "--id", "pipeline-d-1"])
    r = activity.read_all(tmp_path)[0]
    assert r["status"] == "ended" and r["ended"] is not None


def test_cli_heartbeat_cost_maps_to_cost_usd(tmp_path):
    activity.start_run(tmp_path, id="pipeline-c-1", surface="pipeline", title="t")
    activity.main(["heartbeat", "--env-root", str(tmp_path), "--id", "pipeline-c-1", "--cost", "0.42"])
    assert activity.read_all(tmp_path)[0]["cost_usd"] == 0.42


def test_start_run_accepts_detail_in_one_write(tmp_path):
    activity.start_run(tmp_path, id="pipeline-det-1", surface="pipeline", title="t", detail="ingest")
    assert activity.read_all(tmp_path)[0]["detail"] == "ingest"


def test_finish_and_heartbeat_reject_traversal_id(tmp_path, monkeypatch):
    # an unsafe --id must never resolve _rec_path outside state/activity/: guard short-circuits
    # BEFORE any file read/write. If it didn't, _read_json/_atomic_write would touch ../evil.json.
    calls = []
    monkeypatch.setattr(activity, "_read_json", lambda p: calls.append(p) or None)
    activity.finish_run(tmp_path, "../../evil", "ended")
    activity.heartbeat(tmp_path, "../../evil", detail="x")
    assert calls == []  # neither reached the filesystem


def test_prune_skips_log_path_for_unsafe_record_id(tmp_path, monkeypatch):
    # a crafted record with a traversal id must not drive os.remove outside LOGS_DIR
    import os as _os
    activity.ACTIVITY_DIR(tmp_path).mkdir(parents=True)
    (activity.ACTIVITY_DIR(tmp_path) / "rec.json").write_text(
        json.dumps({"id": "../../../etc/passwd", "status": "ended", "ended": 0.0}))
    removed_paths = []
    real_remove = _os.remove
    monkeypatch.setattr(activity.os, "remove", lambda p: removed_paths.append(str(p)) or real_remove(p))
    activity.prune(tmp_path, retain_s=1, now=1000.0)
    # only the in-dir json is removed; no path built from the unsafe id
    assert all("etc" not in p and "passwd" not in p for p in removed_paths)


# ─── Run/span data contract (Task 1: additive record fields on start_run) ───

def test_start_run_carries_span_tree_fields(tmp_path):
    activity.start_run(tmp_path, id="wf-recon-1", surface="workflow", title="vault-reconcile",
                       parent_id="session-open-place-1", now=1000.0)
    r = activity.read_all(tmp_path)[0]
    assert r["parent_id"] == "session-open-place-1"
    assert r["root"] is False            # has a parent -> not a root run
    assert r["spans"] == []
    assert r["input_tokens"] == 0 and r["output_tokens"] == 0
    assert r["pending_approval"] is None


def test_start_run_root_defaults_true_without_parent(tmp_path):
    activity.start_run(tmp_path, id="factory-A1-1", surface="factory", title="d", now=0.0)
    assert activity.read_all(tmp_path)[0]["root"] is True


# ─── Task 2: start_span / end_span ───

def test_start_and_end_span(tmp_path):
    activity.start_run(tmp_path, id="wf-1", surface="workflow", title="reconcile", now=0.0)
    root = activity.start_span(tmp_path, "wf-1", name="invoke_workflow", now=0.0)
    child = activity.start_span(tmp_path, "wf-1", name="audit", kind="invoke_agent",
                                parent_span_id=root, now=1.0)
    assert root == "wf-1#1" and child == "wf-1#2"
    activity.end_span(tmp_path, "wf-1", child, status="ok",
                      input_tokens=800, output_tokens=200, cost=0.9, now=5.0)
    spans = activity.read_all(tmp_path)[0]["spans"]
    assert len(spans) == 2
    c = spans[1]
    assert c["parent_span_id"] == root and c["kind"] == "invoke_agent"
    assert c["status"] == "ok" and c["end"] == 5.0
    assert c["input_tokens"] == 800 and c["output_tokens"] == 200 and c["cost"] == 0.9


def test_start_span_bad_id_returns_none_no_write(tmp_path):
    assert activity.start_span(tmp_path, "../evil", name="x") is None


# ─── Task 3: request_approval / resolve_approval + awaiting_approval status ───

def test_request_and_resolve_approval(tmp_path):
    activity.start_run(tmp_path, id="factory-PS274-1", surface="factory", title="d",
                       pid=os.getpid(), now=0.0)
    activity.request_approval(tmp_path, "factory-PS274-1", kind="gate",
                              prompt="economics wire-format", resume_token="tok-1", now=1.0)
    r = activity.read_all(tmp_path)[0]
    assert r["status"] == "awaiting_approval"
    assert r["pending_approval"]["awaiting"] is True
    assert r["pending_approval"]["prompt"] == "economics wire-format"
    assert r["pending_approval"]["resume_token"] == "tok-1"
    assert activity.is_live(r, now=2.0) is False       # blocked, not live
    assert activity.prune(tmp_path, retain_s=0, now=1e9) == 0   # not terminal -> never pruned

    activity.resolve_approval(tmp_path, "factory-PS274-1", decision="approved", now=3.0)
    r2 = activity.read_all(tmp_path)[0]
    assert r2["status"] == "running"
    assert r2["pending_approval"]["awaiting"] is False
    assert r2["pending_approval"]["decision"] == "approved" and r2["pending_approval"]["responded_at"] == 3.0


# ─── Task 4: run_cost — derived rollup ───

def test_run_cost_sums_span_costs(tmp_path):
    activity.start_run(tmp_path, id="wf-2", surface="workflow", title="t", now=0.0)
    a = activity.start_span(tmp_path, "wf-2", name="a", now=0.0)
    b = activity.start_span(tmp_path, "wf-2", name="b", now=0.0)
    activity.end_span(tmp_path, "wf-2", a, cost=0.9, now=1.0)
    activity.end_span(tmp_path, "wf-2", b, cost=0.6, now=1.0)  # b still costs even if not ended-with-cost elsewhere
    rec = activity.read_all(tmp_path)[0]
    assert activity.run_cost(rec) == pytest.approx(1.5)


def test_run_cost_ignores_none_and_empty(tmp_path):
    assert activity.run_cost({"spans": []}) == 0.0
    assert activity.run_cost({"spans": [{"cost": None}, {"cost": 0.4}]}) == pytest.approx(0.4)


# ─── Task 5: build_graph — the renderable projection ───

def test_build_graph_projects_span_tree_and_fanout(tmp_path):
    # a session invokes a workflow whose coordinator fans out to two agents
    activity.start_run(tmp_path, id="session-1", surface="session", title="s", now=0.0)
    inv = activity.start_span(tmp_path, "session-1", name="invoke_workflow", now=0.0)
    activity.start_run(tmp_path, id="wf-1", surface="workflow", title="reconcile",
                       parent_id="session-1", now=0.0)
    coord = activity.start_span(tmp_path, "wf-1", name="coordinator",
                                parent_span_id=inv, now=0.0)          # cross-run edge
    activity.start_span(tmp_path, "wf-1", name="agent-a", parent_span_id=coord, now=0.0)
    activity.start_span(tmp_path, "wf-1", name="agent-b", parent_span_id=coord, now=0.0)

    g = activity.build_graph(activity.read_all(tmp_path))
    ids = {n["id"] for n in g["nodes"]}
    assert ids == {"session-1#1", "wf-1#1", "wf-1#2", "wf-1#3"}
    edges = {(e["source"], e["target"]) for e in g["edges"]}
    assert edges == {("session-1#1", "wf-1#1"),   # session -> workflow (fan-in point)
                     ("wf-1#1", "wf-1#2"),         # coordinator -> agent-a
                     ("wf-1#1", "wf-1#3")}          # coordinator -> agent-b
