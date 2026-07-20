"""A63 dashboard server core — env-root resolve + Host/token security (task 1)."""
import json
import threading
import urllib.request
import urllib.error
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))
from dashboard_server import resolve_env_root, make_server  # noqa: E402


@pytest.fixture()
def env_root(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "profile").mkdir()
    return tmp_path


@pytest.fixture()
def server(env_root):
    srv = make_server(env_root, port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


def _get(srv, path, host=None):
    port = srv.server_address[1]
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if host:
        req.add_header("Host", host)
    return urllib.request.urlopen(req, timeout=5)


def test_resolve_env_root_walks_up(env_root):
    deep = env_root / "Projects" / "x"
    deep.mkdir(parents=True)
    assert resolve_env_root(deep) == env_root


def test_resolve_env_root_missing_exits(tmp_path):
    with pytest.raises(SystemExit):
        resolve_env_root(tmp_path)


def test_health_ok(server):
    body = json.loads(_get(server, "/api/health").read())
    assert body["ok"] is True


def test_bad_host_rejected(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(server, "/api/health", host="evil.example.com")
    assert e.value.code == 403


def test_index_injects_token(server):
    html = _get(server, "/").read().decode("utf-8")
    assert server.token in html
    assert "{{TOKEN}}" not in html


def test_post_requires_token(server):
    port = server.server_address[1]
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/action/anything", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 401


def test_head_bad_host_rejected(server):
    # HEAD inherits static serving from the base class — it must run the same Host gate (folded
    # from the task-1 review, before the action layer builds on this class).
    port = server.server_address[1]
    req = urllib.request.Request(f"http://127.0.0.1:{port}/index.html", method="HEAD")
    req.add_header("Host", "evil.example.com")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 403
