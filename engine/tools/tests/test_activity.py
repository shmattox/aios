import os, time
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
