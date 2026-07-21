import json, threading, urllib.request, urllib.error
from pathlib import Path
import pytest, sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))
from dashboard_server import make_server

STUB = """import sys, json
print(json.dumps({"argv": sys.argv[1:]}))
"""


@pytest.fixture()
def env_root(tmp_path):
    (tmp_path / "state").mkdir()
    prof = tmp_path / "profile"
    prof.mkdir()
    (prof / "connectors.yaml").write_text(
        "vault:\n  live_root: \"SecondBrain\"\n  live_kb_map:\n    personal: \"01_Personal\"\n",
        encoding="utf-8")
    tools = tmp_path / "stub_tools"
    tools.mkdir()
    for name in ("ship.py", "brief_session.py"):
        (tools / name).write_text(STUB, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def server(env_root):
    srv = make_server(env_root, port=0)
    srv.tools_dir = env_root / "stub_tools"
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


def _post(srv, path, payload, token=None):
    port = srv.server_address[1]
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(payload).encode("utf-8"),
                                 method="POST")
    req.add_header("X-Aios-Token", token if token is not None else srv.token)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def test_unlisted_action_403(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(server, "/api/action/rm_rf", {})
    assert e.value.code == 403


def test_bad_param_400(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(server, "/api/action/gate_ship", {"id": "x; rm -rf /"})
    assert e.value.code == 400


def test_gate_ship_builds_real_argv(server, env_root):
    out = _post(server, "/api/action/gate_ship", {"id": "q1"})
    argv = json.loads(out["stdout"])["argv"]
    assert argv[0] == "ship"
    assert "--id" in argv and "q1" in argv
    assert "--human-approved" in argv
    assert str(env_root / "SecondBrain") in argv
    assert out["ok"] is True


def test_gate_reject(server):
    out = _post(server, "/api/action/gate_reject", {"id": "q1", "reason": "dup"})
    argv = json.loads(out["stdout"])["argv"]
    assert argv[0] == "reject" and "--decided-by" in argv and "human" in argv


def test_walk_decision(server):
    out = _post(server, "/api/action/walk_decision",
                {"item_id": "t1", "station": "act", "choice": "done", "action": "closed it"})
    argv = json.loads(out["stdout"])["argv"]
    assert argv[0] == "record_decision" and "t1" in argv


def test_veto_revert_rejects_bad_sha(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(server, "/api/action/veto_revert", {"repo": ".", "sha": "not-a-sha"})
    assert e.value.code == 400


def test_veto_revert_rejects_outside_repo(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(server, "/api/action/veto_revert",
              {"repo": "../../elsewhere", "sha": "a" * 40})
    assert e.value.code == 400
