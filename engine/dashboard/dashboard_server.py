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
from backlog_parse import parse_backlog, station_for  # engine tools shim already on sys.path
import draft_diff  # "Proposed change" diff (A109)

# state files the UI polls for mtime changes; spend-*.json is globbed separately.
WATCHED = {
    "brief": "state/brief-cache.json",
    "standup": "state/factory/standup.json",
    "gate_metrics": "state/factory/gate-metrics.json",
    "queue": "state/queue.json",
}

SSE_POLL_S = 1.0
SSE_PING_EVERY = 15  # polls between keepalive comments

SAFE_SEG = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")   # domains path segment; '.'/'..' rejected in _domains so traversal is impossible
SAFE_ID = re.compile(r"^[A-Za-z0-9._:\-]{1,160}$")
SAFE_SHA = re.compile(r"^[0-9a-f]{7,40}$")
SAFE_TEXT = re.compile(r"^[^\r\n]{1,500}$")          # single-line free text (reason/choice/action)
MAX_CONTENT_BYTES = 256 * 1024                        # A109 gate_edit: multiline draft body (stdin), not SAFE_TEXT


def _content_ok(v):
    """A109 gate_edit content validator — a bounded multiline string piped to ship.py amend's stdin."""
    return isinstance(v, str) and len(v.encode("utf-8")) <= MAX_CONTENT_BYTES


def _param_ok(rx, val):
    """A param validator is None (any str), a compiled regex (Pattern objects are not callable), or a
    callable custom check like _content_ok."""
    if rx is None:
        return True
    if callable(rx):
        return bool(rx(val))
    return bool(rx.match(val))


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


_GLYPH_BADGE = {"⚠": "parked", "▶": "decided", "⛔": "blocked", "✅": "done"}
_TAG_BADGE = {"park": "parked", "parked": "parked", "block": "blocked", "blocked": "blocked",
              "decided": "decided", "decide": "decided", "gate": "gated", "veto": "veto"}


def _match_bracket(s):
    """s[0] == '['. Return index just past the matching ']' (depth-aware for nested []), else -1."""
    depth = 0
    for i, ch in enumerate(s):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def clean_dev_title(headline, group=None):
    """(clean_title, state_badge) for a dev backlog card. Strips the leading status glyph +
    `[tag: date src — CONTENT]` annotation the factory writes into a backlog headline, promoting
    the status to `state_badge`. Nested-bracket-safe; falls back to the (glyph-stripped) raw text
    when the annotation IS the whole headline with no readable remainder."""
    raw = (headline or "").strip()
    h, badge = raw, None
    if h and h[0] in _GLYPH_BADGE:
        badge = _GLYPH_BADGE[h[0]]
        h = h[1:].lstrip()
    if h.startswith("["):
        end = _match_bracket(h)
        if end > 0:                                   # bracket closes — nested-safe
            inner = h[1:end - 1].strip()
            after = h[end:].lstrip(" —-:·").strip()
            tag = re.match(r"[A-Za-z]+", inner)
            if tag:
                badge = _TAG_BADGE.get(tag.group(0).lower(), badge)
            h = after if len(after) >= 12 else re.sub(r"^[A-Za-z]+:\s*", "", inner)
        else:                                         # unclosed bracket (annotation spans sub-bullets)
            m = re.match(r"^\[([A-Za-z]+)\b[^:\]]*:\s*(.+)$", h)
            if m:
                badge = _TAG_BADGE.get(m.group(1).lower(), badge)
                h = m.group(2).strip()
    # drop a leading "YYYY-MM-DD [relay/Seth] —" meta lead-in (keep real subjects like "factory drain")
    h = re.sub(r"^\d{4}-\d{2}-\d{2}\s+(?:relay|seth|claude)\s*(?:—|–|--|\s-\s)\s*", "", h, flags=re.I)
    h = re.sub(r"^\d{4}-\d{2}-\d{2}\s*[—–:-]*\s*", "", h)  # else drop just a bare leading date
    if not badge and group == "veto":
        badge = "veto"
    h = h.strip().lstrip(":)—–-( ").strip()
    return (h if len(h) >= 6 else raw.lstrip("".join(_GLYPH_BADGE)).strip()), badge


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
    "dismiss": (
        {"id": SAFE_ID, "reason": SAFE_TEXT},
        lambda env, tools, p: [
            sys.executable, str(tools / "queue_tx.py"), "dismiss",
            str(env / "state" / "queue.json"), p["id"],
            "--reason", p["reason"], "--by", "dashboard",
        ],
    ),
    "reply": (
        {"target_id": SAFE_ID, "reply_kind": re.compile(r"^(respond|append|comment)$"),
         "text": SAFE_TEXT},
        lambda env, tools, p: [
            sys.executable, str(tools / "brief_session.py"), "record_reply",
            str(env / "state" / "brief-session.json"),
            p["target_id"], p["reply_kind"], p["text"],
        ],
    ),
    # A109 edit-verb: operator-edited draft body → ship.py amend via STDIN (the 3rd tuple element
    # names the param piped to stdin, so multiline content never rides argv). Human-gated: --human-approved
    # is the click; a Paper-Governs / review-lane item still holds unless amend passes the content-refusal.
    "gate_edit": (
        {"id": SAFE_ID, "content": _content_ok},
        lambda env, tools, p: (lambda vr, km: [
            sys.executable, str(tools / "ship.py"), "amend",
            "--queue", str(env / "state" / "queue.json"), "--id", p["id"],
            "--vault-root", str(vr), "--kb-map", json.dumps(km),
            "--approved-by", "dashboard", "--human-approved",
        ])(*_connectors(env)),
        "content",
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

    def end_headers(self):
        # Local dev dashboard: never let the browser cache assets across a version bump. A cached
        # old app.js against a new index.html renders blank (the v0.10.1 stale-cache confusion);
        # no-store makes every reload fetch fresh. Applied to static + API + SSE alike.
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    # --- routing ------------------------------------------------------
    def do_GET(self):
        if not self._host_ok():
            return self._deny(403, "bad Host header")
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html"):
            return self._index()
        if route == "/api/events":
            return self._events()
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
            out["board"] = max((m for m in (_mtime(p) for _, p in self._board_sources(env))
                                if m), default=None)
            return self._send_json(out)
        if route == "/api/brief":
            return self._brief()
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
        if route == "/api/board":
            return self._board()
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

    def _brief(self):
        """The inbox feed: the cache (FO/personal act + gate held) PLUS the dev/GM/env
        backlog items the factory flagged as needing Seth (`dev`) — so the inbox is a true
        cross-silo needs-you front door, as the mockup shows (a dev card among the tasks)."""
        path = self.server.env_root / WATCHED["brief"]
        data = _read_json_file(path)
        if data is None:
            return self._deny(404, f"{path.name} missing or unreadable")
        mt = _mtime(path)
        data["_mtime"] = mt
        data["_age_s"] = (time.time() - mt) if mt else None
        data["dev"] = self._dev_needs_you(self.server.env_root)
        return self._send_json(data)

    def _dev_needs_you(self, env):
        """The factory standup's needs-you (+ veto) backlog items, shaped as READ-ONLY inbox
        cards. Matched by id against every repo BACKLOG.md (the same sources the board reads).
        No gate verbs (not drafts) and no mark-done (worked in a native session) — a dev card is
        a pointer that drills into its repo's backlog."""
        standup = _read_json_file(env / WATCHED["standup"]) or {}
        want = {}  # backlog id -> factory group
        for group in ("needs_you", "veto"):
            for r in (standup.get("groups") or {}).get(group, []):
                if isinstance(r, dict) and r.get("id"):
                    want.setdefault(r["id"], group)
        if not want:
            return []
        out, seen = [], set()
        for key, path in self._board_sources(env):
            try:
                items = parse_backlog(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):  # ValueError catches UnicodeDecodeError — never 500 /api/brief
                continue
            rel = str(path.relative_to(env)).replace("\\", "/")
            for it in items:
                grp = want.get(it["id"])
                if not grp or it["id"] in seen:
                    continue
                seen.add(it["id"])
                title, badge = clean_dev_title(it["headline"], grp)
                out.append({
                    "id": it["id"], "title": title, "_kind": "dev",
                    "repo": key, "gate_human": it["gate_human"],
                    "factory_group": grp, "state_badge": badge, "backlog_path": rel,
                    "refs": [{"tag": "kb", "label": rel, "mock": f"opens {rel} — the {key} backlog"}],
                })
        return out

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
        draft_text = p.read_text(encoding="utf-8")
        out = {"path": str(p), "markdown": draft_text}
        # "Proposed change": diff the draft against the current canonical page it would replace
        target = self._target_path(held[idx])
        if target is not None:
            cur = target.read_text(encoding="utf-8") if target.is_file() else ""
            out["target"] = str(target)
            out["target_exists"] = target.is_file()
            out["diff"] = draft_diff.compute_diff(cur, draft_text)
        # A75 Paper-Governs evidence (advisory) — surface the packet the pipeline already attaches
        # to the queue item (paper_evidence.py attach); render-only, never recomputed here.
        queue_data = _read_json_file(self.server.env_root / WATCHED["queue"]) or {}
        qitem = next((it for it in (queue_data.get("queue") or []) if it.get("id") == held[idx].get("id")), None)
        if qitem and isinstance(qitem.get("paper_evidence"), dict):
            out["paper_evidence"] = qitem["paper_evidence"]
        return self._send_json(out)

    def _target_path(self, item):
        """The canonical page an item's draft would replace (from its conflict_key + kb-map)."""
        env = self.server.env_root
        ck = item.get("conflict_key") or ""
        kb, _, rel = ck.partition("/")
        if not rel:
            return None
        try:
            vault, kb_map = _connectors(env)
        except OSError:
            return None
        if kb not in kb_map:
            return None
        rel_md = rel if rel.endswith(".md") else rel + ".md"
        target = (vault / kb_map[kb] / rel_md.replace("/", os.sep)).resolve()
        vault_r = vault.resolve()
        # defense-in-depth: never read outside the vault even if a conflict_key held `..`
        if target != vault_r and vault_r not in target.parents:
            return None
        return target

    STATIONS = ["incoming", "needs_you", "in_motion", "review", "shipped"]
    SILOS = [("familyoffice", "FamilyOffice"), ("personal", "Personal"), ("gm", "General Mgmt")]

    def _board_sources(self, env):
        """Every backlog file: env-ops root + each Projects/* repo with a BACKLOG.md."""
        out = [("env-ops", env / "BACKLOG.md")]
        proj = env / "Projects"
        if proj.is_dir():
            for d in sorted(p for p in proj.iterdir() if (p / "BACKLOG.md").is_file()):
                out.append((d.name, d / "BACKLOG.md"))
        return [(k, p) for k, p in out if p.is_file()]

    def _board(self):
        env = self.server.env_root
        standup = _read_json_file(env / WATCHED["standup"]) or {}
        standup_ids = {}
        for group, rows in (standup.get("groups") or {}).items():
            for r in rows:
                if isinstance(r, dict) and r.get("id"):
                    standup_ids[r["id"]] = group
        lanes = []
        brief = _read_json_file(env / WATCHED["brief"]) or {}
        held = brief.get("held", [])
        for key, name in self.SILOS:
            cells = {s: [] for s in self.STATIONS}
            for i, row in enumerate(held):
                if str(row.get("kb", "")).lower().startswith(key[:2] if key == "gm" else key):
                    # carry the full row's doc fields so a held card's board modal is as rich
                    # as the inbox (drill-down + inline source links), not a stripped stub
                    card = {
                        "id": row.get("id", f"held-{i}"), "title": row.get("title", ""),
                        "station": "needs_you", "source": "held",
                        "gate_human": True, "draft_index": i,
                        "kb": row.get("kb"), "lane": row.get("lane"),
                        "recommended": row.get("recommended"), "rec_reason": row.get("rec_reason"),
                        "papered_source": row.get("papered_source"), "state_path": row.get("state_path"),
                        "draft_path": row.get("draft_path"), "conflict_key": row.get("conflict_key"),
                    }
                    cells["needs_you"].append({k: v for k, v in card.items() if v is not None})
            # the real needs-you tasks (brief.act) belong on the board too, in their silo's
            # needs_you cell — carry the two-layer voice so a click opens the same rich card the
            # Inbox shows (not just gate holds). Mapped by domain, same rule as held's kb match.
            for a in (brief.get("act") or []):
                dom = str(a.get("domain") or a.get("kb") or "").lower()
                if dom.startswith(key[:2] if key == "gm" else key):
                    # carry the WHOLE act item (urgency/flags/in_motion/thread_id/voices) so the
                    # board modal is as rich as the Inbox — cherry-picking dropped fields.
                    acard = {**a, "station": "needs_you", "source": "act", "_kind": "act",
                             "id": a.get("id") or a.get("item_id") or (a.get("title") or "")[:24]}
                    cells["needs_you"].append({k: v for k, v in acard.items() if v is not None})
            lanes.append({"kind": "silo", "key": key, "name": name,
                          "badge": "active", "cells": cells})
        for key, path in self._board_sources(env):
            cells = {s: [] for s in self.STATIONS}
            try:
                items = parse_backlog(path.read_text(encoding="utf-8"))
            except OSError:
                items = []
            for it in items:
                st = station_for(it, standup_ids)
                if st:
                    cells[st].append({"id": it["id"], "title": it["headline"],
                                      "station": st, "source": "backlog",
                                      "gate_human": it["gate_human"], "draft_index": None})
            flags = sorted({standup_ids[i["id"]] for i in items if i["id"] in standup_ids})
            lanes.append({"kind": "repo", "key": key, "name": key,
                          "badge": "active" + ("·" + ",".join(flags) if flags else ""),
                          "cells": cells})
        return self._send_json({"stations": self.STATIONS, "lanes": lanes})

    def _events(self):
        env = self.server.env_root

        def fingerprint():
            fp = {name: _mtime(env / rel) for name, rel in WATCHED.items()}
            spends = sorted((env / "state" / "factory").glob("spend-*.json"))
            fp["spend"] = _mtime(spends[-1]) if spends else None
            fp["board"] = max((m for m in (_mtime(p) for _, p in self._board_sources(env))
                               if m), default=None)
            return fp

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(b"event: hello\ndata: {}\n\n")
            self.wfile.flush()
            last, ticks = fingerprint(), 0
            while True:
                time.sleep(SSE_POLL_S)
                cur = fingerprint()
                changed = [k for k in cur if cur[k] != last[k]]
                if changed:
                    payload = json.dumps({"changed": changed}).encode("utf-8")
                    self.wfile.write(b"event: change\ndata: " + payload + b"\n\n")
                    self.wfile.flush()
                    last = cur
                ticks += 1
                if ticks % SSE_PING_EVERY == 0:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            return  # client went away — thread ends with the request

    def _domains(self, route):
        env = self.server.env_root
        root = env / "state" / "domains"
        segs = [s for s in route[len("/api/domains"):].split("/") if s]
        # reject dot-segments explicitly (SAFE_SEG's char class contains '.'): a
        # non-normalizing client could otherwise walk out of state/domains/.  # see A63 spec
        if any(s in (".", "..") or not SAFE_SEG.match(s) for s in segs):
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
        entry = ACTIONS[action_id]
        spec, build = entry[0], entry[1]
        stdin_param = entry[2] if len(entry) > 2 else None  # A109: param piped to the CLI's stdin
        try:
            params = json.loads(self._body or b"{}")  # body already drained in do_POST
        except ValueError:
            return self._deny(400, "invalid JSON body")
        if not isinstance(params, dict):
            return self._deny(400, "body must be a JSON object")
        for key, rx in spec.items():
            val = params.get(key)
            if not isinstance(val, str) or not _param_ok(rx, val):
                return self._deny(400, f"param {key!r} missing or invalid")
        if action_id == "veto_revert" and not _git_repo_ok(env, params["repo"]):
            return self._deny(400, "repo must be a git dir at or under env_root")
        argv = build(env, tools, params)
        run_kwargs = dict(capture_output=True, text=True, encoding="utf-8",
                          timeout=600, cwd=str(env))
        if stdin_param is not None:
            run_kwargs["input"] = params[stdin_param]  # multiline content via stdin, never argv
        try:
            proc = subprocess.run(argv, **run_kwargs)
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
