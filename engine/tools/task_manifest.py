#!/usr/bin/env python3
"""Derive scheduled-task facts from `deploy/tasks.manifest.json` — the single source of truth.

The setup VERIFY gate must NOT hardcode how many OS-native scheduled tasks a registration
produces: the registrars (`deploy/{windows,mac,linux}/register-tasks.*`) create exactly the
`substrate == 'native' and enabled` entries, and that set grows/shrinks as tasks are added or
disabled (H42 added a disabled meetings-router; A91 retired resolve-sweep). A literal count in the
skill prose silently goes stale and makes a correct fresh install report VERIFY failure (A46).

So VERIFY derives the expected count from the manifest itself:

    python engine/tools/task_manifest.py            # -> "7"  (the enabled-native count)
    python engine/tools/task_manifest.py --ids      # -> the enabled-native task ids, one per line

This is the same filter the registrars apply, kept in one place so a task add can never re-break
the gate. `--manifest <path>` overrides the default (for tests / non-standard layouts).
"""
import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
# engine/tools/ -> repo root -> deploy/tasks.manifest.json
DEFAULT_MANIFEST = os.path.normpath(os.path.join(_HERE, "..", "..", "deploy", "tasks.manifest.json"))


def enabled_native_tasks(manifest_path=None):
    """Return the manifest task dicts the OS registrars actually schedule: substrate native + enabled.

    This mirrors the registrars' filter exactly (`substrate -eq 'native' -and $_.enabled`) so the
    expected-count derivation can never drift from what registration produces.
    """
    path = manifest_path or DEFAULT_MANIFEST
    with open(path, encoding="utf-8") as f:
        tasks = json.load(f)["tasks"]
    return [t for t in tasks if t.get("substrate") == "native" and t.get("enabled")]


def enabled_native_count(manifest_path=None):
    return len(enabled_native_tasks(manifest_path))


def enabled_native_ids(manifest_path=None):
    return [t["id"] for t in enabled_native_tasks(manifest_path)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", default=None, help="path to tasks.manifest.json (default: bundled)")
    ap.add_argument("--ids", action="store_true", help="print enabled-native task ids instead of the count")
    args = ap.parse_args(argv)
    if args.ids:
        for tid in enabled_native_ids(args.manifest):
            print(tid)
    else:
        print(enabled_native_count(args.manifest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
