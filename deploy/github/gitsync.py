"""Commit / pull-rebase / push for the GitHub Actions substrate (sub-project A, spec §3.2, §5).

The agent never runs git in the cloud; this does, deterministically:
  add -A → (dry_run: export patch, unstage, stop) → commit → pull --rebase --autostash → push
  push rejected → one more pull-rebase + push → else error + patch artifact.
  rebase conflict → `rebase --abort`, error, patch artifact. The commit is never lost.
"""
import argparse
import json
import os
import subprocess
import sys

IDENTITY = ["-c", "user.name=aios-pipeline", "-c", "user.email=aios-pipeline@users.noreply.github.com",
            "-c", "core.autocrlf=false"]


def _run(cwd, *args, check=False):
    p = subprocess.run(["git", *IDENTITY, *args], cwd=cwd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), (p.stderr or p.stdout).strip()))
    return p


def _write_patch(cwd, artifact_dir, name, staged):
    os.makedirs(artifact_dir, exist_ok=True)
    path = os.path.join(artifact_dir, "%s.patch" % name)
    diff = _run(cwd, "diff", "--cached") if staged else _run(cwd, "format-patch", "-1", "--stdout")
    with open(path, "w", encoding="utf-8") as f:
        f.write(diff.stdout)
    return path


def sync_repo(path, message, dry_run, artifact_dir, name):
    r = {"name": name, "changed": False, "committed": None, "pushed": False, "patch": None, "error": None}
    _run(path, "add", "-A", check=True)
    if _run(path, "diff", "--cached", "--quiet").returncode == 0:
        return r
    r["changed"] = True
    if dry_run:
        r["patch"] = _write_patch(path, artifact_dir, name, staged=True)
        _run(path, "reset", "-q", check=True)
        return r
    try:
        _run(path, "commit", "-q", "-m", message, check=True)
    except RuntimeError as e:
        r["error"] = "commit failed: %s" % str(e)[-400:]
        r["patch"] = _write_patch(path, artifact_dir, name, staged=True)
        return r
    r["committed"] = _run(path, "rev-parse", "HEAD", check=True).stdout.strip()
    # F6: a fresh `actions/checkout` sets no upstream tracking, so the bare `pull`/`push` forms
    # (which rely on tracking) fail. Resolve the branch once and use explicit refspecs instead.
    branch = _run(path, "rev-parse", "--abbrev-ref", "HEAD", check=True).stdout.strip()
    for attempt in (1, 2):
        pull = _run(path, "pull", "--rebase", "--autostash", "-q", "origin", branch)
        if pull.returncode != 0:
            _run(path, "rebase", "--abort")
            r["error"] = "rebase failed (attempt %d): %s" % (attempt, (pull.stderr or pull.stdout).strip()[-400:])
            r["patch"] = _write_patch(path, artifact_dir, name, staged=False)
            return r
        r["committed"] = _run(path, "rev-parse", "HEAD", check=True).stdout.strip()
        push = _run(path, "push", "-q", "origin", "HEAD:%s" % branch)
        if push.returncode == 0:
            r["pushed"] = True
            return r
    r["error"] = "push rejected twice: %s" % (push.stderr or push.stdout).strip()[-400:]
    r["patch"] = _write_patch(path, artifact_dir, name, staged=False)
    return r


def main(argv=None):
    p = argparse.ArgumentParser(description="aios GitHub-substrate git sync")
    p.add_argument("--repo", action="append", required=True, help="repo path (repeatable, in order)")
    p.add_argument("--message", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--artifact-dir", required=True)
    a = p.parse_args(argv)
    for path in a.repo:
        name = os.path.basename(os.path.normpath(path)) or "repo"
        try:
            r = sync_repo(path, a.message, a.dry_run, a.artifact_dir, name)
        except RuntimeError as e:
            r = {"name": name, "changed": None, "committed": None, "pushed": False, "patch": None, "error": str(e)}
        print(json.dumps(r))
        if r["error"]:
            # F3: never proceed on stale state — a conflicted repo must not let a later one
            # push, since the next run would then skip the unpushed drafts entirely.
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
