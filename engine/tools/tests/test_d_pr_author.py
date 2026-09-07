"""D rev B Task 3 — the cockpit authors a PR instead of writing the live vault.

The invariant every test here defends: after a cockpit action, the LIVE vault is unchanged. The
change exists only on a branch until its PR merges. If any of these fail, the cockpit is writing
state again and ruling 4 is broken.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))
import pr_author  # noqa: E402

TOOLS = Path(__file__).resolve().parents[1]
DRAFT = "---\ntype: note\ntitle: Note\n---\n\nbody line\n"


def _git(*args, cwd):
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8")
    assert r.returncode == 0, "git %s: %s" % (args, r.stderr)
    return r.stdout


@pytest.fixture()
def env(tmp_path):
    """An env root: profile, a queue with two awaiting items in different stations, and a
    SecondBrain git repo holding both drafts."""
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "connectors.yaml").write_text(
        "vault:\n  live_root: \"SecondBrain\"\n  live_kb_map:\n"
        "    dev: \"03_Dev\"\n    personal: \"01_Personal\"\n", encoding="utf-8")
    vault = tmp_path / "SecondBrain"
    for kb_dir, slug in (("03_Dev", "note"), ("01_Personal", "pnote")):
        d = vault / kb_dir / "wiki" / "staging"
        d.mkdir(parents=True)
        (d / (slug + ".md")).write_text(DRAFT, encoding="utf-8")
    _git("init", "-q", "-b", "main", cwd=vault)
    _git("config", "user.email", "t@t", cwd=vault)
    _git("config", "user.name", "t", cwd=vault)
    _git("add", "-A", cwd=vault)
    _git("commit", "-qm", "seed", cwd=vault)

    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "queue.json").write_text(json.dumps({"queue": [
        {"id": "i1", "stage": "awaiting", "lane": "auto-ship",
         "conflict_key": "dev/wiki/note", "draft_path": "03_Dev/wiki/staging/note.md",
         "station": "system"},
        {"id": "i2", "stage": "awaiting", "lane": "auto-ship",
         "conflict_key": "personal/wiki/pnote", "draft_path": "01_Personal/wiki/staging/pnote.md",
         "station": "personal"},
    ]}), encoding="utf-8")
    return tmp_path


def _live_tree(env):
    return sorted((p.relative_to(env / "SecondBrain").as_posix(),
                   p.read_text(encoding="utf-8"))
                  for p in (env / "SecondBrain").rglob("*.md"))


def test_dry_run_ships_into_the_worktree_and_never_the_live_vault(env):
    before = _live_tree(env)
    r = pr_author.author_ship(env, ["i1"], branch="h8104-t1", dry_run=True, tools_dir=TOOLS)
    assert r["proposed"] == ["i1"]
    assert r["pr_url"] is None, "a dry run must not open a PR"
    wt = Path(r["worktree"])
    assert (wt / "03_Dev" / "wiki" / "note.md").is_file(), "the ship did not land in the worktree"
    assert _live_tree(env) == before, "THE LIVE VAULT MUST NOT CHANGE until the PR merges"


def test_the_branch_carries_a_commit(env):
    r = pr_author.author_ship(env, ["i1"], branch="h8104-t2", dry_run=True, tools_dir=TOOLS)
    log = _git("log", "--oneline", "-1", cwd=r["worktree"])
    assert "i1" in log, "the commit message should name what shipped: %r" % log
    assert _git("status", "--porcelain", cwd=r["worktree"]).strip() == "", \
        "the worktree should be clean after the commit"


def test_stations_group_into_separate_batches(env):
    """Task 2's decision: one PR per STATION, not per item and not one for everything."""
    groups = pr_author.group_by_station(env, ["i1", "i2"])
    assert groups == {"personal": ["i2"], "system": ["i1"]}


def test_revert_pointer_stays_in_state_not_the_branch(env):
    """Task 2's other decision. rewind.py reads state/revert; the pointer must not travel."""
    r = pr_author.author_ship(env, ["i1"], branch="h8104-t3", dry_run=True, tools_dir=TOOLS)
    assert not (Path(r["worktree"]) / "revert").exists(), \
        "the revert pointer must not be written into the branch"
    assert (env / "state" / "revert").is_dir(), "the pointer belongs in state/revert"
