"""Dev servers for the dashboard: every server declared in a `.claude/launch.json` across the env
root + Projects/*, with a live up/down check (a quick localhost port probe). Read-only; best-effort
(a missing/malformed launch.json contributes zero, never raises). Start/stop/logs are separate
gated actions — this module only reports."""

import io
import os
import re
import json
import socket

from pipeline_state import _repo_roots   # env root + Projects/* — where launch.json files live


def _port_up(port):
    if not port:
        return False
    try:
        s = socket.create_connection(("127.0.0.1", int(port)), timeout=0.3)
        s.close()
        return True
    except (OSError, ValueError, OverflowError):
        return False


def _configs(env_root):
    for root in _repo_roots(env_root):
        lj = os.path.join(root, ".claude", "launch.json")
        if not os.path.isfile(lj):
            continue
        repo = "env-ops" if root == env_root else os.path.basename(root)
        try:
            with io.open(lj, encoding="utf-8") as f:
                data = json.load(f) or {}
        except (OSError, ValueError):
            continue
        for c in data.get("configurations", []) or []:
            if isinstance(c, dict) and c.get("name"):
                yield repo, c


def servers(env_root):
    seen, out = set(), []
    for repo, c in _configs(env_root):
        port = c.get("port")
        cmd = " ".join([c.get("runtimeExecutable", "")] + list(c.get("runtimeArgs", []) or [])).strip()
        # the same server is often declared in two launch.json files (env root + the repo itself);
        # dedupe by (name, port) and take the repo the command actually targets (--prefix Projects/<x>).
        m = re.search(r"Projects[\\/]([^\\/ ]+)", cmd)
        if m:
            repo = m.group(1)
        key = (c.get("name"), port)
        if key in seen:
            continue
        seen.add(key)
        url = c.get("url") or (f"http://localhost:{port}" if port else "")
        out.append({"repo": repo, "name": c.get("name"), "port": port, "url": url,
                    "cmd": cmd, "up": _port_up(port)})
    out.sort(key=lambda s: (not s["up"], s["repo"], s["name"]))   # running first
    return out
