#!/usr/bin/env python3
"""meetings_router_task.py — deterministic runner shell for the meetings-router stage.

Resolves the per-install facts from `<env_root>/profile/domains.yaml` (`meetings:` block),
builds the fact-free `meetings_router.py` argv, and executes it, passing the exit code through.
Registered via `type: "script"` in tasks.manifest.json (opt-in). Fact-free: every fact comes
from the profile at the env root passed in.

  python meetings_router_task.py --env-root <env_root> [--dry-run] [--print-argv]
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _parse_yaml_subset(text):
    """Nested dicts of scalars from the engine-generated profile YAML subset (no lists/flow)."""
    root = {}
    stack = [(-1, root)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if line.startswith("-") or ":" not in line:
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        val = rest.strip()
        if val.startswith("#"):
            val = ""
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


def build_argv(env_root, dry_run):
    prof = os.path.join(env_root, "profile", "domains.yaml")
    with open(prof, "r", encoding="utf-8") as fh:
        cfg = _parse_yaml_subset(fh.read()).get("meetings", {})
    drop_zone = os.path.join(env_root, cfg["drop_zone"].replace("/", os.sep))
    dest_root = os.path.join(env_root, "state", "domains")
    log_dir = os.path.join(env_root, "state", "task-logs", "meetings-router")
    argv = ["--drop-zone", drop_zone, "--dest-root", dest_root,
            "--map", json.dumps(cfg.get("folder_map", {})),
            "--default", cfg["default"], "--log-dir", log_dir]
    if dry_run:
        argv.append("--dry-run")
    return argv


def main(argv):
    ap = argparse.ArgumentParser(prog="meetings_router_task.py")
    ap.add_argument("--env-root", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--print-argv", action="store_true")
    a = ap.parse_args(argv)
    core_argv = build_argv(a.env_root, a.dry_run)
    if a.print_argv:
        print(" ".join(core_argv))
        return 0
    core = os.path.join(HERE, "meetings_router.py")
    return subprocess.run([sys.executable, core, *core_argv]).returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
