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
