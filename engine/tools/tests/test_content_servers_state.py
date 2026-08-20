import os, sys, io, json, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "dashboard"))
import content_state as c
import servers_state as s


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_queue(tmp_path, items):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "queue.json").write_text(json.dumps({"queue": items}), encoding="utf-8")


def test_content_summary_backlog_and_throughput(tmp_path):
    now = datetime.datetime.now(datetime.timezone.utc)
    recent, old = _iso(now - datetime.timedelta(hours=2)), _iso(now - datetime.timedelta(days=9))
    _write_queue(tmp_path, [
        {"stage": "captured", "kb": "gm"},                                   # capture backlog
        {"stage": "sorted", "kb": "gm"}, {"stage": "sorted", "kb": "personal"},  # sort backlog
        {"stage": "awaiting", "kb": "familyoffice"}, {"stage": "awaiting", "kb": "gm"},  # gate backlog
        # entered awaiting 2h ago -> counts as ingest throughput; shipped just now -> garden throughput
        {"stage": "shipped", "kb": "gm", "history": [
            {"stage": "awaiting", "ts": recent}, {"stage": "shipped", "ts": recent}]},
        # shipped long ago -> NOT recent throughput (but still a lifetime shipped)
        {"stage": "shipped", "kb": "gm", "history": [{"stage": "shipped", "ts": old}]},
        {"stage": "rejected", "kb": "gm"},
        {"stage": "awaiting", "kb": "gm", "retired": True},   # retired excluded everywhere
    ])
    d = c.summary(str(tmp_path))
    nodes = {n["id"]: n for n in d["nodes"]}
    assert nodes["capture"]["count"] == 1 and nodes["capture"]["kind"] == "backlog"
    assert nodes["sort"]["count"] == 2
    assert nodes["gate"]["count"] == 2
    assert nodes["ingest"]["count"] == 1 and nodes["ingest"]["kind"] == "phase"   # entered awaiting 2h ago
    assert nodes["garden"]["count"] == 1                                          # shipped 2h ago (not 9d)
    assert d["shipped"] == 2 and d["rejected"] == 1 and d["sorted"] == 2
    assert d["by_kb"] == {"familyoffice": 1, "gm": 1}   # awaiting only, retired dropped


def test_content_stage_detail_backlog_and_unknown(tmp_path):
    _write_queue(tmp_path, [
        {"stage": "sorted", "kb": "gm", "id": "2026-07-30-supabase-invoice-ljdtac-00015",
         "source": "gmail", "captured_utc": "2026-07-30T00:00:00Z"},
        {"stage": "awaiting", "kb": "fo", "id": "x"},
    ])
    d = c.stage_detail(str(tmp_path), "sort")
    assert d["count"] == 1 and d["items"][0]["title"] == "supabase invoice"   # date + slug stripped
    assert c.stage_detail(str(tmp_path), "bogus") is None                     # -> 404


def test_content_summary_missing_queue_never_raises(tmp_path):
    (tmp_path / "state").mkdir()
    d = c.summary(str(tmp_path))
    assert d["shipped"] == 0 and all(n["count"] == 0 for n in d["nodes"])


def test_servers_dedupe_and_repo_from_prefix(tmp_path):
    # env-root launch.json declares an open-place world; open-place's own launch.json declares it too.
    (tmp_path / "profile").mkdir()
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "launch.json").write_text(json.dumps({"configurations": [
        {"name": "hub", "port": 5174, "runtimeExecutable": "npm",
         "runtimeArgs": ["run", "dev", "--prefix", "Projects/open-place/worlds/hub"]}]}), encoding="utf-8")
    proj = tmp_path / "Projects" / "open-place" / ".claude"
    proj.mkdir(parents=True)
    (proj / "launch.json").write_text(json.dumps({"configurations": [
        {"name": "hub", "port": 5174}]}), encoding="utf-8")
    out = s.servers(str(tmp_path))
    assert len(out) == 1                          # deduped by (name, port)
    assert out[0]["repo"] == "open-place"         # derived from --prefix, not "env-ops"
    assert out[0]["up"] is False                  # nothing listening on 5174


def test_servers_no_launch_json_never_raises(tmp_path):
    (tmp_path / "profile").mkdir()
    assert s.servers(str(tmp_path)) == []
