"""A109 task 2 — /api/board discovery + lanes."""
import json
import threading
import urllib.request
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))
from dashboard_server import make_server  # noqa: E402


@pytest.fixture()
def env_root(tmp_path):
    (tmp_path / "state" / "factory").mkdir(parents=True)
    (tmp_path / "profile").mkdir()
    # brief cache with one held row → FamilyOffice needs_you card
    (tmp_path / "state" / "brief-cache.json").write_text(json.dumps({
        "held": [{"id": "q1", "kb": "familyoffice", "lane": "review",
                  "title": "Bayview refi terms", "draft_path": "x.md"}],
    }), encoding="utf-8")
    (tmp_path / "state" / "factory" / "standup.json").write_text(json.dumps({
        "groups": {"needs-you": [{"repo": "demo", "id": "D2", "title": "t"}]},
    }), encoding="utf-8")
    # env-ops backlog + one repo backlog
    (tmp_path / "BACKLOG.md").write_text("- [ ] **H1** — env item [GATE: human]\n", encoding="utf-8")
    repo = tmp_path / "Projects" / "demo"
    repo.mkdir(parents=True)
    (repo / "BACKLOG.md").write_text(
        "- [ ] **D1** — building ▶\n- [ ] **D2** — quiet item\n- ◷ **D3 — seed.**\n",
        encoding="utf-8")
    return tmp_path


@pytest.fixture()
def server(env_root):
    srv = make_server(env_root, port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


def _get(srv, path):
    port = srv.server_address[1]
    return json.loads(urllib.request.urlopen(
        f"http://127.0.0.1:{port}{path}", timeout=5).read())


def test_board_lanes_discovered(server):
    body = _get(server, "/api/board")
    lanes = {l["key"]: l for l in body["lanes"]}
    assert "familyoffice" in lanes and lanes["familyoffice"]["kind"] == "silo"
    assert "env-ops" in lanes and "demo" in lanes
    # a new repo with a backlog appears without config (acceptance)
    assert lanes["demo"]["kind"] == "repo"


def test_board_station_placement(server):
    lanes = {l["key"]: l for l in _get(server, "/api/board")["lanes"]}
    demo = lanes["demo"]["cells"]
    assert [c["id"] for c in demo["in_motion"]] == ["D1"]
    assert [c["id"] for c in demo["needs_you"]] == ["D2"]   # standup needs-you override
    assert [c["id"] for c in demo["incoming"]] == ["D3"]
    assert [c["id"] for c in lanes["env-ops"]["cells"]["needs_you"]] == ["H1"]
    fo = lanes["familyoffice"]["cells"]["needs_you"]
    assert fo and fo[0]["title"] == "Bayview refi terms" and fo[0]["draft_index"] == 0
