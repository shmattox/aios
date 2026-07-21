#!/usr/bin/env python3
"""A63 dashboard server: static Linear-style UI + read API + allowlisted gated
actions over the AIOS state surfaces. Server holds ZERO write logic — every
write shells out to an existing gated CLI.  # see A63 spec

Security (mandatory): 127.0.0.1 bind, exact Host validation, per-start token
on every POST (DNS-rebinding defense).
"""
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
UI_DIR = HERE / "ui"
sys.path.insert(0, str(HERE.parent / "tools"))

from state_validate import _extract_frontmatter, _parse_yaml  # engine YAML-subset reader; no PyYAML in repo

# state files the UI polls for mtime changes; spend-*.json is globbed separately.
WATCHED = {
    "brief": "state/brief-cache.json",
    "standup": "state/factory/standup.json",
    "gate_metrics": "state/factory/gate-metrics.json",
    "queue": "state/queue.json",
}

SAFE_SEG = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")   # domains path segment; traversal impossible by construction
SAFE_ID = re.compile(r"^[A-Za-z0-9._:\-]{1,160}$")
SAFE_SHA = re.compile(r"^[0-9a-f]{7,40}$")
SAFE_TEXT = re.compile(r"^[^\r\n]{1,500}$")          # single-line free text (reason/choice/action)


def _mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _read_json_file(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _record(path):
    text = path.read_text(encoding="utf-8")
    fields = _extract_frontmatter(text) or {}
    body = text.split("---", 2)[2].lstrip("\n") if text.startswith("---") and text.count("---") >= 2 else text
    return fields, body


def _connectors(env):
    data = _parse_yaml((env / "profile" / "connectors.yaml").read_text(encoding="utf-8")) or {}
    vault = data.get("vault", {})
    return (env / vault.get("live_root", "SecondBrain")), vault.get("live_kb_map", {})


def _git_repo_ok(env, rel):
    p = (env / rel).resolve()
    return p.is_dir() and (p / ".git").exists() and (p == env or env in p.parents)


# action id -> (param validators, argv builder(env, tools_dir, params)).
# This dict is the ONLY write path; everything else is 403.  # see A63 spec
ACTIONS = {
    "gate_ship": (
        {"id": SAFE_ID},
        lambda env, tools, p: (lambda vr, km: [
            sys.executable, str(tools / "ship.py"), "ship",
            "--queue", str(env / "state" / "queue.json"), "--id", p["id"],
            "--vault-root", str(vr), "--kb-map", json.dumps(km),
            "--approved-by", "dashboard", "--human-approved",
        ])(*_connectors(env)),
    ),
    "gate_reject": (
        {"id": SAFE_ID, "reason": SAFE_TEXT},
        lambda env, tools, p: [
            sys.executable, str(tools / "ship.py"), "reject",
            "--queue", str(env / "state" / "queue.json"), "--id", p["id"],
            "--reason", p["reason"], "--decided-by", "human",
        ],
    ),
    "walk_decision": (
        {"item_id": SAFE_ID, "station": SAFE_ID, "choice": SAFE_TEXT, "action": SAFE_TEXT},
        lambda env, tools, p: [
            sys.executable, str(tools / "brief_session.py"), "record_decision",
            str(env / "state" / "brief-session.json"),
            p["item_id"], p["station"], p["choice"], p["action"],
        ],
    ),
    "veto_revert": (
        {"repo": None, "sha": SAFE_SHA},  # repo validated structurally below (must be a git dir under env_root)
        lambda env, tools, p: [
            "git", "-C", str((env / p["repo"]).resolve()), "revert", "--no-edit", p["sha"],
        ],
    ),
}


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
        # constant-time compare — this is the foundation the gated-action layer builds on
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
        # inherit static HEAD, but behind the same Host gate as GET/POST
        if not self._host_ok():
            return self._deny(403, "bad Host header")
        return super().do_HEAD()

    def do_POST(self):
        # Drain the request body BEFORE responding — an unread POST body makes the client see a
        # connection reset instead of our status code (surfaced as a flaky RST under load). The read
        # also stashes the body for the action handlers. 1 MiB cap = defense-in-depth against an
        # unbounded Content-Length allocation.  # see A63 spec
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
        env = self.server.env_root
        if route == "/api/health":
            return self._send_json({"ok": True, "env_root": str(env), "now": time.time()})
        if route == "/api/mtimes":
            out = {name: _mtime(env / rel) for name, rel in WATCHED.items()}
            spends = sorted((env / "state" / "factory").glob("spend-*.json"))
            out["spend"] = _mtime(spends[-1]) if spends else None
            return self._send_json(out)
        if route == "/api/brief":
            return self._file_with_age(env / WATCHED["brief"])
        if route == "/api/standup":
            return self._file_with_age(env / WATCHED["standup"])
        if route == "/api/spend":
            days = [d for d in (_read_json_file(p) for p in
                    sorted((env / "state" / "factory").glob("spend-*.json"))) if d]
            return self._send_json({"days": days,
                                    "gate_metrics": _read_json_file(env / WATCHED["gate_metrics"])})
        if route == "/api/held":
            brief = _read_json_file(env / WATCHED["brief"]) or {}
            return self._send_json({"held": brief.get("held", []),
                                    "generated_utc": brief.get("generated_utc")})
        if route == "/api/draft":
            return self._draft()
        if route == "/api/domains" or route.startswith("/api/domains/"):
            return self._domains(route)
        return self._deny(404, f"unknown GET {route}")

    def _file_with_age(self, path):
        data = _read_json_file(path)
        if data is None:
            return self._deny(404, f"{path.name} missing or unreadable")
        mt = _mtime(path)
        data["_mtime"] = mt
        data["_age_s"] = (time.time() - mt) if mt else None
        return self._send_json(data)

    def _draft(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        try:
            idx = int(q.get("i", ["-1"])[0])
        except ValueError:
            idx = -1
        brief = _read_json_file(self.server.env_root / WATCHED["brief"]) or {}
        held = brief.get("held", [])
        # index into the live held list = data-derived allowlist; never accept a
        # caller-supplied path.  # see A63 spec
        if not (0 <= idx < len(held)):
            return self._deny(404, "no such held row")
        p = Path(held[idx].get("draft_path", ""))
        if not p.is_file():
            return self._deny(404, "draft file missing on disk")
        return self._send_json({"path": str(p),
                                "markdown": p.read_text(encoding="utf-8")})

    def _domains(self, route):
        env = self.server.env_root
        root = env / "state" / "domains"
        segs = [s for s in route[len("/api/domains"):].split("/") if s]
        if any(not SAFE_SEG.match(s) for s in segs):
            return self._deny(400, "bad path segment")
        if not segs:
            silos = []
            for sd in (sorted(p for p in root.iterdir() if (p / "schema.yaml").is_file())
                       if root.is_dir() else []):
                tdir = sd / "tables"
                tables = [{"name": t.name, "count": len(list(t.glob("*.md")))}
                          for t in sorted(tdir.iterdir()) if t.is_dir()] if tdir.is_dir() else []
                silos.append({"silo": sd.name, "tables": tables})
            return self._send_json({"silos": silos})
        if len(segs) == 2:
            tdir = root / segs[0] / "tables" / segs[1]
            if not tdir.is_dir():
                return self._deny(404, "no such table")
            records = []
            for f in sorted(tdir.glob("*.md")):
                fields, _ = _record(f)
                records.append({"slug": f.stem, "fields": fields})
            return self._send_json({"records": records})
        if len(segs) == 3:
            f = root / segs[0] / "tables" / segs[1] / (segs[2] + ".md")
            if not f.is_file():
                return self._deny(404, "no such record")
            fields, body = _record(f)
            return self._send_json({"slug": segs[2], "fields": fields, "body": body})
        return self._deny(404, "unknown domains route")

    def _api_post(self, route):
        env = self.server.env_root
        tools = getattr(self.server, "tools_dir", HERE.parent / "tools")
        if not route.startswith("/api/action/"):
            return self._deny(404, f"unknown POST {route}")
        action_id = route[len("/api/action/"):]
        if action_id not in ACTIONS:
            return self._deny(403, f"action {action_id!r} not allowlisted")
        spec, build = ACTIONS[action_id]
        try:
            params = json.loads(self._body or b"{}")  # body already drained in do_POST
        except ValueError:
            return self._deny(400, "invalid JSON body")
        if not isinstance(params, dict):
            return self._deny(400, "body must be a JSON object")
        for key, rx in spec.items():
            val = params.get(key)
            if not isinstance(val, str) or (rx is not None and not rx.match(val)):
                return self._deny(400, f"param {key!r} missing or invalid")
        if action_id == "veto_revert" and not _git_repo_ok(env, params["repo"]):
            return self._deny(400, "repo must be a git dir at or under env_root")
        argv = build(env, tools, params)
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  encoding="utf-8", timeout=600, cwd=str(env))
        except (OSError, subprocess.TimeoutExpired) as e:
            return self._send_json({"ok": False, "code": -1, "stdout": "",
                                    "stderr": str(e)}, code=502)
        return self._send_json({"ok": proc.returncode == 0, "code": proc.returncode,
                                "stdout": proc.stdout, "stderr": proc.stderr})

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
