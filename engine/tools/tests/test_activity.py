import contextlib, errno, json, os, subprocess, textwrap, time
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


# ─── Task 6: CLI seam — span-start / span-end / approve / resolve ───

def test_cli_span_start_prints_id_and_span_end_updates(tmp_path, capsys):
    activity.start_run(tmp_path, id="wf-cli-1", surface="workflow", title="t", now=0.0)
    rc = activity.main(["span-start", "--env-root", str(tmp_path), "--id", "wf-cli-1",
                        "--name", "audit", "--kind", "invoke_agent"])
    out = capsys.readouterr().out.strip()
    assert rc == 0 and out == "wf-cli-1#1"
    rc2 = activity.main(["span-end", "--env-root", str(tmp_path), "--id", "wf-cli-1",
                         "--span-id", out, "--status", "ok", "--in-tokens", "500",
                         "--out-tokens", "120", "--cost", "0.7"])
    s = activity.read_all(tmp_path)[0]["spans"][0]
    assert rc2 == 0 and s["status"] == "ok" and s["input_tokens"] == 500 and s["cost"] == 0.7


def test_cli_approve_and_resolve(tmp_path):
    activity.start_run(tmp_path, id="factory-cli-1", surface="factory", title="t", now=0.0)
    activity.main(["approve", "--env-root", str(tmp_path), "--id", "factory-cli-1",
                   "--kind", "gate", "--prompt", "needs you"])
    assert activity.read_all(tmp_path)[0]["status"] == "awaiting_approval"
    activity.main(["resolve", "--env-root", str(tmp_path), "--id", "factory-cli-1",
                   "--decision", "approved"])
    assert activity.read_all(tmp_path)[0]["status"] == "running"


# --- A124: concurrency-safe span writes -- N real OS processes racing one run record ---
# Real subprocesses, not threads: fan-out agents each shell out to this CLI as separate
# processes, so an in-process lock would prove nothing. These fail on the lockless
# read-modify-write (two readers see the same len(spans), mint the same id, and the later
# os.replace clobbers the earlier write).

def test_span_start_concurrent_processes_get_distinct_ids_zero_drops(tmp_path):
    activity.start_run(tmp_path, id="wf-race-1", surface="workflow", title="race", now=0.0)
    script = str(Path(activity.__file__))
    n = 12
    procs = [subprocess.Popen(
        [sys.executable, script, "span-start", "--env-root", str(tmp_path),
         "--id", "wf-race-1", "--name", "agent-%d" % i],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for i in range(n)]
    ids = []
    for p in procs:
        out, err = p.communicate(timeout=60)
        assert p.returncode == 0, err
        ids.append(out.strip())
    assert len(ids) == n
    assert len(set(ids)) == n, "duplicate span_ids minted: %r" % (ids,)
    rec = activity.read_all(tmp_path)[0]
    assert len(rec["spans"]) == n, "dropped spans: %d of %d landed" % (len(rec["spans"]), n)


def test_end_span_concurrent_processes_exact_run_cost(tmp_path):
    activity.start_run(tmp_path, id="wf-race-2", surface="workflow", title="race2", now=0.0)
    n = 10
    span_ids = [activity.start_span(tmp_path, "wf-race-2", name="a%d" % i, now=0.0) for i in range(n)]
    script = str(Path(activity.__file__))
    costs = [round(0.11 * (i + 1), 2) for i in range(n)]
    procs = [subprocess.Popen(
        [sys.executable, script, "span-end", "--env-root", str(tmp_path),
         "--id", "wf-race-2", "--span-id", sid, "--cost", str(cost)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for sid, cost in zip(span_ids, costs)]
    for p in procs:
        out, err = p.communicate(timeout=60)
        assert p.returncode == 0, err
    rec = activity.read_all(tmp_path)[0]
    assert all(s["status"] == "ok" for s in rec["spans"]), "an end_span update was lost"
    assert activity.run_cost(rec) == pytest.approx(sum(costs)), "run_cost inexact under concurrency"


def test_prune_removes_the_lock_sidecar(tmp_path):
    """A lock file outlives its record otherwise: prune would leave one .lock per pruned run
    accumulating in state/activity/ forever."""
    activity.start_run(tmp_path, id="wf-lock-prune", surface="workflow", title="t", now=0.0)
    activity.start_span(tmp_path, "wf-lock-prune", name="s", now=0.0)  # creates the .lock
    activity.finish_run(tmp_path, "wf-lock-prune", "ended", now=0.0)
    lock = activity.ACTIVITY_DIR(tmp_path) / "wf-lock-prune.json.lock"
    assert lock.exists(), "the mutators should have created a lock sidecar"
    assert activity.prune(tmp_path, retain_s=0, now=100000.0) == 1
    assert not lock.exists(), "prune left the .lock sidecar behind"


@pytest.fixture(autouse=True)
def _reset_lock_unsupported_latch():
    """`_lock_unsupported_marked` is a MODULE-level latch: correct in production, where each CLI
    invocation is its own short-lived process, but inside one pytest process it leaks across
    tests — the first test to trip ENOLCK would silently suppress the marker for every later one.
    Reset it per test so these are order-independent rather than accidentally passing."""
    activity._lock_unsupported_marked = False
    yield
    activity._lock_unsupported_marked = False


def test_unlockable_filesystem_does_not_stall_the_producer(tmp_path, monkeypatch):
    """A filesystem that cannot lock (ENOLCK on SMB/NFS/some FUSE mounts) must be detected
    on the FIRST attempt and fall through unlocked. Retrying a permanent error burns the whole
    deadline on every mutator call, which breaks this module's stated invariant that its I/O
    never blocks a producer's real work."""
    activity.start_run(tmp_path, id="wf-nolock", surface="workflow", title="t", now=0.0)

    calls = []

    def _boom(*a, **k):
        calls.append(1)
        raise OSError(errno.ENOLCK, "no locks available")

    if sys.platform == "win32":
        import msvcrt
        monkeypatch.setattr(msvcrt, "locking", _boom)
    else:
        import fcntl
        monkeypatch.setattr(fcntl, "flock", _boom)

    t0 = time.time()
    for _ in range(3):
        activity.heartbeat(tmp_path, "wf-nolock", now=1.0)
    elapsed = time.time() - t0
    # 3 calls x the 5s deadline = ~15s if a permanent error is retried as contention.
    assert calls, "the lock primitive was never called -- this test disarmed itself"
    assert elapsed < 1.0, "unlockable fs stalled the producer for %.2fs across 3 calls" % elapsed
    assert activity.read_all(tmp_path)[0]["heartbeat"] == 1.0  # and the write still happened


def test_start_span_racing_end_span_across_processes(tmp_path):
    """The realistic fan-out shape: agents opening and closing spans at the same time. The two
    other race tests each stress ONE mutator in isolation, so neither covers this."""
    activity.start_run(tmp_path, id="wf-race-3", surface="workflow", title="race3", now=0.0)
    pre = [activity.start_span(tmp_path, "wf-race-3", name="pre%d" % i, now=0.0) for i in range(6)]
    script = str(Path(activity.__file__))
    procs = []
    for i in range(6):
        procs.append(subprocess.Popen(
            [sys.executable, script, "span-start", "--env-root", str(tmp_path),
             "--id", "wf-race-3", "--name", "new%d" % i],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        procs.append(subprocess.Popen(
            [sys.executable, script, "span-end", "--env-root", str(tmp_path),
             "--id", "wf-race-3", "--span-id", pre[i], "--cost", "1.0"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
    for p in procs:
        out, err = p.communicate(timeout=60)
        assert p.returncode == 0, err
    rec = activity.read_all(tmp_path)[0]
    assert len(rec["spans"]) == 12, "dropped spans: %d of 12" % len(rec["spans"])
    assert len({s["span_id"] for s in rec["spans"]}) == 12, "duplicate span_ids"
    closed = [s for s in rec["spans"] if s["status"] == "ok"]
    assert len(closed) == 6, "lost end_span updates: %d of 6 closed" % len(closed)
    assert activity.run_cost(rec) == pytest.approx(6.0)


def test_contended_lock_still_writes_after_the_deadline(tmp_path, monkeypatch):
    """The degraded branch: when the lock is genuinely held by ANOTHER PROCESS past the deadline,
    the write proceeds unlocked rather than raising or dropping data -- the deliberate trade,
    since this module must never raise into or block a producer forever.

    Contends with a real subprocess holder and shrinks only the DEADLINE constant, so the real
    retry loop, handle handling and release path all still run. (A previous version of this test
    monkeypatched `_run_lock` itself into a nullcontext and passed even with the lock's entire
    body deleted -- it pinned nothing.)"""
    activity.start_run(tmp_path, id="wf-contended", surface="workflow", title="t", now=0.0)
    path = activity._rec_path(tmp_path, "wf-contended")
    holder_src = textwrap.dedent(
        """
        import sys, time
        p = sys.argv[1] + ".lock"
        fh = open(p, "a+b")
        fh.write(b"L"); fh.flush(); fh.seek(0)
        if sys.platform == "win32":
            import msvcrt; msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl; fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        print("held", flush=True)
        time.sleep(4)
        """
    )
    holder = subprocess.Popen([sys.executable, "-c", holder_src, str(path)],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"  # lock is genuinely taken
        monkeypatch.setattr(activity, "_LOCK_TIMEOUT_S", 0.3)
        t0 = time.time()
        sid = activity.start_span(tmp_path, "wf-contended", name="s", now=1.0)
        elapsed = time.time() - t0
        assert elapsed >= 0.3, "did not actually contend (%.3fs) -- the lock was not held" % elapsed
        assert elapsed < 3.0, "blocked far past its own deadline (%.2fs)" % elapsed
        assert sid == "wf-contended#1"                       # degraded, but it still wrote
        assert len(activity.read_all(tmp_path)[0]["spans"]) == 1
        # THE headline invariant: contention is the designed, self-limiting degradation, so it
        # must NOT leave the install-level "this filesystem cannot lock" marker. Without this,
        # moving the marker call onto the contended branch keeps the whole suite green.
        assert not (activity.ACTIVITY_DIR(tmp_path) / activity.LOCK_UNSUPPORTED_MARKER).exists(), \
            "contended-past-deadline must not be marked as an unlockable filesystem"
    finally:
        holder.kill()
        holder.wait(timeout=10)


def test_unlockable_filesystem_leaves_a_breadcrumb(tmp_path, monkeypatch):
    """A filesystem that cannot lock degrades SILENTLY and PERMANENTLY: every write from then on
    runs with zero concurrency protection and nothing anywhere says so. The stall it used to cause
    was at least a symptom someone would chase; removing the stall (the A124 review's CRITICAL)
    made the failure quieter, not rarer. So the permanent case drops one marker file.

    Only the PERMANENT case is marked. Losing a contended lock at the deadline is the designed,
    self-limiting degradation and would just make this noisy."""
    activity.start_run(tmp_path, id="wf-crumb", surface="workflow", title="t", now=0.0)
    marker = activity.ACTIVITY_DIR(tmp_path) / activity.LOCK_UNSUPPORTED_MARKER
    assert not marker.exists()

    def _boom(*a, **k):
        raise OSError(errno.ENOLCK, "no locks available")

    if sys.platform == "win32":
        import msvcrt
        monkeypatch.setattr(msvcrt, "locking", _boom)
    else:
        import fcntl
        monkeypatch.setattr(fcntl, "flock", _boom)

    activity.heartbeat(tmp_path, "wf-crumb", now=1.0)
    assert marker.exists(), "an unlockable filesystem left no trace at all"
    body = marker.read_text(encoding="utf-8")
    assert "ENOLCK" in body or str(errno.ENOLCK) in body, body   # names WHY, not just that
    assert activity.read_all(tmp_path)[0]["heartbeat"] == 1.0    # and the write still landed


def test_breadcrumb_not_written_when_locking_works(tmp_path):
    """The marker must mean something: a healthy install never grows one."""
    activity.start_run(tmp_path, id="wf-nocrumb", surface="workflow", title="t", now=0.0)
    activity.start_span(tmp_path, "wf-nocrumb", name="s", now=0.0)
    activity.heartbeat(tmp_path, "wf-nocrumb", now=1.0)
    assert not (activity.ACTIVITY_DIR(tmp_path) / activity.LOCK_UNSUPPORTED_MARKER).exists()


def test_breadcrumb_failure_never_raises(tmp_path, monkeypatch):
    """The breadcrumb is subject to the same contract as everything else here: if WRITING the
    marker fails, the producer must not see it."""
    activity.start_run(tmp_path, id="wf-crumbfail", surface="workflow", title="t", now=0.0)

    def _boom(*a, **k):
        raise OSError(errno.ENOLCK, "no locks available")

    if sys.platform == "win32":
        import msvcrt
        monkeypatch.setattr(msvcrt, "locking", _boom)
    else:
        import fcntl
        monkeypatch.setattr(fcntl, "flock", _boom)
    monkeypatch.setattr(activity, "_mark_lock_unsupported",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("marker disk full")))

    activity.heartbeat(tmp_path, "wf-crumbfail", now=1.0)          # must not raise
    assert activity.read_all(tmp_path)[0]["heartbeat"] == 1.0


def test_breadcrumb_written_once_and_repaired_if_empty(tmp_path, monkeypatch):
    """Two guarantees the docstring claims and nothing pinned: a second mutator call does not
    rewrite the marker, and a torn/empty marker is REPAIRED rather than latching content-free."""
    activity.start_run(tmp_path, id="wf-once", surface="workflow", title="t", now=0.0)

    def _boom(*a, **k):
        raise OSError(errno.ENOLCK, "no locks available")

    if sys.platform == "win32":
        import msvcrt
        monkeypatch.setattr(msvcrt, "locking", _boom)
    else:
        import fcntl
        monkeypatch.setattr(fcntl, "flock", _boom)

    marker = activity.ACTIVITY_DIR(tmp_path) / activity.LOCK_UNSUPPORTED_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("", encoding="utf-8")          # a torn first write
    activity.heartbeat(tmp_path, "wf-once", now=1.0)
    assert marker.read_text(encoding="utf-8").strip(), "an empty marker latched and was never repaired"

    body = marker.read_text(encoding="utf-8")
    activity.heartbeat(tmp_path, "wf-once", now=2.0)
    assert marker.read_text(encoding="utf-8") == body, "marker rewritten on a second call"


def test_prune_preserves_the_lock_unsupported_marker(tmp_path):
    """The marker describes the MOUNT, not any run, so a prune that reaps terminal records must
    leave it. Unpinned before: widening prune() to delete non-.json entries stayed green."""
    activity.start_run(tmp_path, id="wf-prunemark", surface="workflow", title="t", now=0.0)
    activity.finish_run(tmp_path, "wf-prunemark", "ended", now=0.0)
    marker = activity.ACTIVITY_DIR(tmp_path) / activity.LOCK_UNSUPPORTED_MARKER
    marker.write_text("unlockable\n", encoding="utf-8")
    assert activity.prune(tmp_path, retain_s=0, now=100000.0) == 1
    assert marker.exists(), "prune ate the install-level marker"
