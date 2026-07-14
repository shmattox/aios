#!/usr/bin/env python3
"""capture_router_task.py — the deterministic runner shell for the capture-router stage.

The stage's own body says "Zero judgment here: run the tool, report what it printed" — so no
model belongs in the loop. This shell replaces the nightly `claude -p` wrapper: it resolves the
per-install facts from `<env_root>/profile/*.yaml`, builds the `capture_router.py` argv, and
executes it, passing the exit code through. Registered via `type: "script"` in
tasks.manifest.json; the OS-native runner invokes it directly.

Fact-free: every fact comes from the profile at the env root passed in.

Profile parsing: the profile files are engine-generated (setup writes them) — nested mappings
of scalar values, 2-space indentation, optional quotes, inline comments. `_parse_yaml_subset`
reads exactly that shape; it is NOT a general YAML parser (lists/flow styles are skipped).

Usage:
  python capture_router_task.py --env-root <env_root> [--stale-days 3] [--print-argv]
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _parse_yaml_subset(text):
    """Nested dicts of scalars from the engine-generated profile YAML subset."""
    root = {}
    stack = [(-1, root)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if line.startswith("-") or ":" not in line:
            continue                      # list items / flow lines — not needed here
        key, _, rest = line.partition(":")
        key = key.strip()
        val = rest.strip()
        if val.startswith("#"):
            val = ""                      # value is only an inline comment -> section opener
        elif val.startswith('"'):
            end = val.find('"', 1)
            val = val[1:end] if end > 0 else val.strip('"')
        elif val.startswith("'"):
            end = val.find("'", 1)
            val = val[1:end] if end > 0 else val.strip("'")
        else:
            val = val.split(" #", 1)[0].strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if val == "":
            child = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = val
    return root


def _load(path):
    with open(path, encoding="utf-8") as f:
        return _parse_yaml_subset(f.read())


def read_profile(env_root):
    """(live_root, kb_map, default_kb) from the install's profile. Fails loud on gaps."""
    conn = _load(os.path.join(env_root, "profile", "connectors.yaml"))
    vault = conn.get("vault") or {}
    live_root = vault.get("live_root")
    kb_map = {k: v for k, v in (vault.get("live_kb_map") or {}).items() if isinstance(v, str)}
    if not live_root or not kb_map:
        # print to STDOUT so the runner's log trailer carries the reason (it captures stdout)
        print("FAIL: profile vault.live_root / vault.live_kb_map missing - "
              "run /aios:setup (capture-router cannot route without them)")
        raise SystemExit(1)
    dom = _load(os.path.join(env_root, "profile", "domains.yaml"))
    # `_default` from the profile; else fall back to a real kb from THIS install's map (sorted for
    # determinism) — never a baked-in instance name. kb_map is guaranteed non-empty (fail-loud above).
    default_kb = (((dom.get("session_capture") or {}).get("domain_map") or {}).get("_default")
                  or sorted(kb_map)[0])
    return live_root, kb_map, default_kb


def build_argv(env_root, live_root, kb_map, default_kb, stale_days):
    """The capture_router.py argv — pure function of the resolved profile (testable)."""
    vault = live_root if os.path.isabs(live_root) else os.path.join(env_root, live_root)
    argv = ["run",
            "--auto-root", os.path.join(vault, "00_Inbox", "auto"),
            "--vault-root", vault]
    for kb in sorted(kb_map):
        argv += ["--kb", f"{kb}={kb_map[kb]}"]
    argv += ["--source", "gmail", "--source", "webclipper"]
    bookmarks = os.path.join(vault, "00_Inbox", ".state", "chrome", "Bookmarks")
    if os.path.isfile(bookmarks):
        argv += ["--source", "chrome", "--bookmarks", bookmarks]
    argv += ["--default-kb", default_kb, "--stale-days", str(stale_days),
             "--manifest", os.path.join(env_root, "state", "capture-router-manifest.jsonl"),
             "--context-log", os.path.join(env_root, "state", "context-log.jsonl")]
    return argv


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="capture_router_task.py",
                                 description="Deterministic capture-router stage shell.")
    ap.add_argument("--env-root", required=True)
    ap.add_argument("--stale-days", type=int, default=3)
    ap.add_argument("--print-argv", action="store_true",
                    help="print the resolved capture_router argv as JSON and exit (dry-run)")
    args = ap.parse_args(argv)

    live_root, kb_map, default_kb = read_profile(args.env_root)
    router_argv = build_argv(args.env_root, live_root, kb_map, default_kb, args.stale_days)
    if args.print_argv:
        print(json.dumps(router_argv))
        return 0
    return subprocess.call([sys.executable, os.path.join(HERE, "capture_router.py")] + router_argv)


if __name__ == "__main__":
    sys.exit(main())
