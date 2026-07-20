#!/usr/bin/env python3
"""A63 dashboard server: static Linear-style UI + read API + allowlisted gated
actions over the AIOS state surfaces. Server holds ZERO write logic — every
write shells out to an existing gated CLI.  # see A63 spec

Security (mandatory): 127.0.0.1 bind, exact Host validation, per-start token
on every POST (DNS-rebinding defense).
"""
import json
import os
import secrets
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
UI_DIR = HERE / "ui"
sys.path.insert(0, str(HERE.parent / "tools"))


def resolve_env_root(start=None):
    p = Path(start or os.getcwd()).resolve()
    for cand in (p, *p.parents):
        if (cand / "state").is_dir() and (cand / "profile").is_dir():
            return cand
    raise SystemExit("aios-dashboard: no env_root (dir containing state/ + profile/) at or above cwd")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(UI_DIR), **kw)

    # --- security -----------------------------------------------------
    def _host_ok(self):
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _token_ok(self):
        # constant-time compare — this is the foundation the gated-action layer (task 3) builds on
        return secrets.compare_digest(self.headers.get("X-Aios-Token", ""), self.server.token)

    def _deny(self, code, msg):
        body = json.dumps({"error": msg}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # --- routing ------------------------------------------------------
    def do_GET(self):
        if not self._host_ok():
            return self._deny(403, "bad Host header")
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html"):
            return self._index()
        if route.startswith("/api/"):
            return self._api_get(route)
        return super().do_GET()

    def do_HEAD(self):
        # inherit static HEAD, but behind the same Host gate as GET/POST (task 3 layers on this class)
        if not self._host_ok():
            return self._deny(403, "bad Host header")
        return super().do_HEAD()

    def do_POST(self):
        # Drain the request body BEFORE responding — an unread POST body makes the client see a
        # connection reset instead of our status code (surfaced as a flaky RST under load). The read
        # also stashes the body for the action handlers (task 3). 1 MiB cap = defense-in-depth against
        # an unbounded Content-Length allocation.  # see A63 spec
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            return self._deny(400, "bad Content-Length")  # malformed header: don't 500/RST
        if length > 1_048_576:
            return self._deny(413, "request body too large")
        self._body = self.rfile.read(length) if length > 0 else b""
        if not self._host_ok():
            return self._deny(403, "bad Host header")
        if not self._token_ok():
            return self._deny(401, "missing or wrong X-Aios-Token")
        return self._api_post(self.path.split("?", 1)[0])

    # --- handlers -----------------------------------------------------
    def _index(self):
        html = (UI_DIR / "index.html").read_text(encoding="utf-8")
        html = html.replace("{{TOKEN}}", self.server.token)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _api_get(self, route):
        if route == "/api/health":
            return self._send_json({"ok": True, "env_root": str(self.server.env_root),
                                    "now": time.time()})
        return self._deny(404, f"unknown GET {route}")

    def _api_post(self, route):
        return self._deny(404, f"unknown POST {route}")

    def log_message(self, fmt, *args):  # quiet by default; server prints URL at start
        pass


def make_server(env_root, port=0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.env_root = Path(env_root)
    srv.token = secrets.token_urlsafe(32)
    return srv


def main(argv=None):
    import argparse
    import webbrowser
    ap = argparse.ArgumentParser(description="AIOS dashboard server")
    ap.add_argument("--port", type=int, default=8642)
    ap.add_argument("--env-root", default=None)
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args(argv)
    env_root = resolve_env_root(args.env_root)
    try:
        srv = make_server(env_root, args.port)
    except OSError:
        print(f"aios-dashboard: port {args.port} already in use (server already running?)")
        if args.open:
            webbrowser.open(f"http://127.0.0.1:{args.port}/")
        return 1
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"aios-dashboard: {url}  (env_root={env_root})")
    if args.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
