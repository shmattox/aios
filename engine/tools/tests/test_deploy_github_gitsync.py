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
