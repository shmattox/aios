"""Resolve one pipeline leg's runtime config for the GitHub Actions substrate (sub-project A).

The manifest keeps ONE body per leg. The native entry `aios-<leg>` carries body_path,
allowed_tools, max_turns, model; the `aios-gh-<leg>` entry (substrate "github") carries only what
the substrate changes: cron (UTC) and enabled. This module merges them so the workflow never
re-states a tool list or a turn cap.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MANIFEST = os.path.normpath(os.path.join(HERE, "..", "tasks.manifest.json"))
REQUIRED_NATIVE = ("body_path", "allowed_tools", "max_turns")
REQUIRED_GH = ("cron", "enabled")


def _tasks(manifest_path):
    with open(manifest_path, encoding="utf-8") as f:
        return json.load(f)["tasks"]


def _find(tasks, task_id, substrate):
    for t in tasks:
        if t.get("id") == task_id and t.get("substrate") == substrate:
            return t
    raise KeyError("manifest has no %s entry %r" % (substrate, task_id))


def resolve(leg, manifest_path=DEFAULT_MANIFEST):
    tasks = _tasks(manifest_path)
    native = _find(tasks, "aios-%s" % leg, "native")
    gh = _find(tasks, "aios-gh-%s" % leg, "github")
    for k in REQUIRED_NATIVE:
        if k not in native:
            raise ValueError("native entry aios-%s lacks %r" % (leg, k))
    for k in REQUIRED_GH:
        if k not in gh:
            raise ValueError("github entry aios-gh-%s lacks %r" % (leg, k))
    return {
        "leg": leg,
        "body_path": native["body_path"],
        "model": native.get("model") or "",
        "max_turns": int(native["max_turns"]),
        "allowed_tools": ",".join(native["allowed_tools"]),
        "cron": gh["cron"],
        "enabled": bool(gh["enabled"]),
        "context_stages": ",".join(native.get("context_stages") or []),
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.stderr.write("usage: leg_config.py <leg> [manifest_path]\n")
        return 2
    manifest = argv[1] if len(argv) > 1 else DEFAULT_MANIFEST
    print(json.dumps(resolve(argv[0], manifest)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
