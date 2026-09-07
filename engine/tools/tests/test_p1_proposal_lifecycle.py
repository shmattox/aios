"""P1 lifecycle fixture — prepare, failure, rejection, merge, retry, and post-merge undo.

The two P1 findings of the 2026-09-07 independent assessment
(`docs/superpowers/findings/2026-09-07-claude-aios-independent-assessment.md`):

  P1-1  Preparing a PR flipped the LIVE queue item to `shipped` before commit, push or PR
        creation — `author_ship(..., dry_run=True)` included. An unapproved proposal claimed
        completion, and a push failure or a closed PR left that lie in operational state.
  P1-2  The same call wrote an absolute proposal-WORKTREE path into `state/revert/<id>.json`.
        Post-merge undo therefore deleted the worktree copy, reported success, and left the
        canonical page standing; delete the worktree and the destination is gone entirely.

Every leg here asserts on the LIVE QUEUE, not only on the vault — the assessment's point is that
an unchanged vault was never sufficient evidence.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))
import pr_author  # noqa: E402

TOOLS = Path(__file__).resolve().parents[1]
DRAFT = "---\ntype: note\ntitle: Note\n---\n\nbody line\n"
INCUMBENT = "---\ntype: note\ntitle: Note\n---\n\nTHE INCUMBENT MUST COME BACK\n"


def _git(*args, cwd, check=True):
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8")
    if check:
        assert r.returncode == 0, "git %s: %s" % (args, r.stderr)
    return r


@pytest.fixture()
def env(tmp_path):
    """An env root whose vault is a git repo: one awaiting item, its staging draft, and an
    already-canonical incumbent page the ship will replace (so undo has content to restore)."""
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "connectors.yaml").write_text(
        "vault:\n  live_root: \"SecondBrain\"\n  live_kb_map:\n    dev: \"03_Dev\"\n",
        encoding="utf-8")
    vault = tmp_path / "SecondBrain"
    (vault / "03_Dev" / "wiki" / "staging").mkdir(parents=True)
    (vault / "03_Dev" / "wiki" / "staging" / "note.md").write_text(DRAFT, encoding="utf-8")
    (vault / "03_Dev" / "wiki" / "note.md").write_text(INCUMBENT, encoding="utf-8")
    _git("init", "-q", "-b", "main", cwd=vault)
    _git("config", "user.email", "t@t", cwd=vault)
    _git("config", "user.name", "t", cwd=vault)
    _git("add", "-A", cwd=vault)
    _git("commit", "-qm", "seed", cwd=vault)

    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "queue.json").write_text(json.dumps({"queue": [
        {"id": "i1", "stage": "awaiting", "lane": "review",
         "conflict_key": "dev/wiki/note", "draft_path": "03_Dev/wiki/staging/note.md",
         "station": "system"},
    ]}), encoding="utf-8")
    return tmp_path


def _item(env):
    q = json.loads((env / "state" / "queue.json").read_text(encoding="utf-8"))
    return next(it for it in q["queue"] if it["id"] == "i1")


def _canonical(env):
    return env / "SecondBrain" / "03_Dev" / "wiki" / "note.md"


def _finalize(env, outcome, ref=None):
    return pr_author.finalize_proposals(env, ["i1"], outcome, ref=ref, tools_dir=TOOLS)[0]


def _merge_into_the_live_vault(env, branch):
    """What the PR merging actually does to the canonical vault."""
    _git("merge", "--no-ff", "-q", "-m", "merge " + branch, branch, cwd=env / "SecondBrain")


# ---------------------------------------------------------------- 1. prepare


def test_prepare_does_not_finalize_the_live_queue(env):
    """P1-1, the reproduction inverted: before `awaiting`, after a DRY RUN still `awaiting`."""
    assert _item(env)["stage"] == "awaiting"
    r = pr_author.author_ship(env, ["i1"], branch="p1-prep", dry_run=True, tools_dir=TOOLS)
    assert r["pr_url"] is None
    it = _item(env)
    assert it["stage"] == "awaiting", (
        "PREPARING a PR must not finalize the live queue — nothing is approved until it merges")
    assert it["proposal"]["ref"] == "p1-prep", "the proposal reference must be recorded instead"
    # the files really were written, so this is not passing because nothing happened
    assert (Path(r["worktree"]) / "03_Dev" / "wiki" / "note.md").read_text(
        encoding="utf-8") != INCUMBENT
    assert _canonical(env).read_text(encoding="utf-8") == INCUMBENT, "live vault must not change"


def test_the_pointer_is_vault_relative_not_a_worktree_path(env):
    """P1-2 at the source: the recorded identity must not name the throwaway checkout."""
    r = pr_author.author_ship(env, ["i1"], branch="p1-ptr", dry_run=True, tools_dir=TOOLS)
    ptr = json.loads((env / "state" / "revert" / "i1.json").read_text(encoding="utf-8"))
    assert ptr["shipped_rel"] == "03_Dev/wiki/note.md"
    assert ptr["from_staging_rel"] == "03_Dev/wiki/staging/note.md"
    assert r["worktree"] in ptr["shipped_path"], (
        "the legacy absolute field is expected to be worktree-bound — which is exactly why "
        "rewind must prefer the relative one")


# ------------------------------------------------- 2. failure   3. rejection


def test_a_prepare_that_fails_to_push_leaves_the_item_actionable(env):
    """No `origin` remote, so the push step raises. The item must survive as workable."""
    with pytest.raises(pr_author.AuthorError):
        pr_author.author_ship(env, ["i1"], branch="p1-push", dry_run=False, tools_dir=TOOLS)
    it = _item(env)
    assert it["stage"] == "awaiting", "a failed push must not leave a `shipped` claim behind"
    assert _canonical(env).read_text(encoding="utf-8") == INCUMBENT
    # actionable: the draft is still in the live vault, so a fresh prepare can run
    assert (env / "SecondBrain" / "03_Dev" / "wiki" / "staging" / "note.md").is_file()
    r = pr_author.author_ship(env, ["i1"], branch="p1-push-retry", dry_run=True, tools_dir=TOOLS)
    assert r["shipped"] == ["i1"] and _item(env)["proposal"]["ref"] == "p1-push-retry"


def test_a_pr_closed_unmerged_leaves_the_item_actionable(env):
    pr_author.author_ship(env, ["i1"], branch="p1-closed", dry_run=True, tools_dir=TOOLS)
    out = _finalize(env, "closed", ref="p1-closed")
    assert out["ok"] and out["stage"] == "awaiting"
    it = _item(env)
    assert it["stage"] == "awaiting"
    assert "proposal" not in it, "a closed PR must clear the stale proposal reference"
    assert not (env / "state" / "revert" / "i1.json").exists(), (
        "the pointer describes a canonical write that never happened — leaving it arms undo-ship "
        "against a page nobody shipped")
    assert _canonical(env).read_text(encoding="utf-8") == INCUMBENT
    # and it is genuinely workable again
    pr_author.author_ship(env, ["i1"], branch="p1-closed-retry", dry_run=True, tools_dir=TOOLS)
    assert _item(env)["proposal"]["ref"] == "p1-closed-retry"
    assert _finalize(env, "closed")["idempotent"] is False
    assert _finalize(env, "closed")["idempotent"] is True, "closed must be idempotent"


# ------------------------------------------------------ 4. merge  5. retry


def test_only_an_observed_merge_finalizes_and_it_is_idempotent(env):
    pr_author.author_ship(env, ["i1"], branch="p1-merge", dry_run=True, tools_dir=TOOLS)
    assert _item(env)["stage"] == "awaiting"
    _merge_into_the_live_vault(env, "p1-merge")
    assert _item(env)["stage"] == "awaiting", "merging the branch alone does not move the queue"

    first = _finalize(env, "merged", ref="p1-merge")
    assert first["stage"] == "shipped" and first["idempotent"] is False
    it = _item(env)
    assert it["stage"] == "shipped"
    assert it["history"][-1]["approved_by"] == "pr-merge"

    second = _finalize(env, "merged", ref="p1-merge")
    assert second["stage"] == "shipped" and second["idempotent"] is True, (
        "an observer re-running over settled state must be free")
    assert _item(env)["history"][-1]["approved_by"] == "pr-merge", "no duplicate flip"


def test_finalize_merged_refuses_an_item_with_no_proposal(env):
    """The guard that keeps `finalize` from becoming a second accidental direct-write path."""
    r = subprocess.run([sys.executable, str(TOOLS / "ship.py"), "finalize",
                        "--queue", str(env / "state" / "queue.json"),
                        "--id", "i1", "--outcome", "merged"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode != 0 and "no proposal" in r.stdout + r.stderr
    assert _item(env)["stage"] == "awaiting"


def test_finalize_closed_refuses_to_un_ship(env):
    pr_author.author_ship(env, ["i1"], branch="p1-noun", dry_run=True, tools_dir=TOOLS)
    _merge_into_the_live_vault(env, "p1-noun")
    _finalize(env, "merged")
    with pytest.raises(pr_author.AuthorError):
        _finalize(env, "closed")
    assert _item(env)["stage"] == "shipped"
    assert (env / "state" / "revert" / "i1.json").exists(), "the undo pointer must survive"


# --------------------------------------------- post-merge undo, worktree gone


def test_post_merge_undo_restores_the_canonical_after_the_worktree_is_deleted(env):
    """P1-2's reproduction, end to end. Undo must act on the CANONICAL page, and must still work
    when the proposal checkout it was authored in no longer exists."""
    r = pr_author.author_ship(env, ["i1"], branch="p1-undo", dry_run=True, tools_dir=TOOLS)
    _merge_into_the_live_vault(env, "p1-undo")
    _finalize(env, "merged", ref="p1-undo")
    assert _canonical(env).read_text(encoding="utf-8") != INCUMBENT, "the merge landed"

    # the proposal checkout is gone — its path is not a destination any more
    _git("worktree", "remove", "--force", r["worktree"], cwd=env / "SecondBrain")
    assert not Path(r["worktree"]).exists()

    u = subprocess.run(
        [sys.executable, str(TOOLS / "rewind.py"), "undo-ship",
         str(env / "state" / "queue.json"), "i1", str(env / "SecondBrain"),
         str(env / "state" / "revert"), "awaiting",
         "--kb-map", json.dumps({"dev": "03_Dev"})],
        capture_output=True, text=True, encoding="utf-8")
    assert u.returncode == 0, u.stdout + u.stderr

    assert _item(env)["stage"] == "awaiting"
    assert _canonical(env).read_text(encoding="utf-8") == INCUMBENT, (
        "undo reported success but the canonical page kept the shipped content — this is the "
        "exact P1-2 failure: it used to delete the worktree copy instead")
    assert (env / "SecondBrain" / "03_Dev" / "wiki" / "staging" / "note.md").is_file(), (
        "the staging husk must be restored to the LIVE vault so `awaiting` is re-shippable")


def test_post_merge_undo_deletes_a_page_that_had_no_incumbent(env):
    """The other undo branch: no prior content to preserve, so the canonical page is REMOVED —
    from the live vault, not from a worktree. Guards against 'restore' passing vacuously."""
    _canonical(env).unlink()
    _git("commit", "-aqm", "drop incumbent", cwd=env / "SecondBrain")
    r = pr_author.author_ship(env, ["i1"], branch="p1-new", dry_run=True, tools_dir=TOOLS)
    _merge_into_the_live_vault(env, "p1-new")
    _finalize(env, "merged")
    assert _canonical(env).is_file(), "the merge created the page"
    shutil.rmtree(Path(r["worktree"]), ignore_errors=True)
    _git("worktree", "prune", cwd=env / "SecondBrain")

    u = subprocess.run(
        [sys.executable, str(TOOLS / "rewind.py"), "undo-ship",
         str(env / "state" / "queue.json"), "i1", str(env / "SecondBrain"),
         str(env / "state" / "revert"), "awaiting",
         "--kb-map", json.dumps({"dev": "03_Dev"})],
        capture_output=True, text=True, encoding="utf-8")
    assert u.returncode == 0, u.stdout + u.stderr
    assert not _canonical(env).exists(), "undo must remove the LIVE canonical page"
    assert _item(env)["stage"] == "awaiting"
