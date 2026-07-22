#!/usr/bin/env python3
"""runtime_fingerprint.py — is the RUNNING (installed) aios engine the same as its source repo? (A90)

Zero-LLM, stdlib-only, fail-soft. Nothing else compares the installed plugin to its dev clone, so a
merged engine change with NO version bump ran a days-old brief for days with zero signal (the 2026-07-14
incident). This computes a cheap drift fingerprint — installed plugin (`installed_plugins.json`:
`version` + `gitCommitSha`) vs the local dev clone (`plugin.json` `version` + `git log -1 -- engine/`
sha) — layered so the same-version-different-sha case (the ACTUAL incident) is caught, not just the
ordinary version-behind case.

Every path returns a dict and every CLI exit is 0 — a fingerprint failure must NEVER break a brief or a
drain. `status` ∈ {stale-version, stale-sha, clean, no-dev-clone}:
  stale-version  installed version ≠ repo version   → "run /plugin update aios"
  stale-sha      same version, engine sha diverged   → "bump plugin.json + reinstall" (the incident)
  clean          version + engine sha match
  no-dev-clone   no local source repo / unreadable install record → silent (external installs degrade)

Scoping the repo sha to `git log -1 -- engine/` is what kills the naive-sha false positive: a docs-only
or backlog commit advances HEAD but not the engine sha, so it does not flag "stale".

Usage:
  python runtime_fingerprint.py --json [--repo-root R] [--installed-plugins P] [--env-root E]
"""
import argparse
import json
import os
import subprocess
import sys

_PLUGIN_KEY = "aios@aios"


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return None


def _default_installed_plugins():
    return os.path.join(os.path.expanduser("~"), ".claude", "plugins", "installed_plugins.json")


def _installed_entry(installed_plugins_path):
    """The aios@aios install record — {version, sha} — preferring `scope: user` over `project`.
    None on any missing/malformed shape (fail-soft; the caller degrades to no-dev-clone)."""
    doc = _read_json(installed_plugins_path)
    if not isinstance(doc, dict):
        return None
    entries = (doc.get("plugins") or {}).get(_PLUGIN_KEY)
    if not isinstance(entries, list) or not entries:
        return None
    good = [e for e in entries if isinstance(e, dict)]
    if not good:
        return None
    chosen = next((e for e in good if e.get("scope") == "user"), good[0])
    return {"version": chosen.get("version"), "sha": chosen.get("gitCommitSha")}


def _find_repo_root(explicit, env_root):
    """Resolve the dev clone, first hit wins, silent miss (§2.3):
    1. explicit --repo-root; 2. $AIOS_DEV_CLONE; 3. a sibling Projects/aios beside env_root.
    A dir counts only if it carries `.claude-plugin/plugin.json`."""
    candidates = []
    if explicit:
        candidates.append(explicit)
    env_clone = os.environ.get("AIOS_DEV_CLONE")
    if env_clone:
        candidates.append(env_clone)
    if env_root:
        # env_root is the state/+profile/ dir; walk up looking for a sibling Projects/aios clone.
        cur = os.path.abspath(env_root)
        seen = set()
        while cur and cur not in seen:
            seen.add(cur)
            candidates.append(os.path.join(cur, "Projects", "aios"))
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    for c in candidates:
        if c and os.path.isfile(os.path.join(c, ".claude-plugin", "plugin.json")):
            return c
    return None


def _repo_side(repo_root):
    """{version, sha} for the dev clone: plugin.json version + the last commit touching engine/.
    None sha when git is unavailable / not a repo (still yields a version for the version compare)."""
    pj = _read_json(os.path.join(repo_root, ".claude-plugin", "plugin.json"))
    version = pj.get("version") if isinstance(pj, dict) else None
    sha = None
    try:
        r = subprocess.run(
            ["git", "-C", repo_root, "log", "-1", "--format=%H", "--", "engine/"],
            capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and r.stdout.strip():
            sha = r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        sha = None
    return {"version": version, "sha": sha}


def _is_ancestor(repo_root, ancestor, descendant):
    """True iff `ancestor` is an ancestor of (or equal to) `descendant` in the dev clone; False on a
    definitive no; None when git can't answer (unknown sha, no git — caller decides the fail posture)."""
    try:
        r = subprocess.run(
            ["git", "-C", repo_root, "merge-base", "--is-ancestor", ancestor, descendant],
            capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return True
        if r.returncode == 1:
            return False
        return None
    except (OSError, subprocess.SubprocessError):
        return None


def fingerprint(repo_root=None, installed_plugins_path=None, env_root=None):
    """Compare the installed engine to its source repo. See module docstring for the status ladder.
    Never raises — returns a dict on every path."""
    installed_plugins_path = installed_plugins_path or _default_installed_plugins()
    installed = _installed_entry(installed_plugins_path)
    resolved_repo = _find_repo_root(repo_root, env_root)
    if installed is None or resolved_repo is None:
        return {"status": "no-dev-clone", "installed": installed,
                "repo": (_repo_side(resolved_repo) if resolved_repo else None),
                "message": ""}
    repo = _repo_side(resolved_repo)
    iv, rv = installed.get("version"), repo.get("version")
    result = {"installed": installed, "repo": repo}
    if iv and rv and iv != rv:
        return {**result, "status": "stale-version",
                "message": f"installed engine v{iv} ≠ repo v{rv} — run /plugin update aios"}
    ish, rsh = installed.get("sha"), repo.get("sha")
    # The install records repo HEAD at install time; the repo side is the last engine-touching
    # commit. Equality is too strict — a trailing non-engine commit (version bump, backlog note)
    # makes the install sha a DESCENDANT of the engine sha while containing every engine change.
    # Clean iff the engine sha is an ancestor of the install sha; an unanswerable git (unknown
    # sha, detached fixture) keeps the stale-sha signal (signal over silence, the A90 posture).
    if ish and rsh and ish != rsh and _is_ancestor(resolved_repo, rsh, ish) is not True:
        return {**result, "status": "stale-sha",
                "message": (f"engine changed at v{rv or iv} without a version bump — "
                            f"bump plugin.json + reinstall")}
    return {**result, "status": "clean", "message": ""}


def render(fp):
    """The ONE brief health line for a drift status, '' when clean/no-dev-clone (the delta-gated
    'earn your line' rule — a healthy engine renders nothing). The tool owns its own formatting; the
    brief gather folds this into `health_lines` (same pattern as pipeline_health / standing_checks)."""
    if (fp or {}).get("status") in ("stale-version", "stale-sha"):
        return "⚠️ Engine drift: " + fp.get("message", "")
    return ""


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # native Windows console is cp1252
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(
        prog="runtime_fingerprint.py",
        description="Detect a stale installed aios engine vs its source repo (A90, fail-soft).")
    ap.add_argument("--repo-root", help="dev clone path (default: $AIOS_DEV_CLONE or sibling of env-root)")
    ap.add_argument("--installed-plugins", help="installed_plugins.json (default: ~/.claude/plugins/…)")
    ap.add_argument("--env-root", help="the state/+profile/ dir, for sibling dev-clone resolution")
    ap.add_argument("--json", action="store_true", help="print the fingerprint dict")
    ap.add_argument("--line", action="store_true", help="print only the brief drift line ('' when clean)")
    args = ap.parse_args(argv)
    fp = fingerprint(repo_root=args.repo_root, installed_plugins_path=args.installed_plugins,
                     env_root=args.env_root)
    if args.line:
        line = render(fp)
        if line:
            print(line)
    else:
        print(json.dumps(fp, ensure_ascii=False, indent=2))
    return 0  # ALWAYS 0 — a fingerprint failure never breaks a brief or a drain


if __name__ == "__main__":
    sys.exit(main())
