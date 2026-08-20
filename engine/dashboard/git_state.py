"""Git state for the dashboard cockpit: active worktrees (the factory's isolated drains) +
open PRs. Best-effort — a missing repo/remote/gh binary contributes zero, never raises. Worktrees
are local + fast (computed per request); PRs hit the network, so they are served from a TTL cache
refreshed on a background thread (the request never blocks on `gh`)."""

import os
import json
import time
import threading
import subprocess

from pipeline_state import _repo_roots   # env root + Projects/* — the only repos we scan


def _run(args, cwd=None, timeout=8):
    """Read-only subprocess, list-args (no shell). Returns stdout on success, else None."""
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _parse_worktrees(raw, repo, root):
    """Parse `git worktree list --porcelain` into records. Pure (unit-tested)."""
    out, cur = [], {}

    def flush():
        if not cur:
            return
        branch = cur.get("branch", "")
        if branch.startswith("refs/heads/"):
            branch = branch[len("refs/heads/"):]
        path = cur.get("worktree", "")
        out.append({
            "repo": repo, "path": path, "head": (cur.get("HEAD", "") or "")[:8],
            "branch": branch or ("detached" if "detached" in cur else ""),
            "locked": "locked" in cur, "primary": os.path.abspath(path) == os.path.abspath(root),
        })

    for line in (raw or "").splitlines():
        if not line.strip():
            flush(); cur = {}
            continue
        key, _, val = line.partition(" ")
        cur[key] = val
    flush()
    return out


def worktrees(env_root):
    out = []
    for root in _repo_roots(env_root):
        raw = _run(["git", "-C", root, "worktree", "list", "--porcelain"], timeout=5)
        if not raw:
            continue
        repo = "env-ops" if root == env_root else os.path.basename(root)
        out += _parse_worktrees(raw, repo, root)
    # extras (active drains) before the boring primary checkouts
    out.sort(key=lambda w: (w["primary"], w["repo"]))
    return out


# --- PRs: TTL cache refreshed off-thread so the request never blocks on the network ----------
_PR_TTL = 60.0
_PR_LOCK = threading.Lock()
_PR_CACHE = {"ts": 0.0, "prs": [], "refreshing": False, "loaded": False}


def _refresh_prs(env_root):
    prs = []
    try:
        for root in _repo_roots(env_root):
            remote = _run(["git", "-C", root, "remote", "get-url", "origin"], timeout=5)
            if not remote or "github.com" not in remote:
                continue
            out = _run(["gh", "pr", "list", "--json", "number,title,headRefName,state,url,isDraft",
                        "--limit", "30"], cwd=root, timeout=10)
            try:
                items = json.loads(out or "[]")
            except (ValueError, TypeError):
                items = []
            repo = "env-ops" if root == env_root else os.path.basename(root)
            for p in items:
                prs.append({"repo": repo, "number": p.get("number"), "title": p.get("title"),
                            "branch": p.get("headRefName"), "state": p.get("state"),
                            "url": p.get("url"), "draft": bool(p.get("isDraft"))})
    finally:
        with _PR_LOCK:
            _PR_CACHE.update(ts=time.time(), prs=prs, refreshing=False, loaded=True)


def prs(env_root):
    """Return the cached PR list immediately; kick a background refresh if stale. `loading` is True
    until the first refresh completes so the UI can distinguish 'no PRs' from 'not fetched yet'."""
    now = time.time()
    with _PR_LOCK:
        stale = now - _PR_CACHE["ts"] > _PR_TTL
        if stale and not _PR_CACHE["refreshing"]:
            _PR_CACHE["refreshing"] = True
            threading.Thread(target=_refresh_prs, args=(env_root,), daemon=True).start()
        return {"items": list(_PR_CACHE["prs"]), "loading": not _PR_CACHE["loaded"]}


def reset_pr_cache():
    """Test hook — drop the PR cache."""
    with _PR_LOCK:
        _PR_CACHE.update(ts=0.0, prs=[], refreshing=False, loaded=False)


def state(env_root):
    return {"worktrees": worktrees(env_root), "prs": prs(env_root)}
