"""Sub-project A — the workflow, not the agent, owns commit/rebase/push. Proven against real git:
push lands; dry-run exports a patch and leaves no commit; a conflicting upstream aborts cleanly."""
import os, subprocess, sys, tempfile
import pytest
_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO = os.path.normpath(os.path.join(_TOOLS, "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "deploy", "github"))
import gitsync as gs


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false",
                           *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def pair(tmp_path):
    bare = tmp_path / "origin.git"; _git(tmp_path, "init", "--bare", "-q", "--initial-branch=main", str(bare))
    a = tmp_path / "a"; _git(tmp_path, "clone", "-q", str(bare), str(a))
    (a / "f.txt").write_text("base\n"); _git(a, "add", "."); _git(a, "commit", "-qm", "base")
    _git(a, "push", "-q", "-u", "origin", "main")
    b = tmp_path / "b"; _git(tmp_path, "clone", "-q", str(bare), str(b))
    return bare, a, b


def test_no_change_is_noop(pair, tmp_path):
    _, a, _ = pair
    r = gs.sync_repo(str(a), "msg", False, str(tmp_path / "art"), "a")
    assert r == {"name": "a", "changed": False, "committed": None, "pushed": False, "patch": None, "error": None}


def test_change_is_committed_and_pushed_after_rebase_on_upstream(pair, tmp_path):
    bare, a, b = pair
    (b / "g.txt").write_text("from b\n"); _git(b, "add", "."); _git(b, "commit", "-qm", "b"); _git(b, "push", "-q")
    (a / "f.txt").write_text("changed by a\n")
    r = gs.sync_repo(str(a), "leg run", False, str(tmp_path / "art"), "a")
    assert r["changed"] and r["committed"] and r["pushed"] and r["error"] is None
    _git(b, "fetch", "-q")
    log = _git(b, "log", "--oneline", "origin/main")
    assert "leg run" in log and "b" in log


def test_dry_run_exports_patch_and_leaves_tree_uncommitted(pair, tmp_path):
    _, a, _ = pair
    (a / "f.txt").write_text("dry\n")
    r = gs.sync_repo(str(a), "leg run", True, str(tmp_path / "art"), "a")
    assert r["changed"] and r["committed"] is None and r["pushed"] is False
    assert os.path.isfile(r["patch"]) and "+dry" in open(r["patch"]).read()
    assert "leg run" not in _git(a, "log", "--oneline")
    assert _git(a, "status", "--porcelain").strip() == "M f.txt"


def test_conflict_aborts_rebase_and_reports_error(pair, tmp_path):
    _, a, b = pair
    (b / "f.txt").write_text("b wins\n"); _git(b, "add", "."); _git(b, "commit", "-qm", "b"); _git(b, "push", "-q")
    (a / "f.txt").write_text("a wins\n")
    r = gs.sync_repo(str(a), "leg run", False, str(tmp_path / "art"), "a")
    assert r["error"] and "rebase" in r["error"].lower()
    assert not os.path.isdir(a / ".git" / "rebase-merge"), "rebase must be aborted"
    assert os.path.isfile(r["patch"])


def test_main_exit_1_when_any_repo_errors(pair, tmp_path, capsys):
    _, a, b = pair
    (b / "f.txt").write_text("b wins\n"); _git(b, "add", "."); _git(b, "commit", "-qm", "b"); _git(b, "push", "-q")
    (a / "f.txt").write_text("a wins\n")
    assert gs.main(["--repo", str(a), "--message", "m", "--artifact-dir", str(tmp_path / "art")]) == 1
    assert '"error"' in capsys.readouterr().out


def test_main_stops_at_the_first_failing_repo(pair, tmp_path, capsys):
    """Spec §5 — never proceed on stale state. A conflicted vault must not let the env repo push a
    queue advance whose drafts never landed (the next run would skip them; the gate would then
    reject them as 'no draft found')."""
    _, a, b = pair
    (b / "f.txt").write_text("b wins\n"); _git(b, "add", "."); _git(b, "commit", "-qm", "b"); _git(b, "push", "-q")
    (a / "f.txt").write_text("a wins\n")
    bare2 = tmp_path / "origin2.git"
    _git(tmp_path, "init", "--bare", "-q", "--initial-branch=main", str(bare2))
    c = tmp_path / "c"; _git(tmp_path, "clone", "-q", str(bare2), str(c))
    (c / "state.txt").write_text("base\n"); _git(c, "add", "."); _git(c, "commit", "-qm", "base")
    _git(c, "push", "-q", "-u", "origin", "main")
    before = _git(c, "rev-parse", "HEAD").strip()
    (c / "state.txt").write_text("queue advanced\n")

    code = gs.main(["--repo", str(a), "--repo", str(c), "--message", "m",
                    "--artifact-dir", str(tmp_path / "art")])

    assert code == 1
    out = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(out) == 1 and '"error"' in out[0], "only the failing repo is reported"
    assert _git(c, "rev-parse", "HEAD").strip() == before, "second repo must not be committed"
    assert _git(c, "diff", "--cached", "--name-only").strip() == "", "second repo must not be staged"


def test_pull_and_push_work_without_upstream_tracking(pair, tmp_path):
    """Production is a fresh `actions/checkout`, which configures no upstream. Every other test
    pre-sets it with `push -u`, so the bare `pull`/`push` form was invisible to a green suite."""
    bare, a, _ = pair
    c = tmp_path / "c"; _git(tmp_path, "clone", "-q", str(bare), str(c))
    _git(c, "branch", "--unset-upstream")
    assert subprocess.run(["git", "rev-parse", "--abbrev-ref", "main@{u}"], cwd=c,
                          capture_output=True).returncode != 0, "fixture must have no tracking"
    (c / "h.txt").write_text("no tracking\n")
    r = gs.sync_repo(str(c), "leg run", False, str(tmp_path / "art"), "c")
    assert r["error"] is None and r["pushed"] is True and r["committed"]
    _git(a, "fetch", "-q")
    assert "leg run" in _git(a, "log", "--oneline", "origin/main")


def test_commit_failure_exports_staged_patch(pair, tmp_path):
    _, a, _ = pair
    (a / "f.txt").write_text("staged but uncommittable\n")
    hook = a / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n"); hook.chmod(0o755)
    r = gs.sync_repo(str(a), "leg run", False, str(tmp_path / "art"), "a")
    assert r["changed"] and r["committed"] is None and r["pushed"] is False
    assert r["error"] and "commit" in r["error"].lower()
    assert os.path.isfile(r["patch"]) and "+staged but uncommittable" in open(r["patch"]).read()


def test_push_rejected_once_then_retry_succeeds(pair, tmp_path):
    bare, a, b = pair
    counter = tmp_path / "count"
    hook = bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nif [ ! -f '%s' ]; then echo 1 > '%s'; exit 1; fi\nexit 0\n"
                    % (counter.as_posix(), counter.as_posix()))
    hook.chmod(0o755)
    (a / "f.txt").write_text("retry me\n")
    r = gs.sync_repo(str(a), "leg run", False, str(tmp_path / "art"), "a")
    assert r["pushed"] is True and r["error"] is None and r["patch"] is None
    _git(b, "fetch", "-q")
    assert "leg run" in _git(b, "log", "--oneline", "origin/main")


def test_push_rejected_twice_reports_error_and_exports_patch(pair, tmp_path):
    bare, a, _ = pair
    hook = bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n"); hook.chmod(0o755)
    (a / "f.txt").write_text("never lands\n")
    r = gs.sync_repo(str(a), "leg run", False, str(tmp_path / "art"), "a")
    assert r["error"] and "push rejected twice" in r["error"].lower()
    assert r["pushed"] is False
    assert os.path.isfile(r["patch"])
