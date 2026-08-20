import os, sys, io, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "dashboard"))
import content_state as c
import servers_state as s


def test_content_summary_maps_queue_stages(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "queue.json").write_text(json.dumps({"queue": [
        {"stage": "sorted", "kb": "gm"}, {"stage": "sorted", "kb": "personal"},
        {"stage": "awaiting", "kb": "familyoffice"}, {"stage": "awaiting", "kb": "gm"},
        {"stage": "shipped", "kb": "gm"}, {"stage": "rejected", "kb": "gm"},
        {"stage": "awaiting", "kb": "gm", "retired": True},   # retired excluded
    ]}), encoding="utf-8")
    d = c.summary(str(tmp_path))
    nodes = {n["id"]: n["count"] for n in d["nodes"]}
    assert nodes["sort"] == 2 and nodes["gate"] == 2 and nodes["garden"] == 0
    assert d["shipped"] == 1 and d["rejected"] == 1
    assert d["by_kb"] == {"familyoffice": 1, "gm": 1}   # awaiting only, retired dropped


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
