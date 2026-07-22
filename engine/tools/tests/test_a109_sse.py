"""A109 task 5 — SSE change events on the stdlib server."""
import json
import socket
import threading
import time
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))
import dashboard_server
from dashboard_server import make_server  # noqa: E402


@pytest.fixture()
def env_root(tmp_path):
    (tmp_path / "state" / "factory").mkdir(parents=True)
    (tmp_path / "profile").mkdir()
    (tmp_path / "state" / "brief-cache.json").write_text("{}", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def server(env_root, monkeypatch):
    monkeypatch.setattr(dashboard_server, "SSE_POLL_S", 0.05)
    srv = make_server(env_root, port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


def test_sse_emits_change_on_touch(server, env_root):
    port = server.server_address[1]
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(b"GET /api/events HTTP/1.0\r\nHost: 127.0.0.1:%d\r\n\r\n" % port)
    s.settimeout(5)
    buf = b""
    while b"event: hello" not in buf:
        buf += s.recv(4096)
    assert b"text/event-stream" in buf
    time.sleep(0.1)  # let the first fingerprint settle
    p = env_root / "state" / "brief-cache.json"
    p.write_text(json.dumps({"x": 1}), encoding="utf-8")
    deadline = time.time() + 5
    while b"event: change" not in buf and time.time() < deadline:
        try:
            buf += s.recv(4096)
        except socket.timeout:
            break
    s.close()
    assert b"event: change" in buf and b"brief" in buf
