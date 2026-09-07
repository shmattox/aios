"""D Task 5 — the cockpit is installable to a home screen.

Three properties, each with a reason it is a test and not a glance:

1. The manifest is served with the RIGHT MIME TYPE. `SimpleHTTPRequestHandler` guesses from the
   extension and Python's mimetypes table has no `.webmanifest`, so without an explicit mapping it
   ships `application/octet-stream` and every browser silently ignores the manifest. The page still
   renders, the install prompt just never appears — a failure with no error anywhere.

2. The served HTML LINKS the manifest and registers the worker. `_index()` builds the HTML by
   substitution, so a file on disk that nothing references is dead weight.

3. The Host gate still covers all of it. Static assets ride `super().do_GET()`, which is behind
   `_host_ok()` — but a new file is exactly the kind of thing that gets special-cased past a guard
   later, so the guard is asserted on the new routes too, not assumed from the old ones.

Deliberately NOT tested: that the service worker caches anything. It must not. The server sends
`Cache-Control: no-store` on every response by design (a cached old app.js against a new
index.html renders blank), and a cockpit serving stale queue state from a worker cache is worse
than no cockpit. The worker exists to make the app installable, nothing else.
"""
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))
from dashboard_server import make_server  # noqa: E402


def _env(tmp_path):
    prof = tmp_path / "profile"
    prof.mkdir(parents=True, exist_ok=True)
    (prof / "connectors.yaml").write_text(
        "vault:\n  live_root: \"SecondBrain\"\n  live_kb_map:\n    personal: \"01_Personal\"\n",
        encoding="utf-8")
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _serve(env_root):
    srv = make_server(env_root, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _get(srv, route, host_header=None):
    port = srv.server_address[1]
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, route))
    req.add_header("Host", host_header or ("127.0.0.1:%d" % port))
    return urllib.request.urlopen(req, timeout=10)


def test_manifest_is_served_as_a_manifest(tmp_path):
    """Wrong MIME type = silently ignored manifest = no install prompt, and no error to notice."""
    srv = _serve(_env(tmp_path))
    try:
        with _get(srv, "/manifest.webmanifest") as r:
            assert r.status == 200
            assert r.headers.get_content_type() == "application/manifest+json"
            m = json.loads(r.read().decode("utf-8"))
    finally:
        srv.shutdown()

    assert m["display"] == "standalone", "without standalone it opens in a browser tab, not as an app"
    assert m["start_url"] == "/"
    assert m.get("name") and m.get("short_name")
    assert m.get("icons"), "no icons means no home-screen tile"


def test_index_links_the_manifest_and_registers_the_worker(tmp_path):
    """A manifest on disk that the HTML never references is dead weight."""
    srv = _serve(_env(tmp_path))
    try:
        with _get(srv, "/") as r:
            html = r.read().decode("utf-8")
    finally:
        srv.shutdown()
    assert 'rel="manifest"' in html and "manifest.webmanifest" in html
    assert "serviceWorker" in html and "/sw.js" in html


def test_service_worker_is_served_as_javascript(tmp_path):
    """A worker served as octet-stream is refused by the browser at registration."""
    srv = _serve(_env(tmp_path))
    try:
        with _get(srv, "/sw.js") as r:
            assert r.status == 200
            assert r.headers.get_content_type() in ("text/javascript", "application/javascript")
            body = r.read().decode("utf-8")
    finally:
        srv.shutdown()
    assert "fetch" in body, "Chrome's install criteria require a fetch handler"
    # The worker must not build a cache: the server is no-store by design.
    assert "caches.open" not in body and "cache.put" not in body


@pytest.mark.parametrize("route", ["/manifest.webmanifest", "/sw.js"])
def test_new_routes_are_behind_the_host_gate(tmp_path, route):
    """New static files are exactly what gets special-cased past a guard later. Assert, don't assume."""
    srv = _serve(_env(tmp_path))
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(srv, route, host_header="evil.example.com")
        assert e.value.code == 403
    finally:
        srv.shutdown()
