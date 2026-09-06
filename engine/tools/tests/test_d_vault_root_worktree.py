"""D rev B Task 1 — a ship into an ALTERNATE vault root is identical to a live ship.

rev B routes every gated write through `ship.py ship --vault-root <worktree>` so the canonical vault
changes only when the PR merges. That rests on `vault_root` being honoured as a free path
(`ship.py:219,227`) — documented, but only the live path is exercised today. If the two roots
diverge, rev B's mechanism is invalid and the rest of the plan must not be built.

Two scratch roots rather than a real git worktree: the property under test is that ship writes
wherever it is pointed, not that git works.
"""
import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]

KB_MAP = {"dev": "03_Dev"}
DRAFT_REL = "03_Dev/wiki/staging/note.md"
DRAFT_TEXT = "---\ntype: note\ntitle: Note\n---\n\nbody line\n"


def _build_root(base):
    """An env root with a vault containing one staged draft, and a queue holding one awaiting item."""
    vault = base / "vault"
    draft = vault / DRAFT_REL
    draft.parent.mkdir(parents=True)
    draft.write_text(DRAFT_TEXT, encoding="utf-8")
    queue = base / "queue.json"
    queue.write_text(json.dumps({"queue": [{
        "id": "i1",
        "stage": "awaiting",
        "lane": "auto-ship",
        "conflict_key": "dev/wiki/note",
        "draft_path": DRAFT_REL,
    }]}), encoding="utf-8")
    return vault, queue


def _ship(queue, vault, revert):
    return subprocess.run(
        [sys.executable, str(TOOLS / "ship.py"), "ship",
         "--queue", str(queue), "--vault-root", str(vault),
         "--kb-map", json.dumps(KB_MAP),
         "--id", "i1", "--approved-by", "test",
         "--revert-dir", str(revert)],
        capture_output=True, text=True, encoding="utf-8")


def _tree(vault):
    return sorted((p.relative_to(vault).as_posix(), p.read_text(encoding="utf-8"))
                  for p in vault.rglob("*.md"))


def test_alternate_vault_root_produces_the_same_tree(tmp_path):
    """The whole of rev B rests on this: point ship somewhere else and get the same result."""
    trees = {}
    for name in ("live", "worktree"):
        base = tmp_path / name
        base.mkdir()
        vault, queue = _build_root(base)
        r = _ship(queue, vault, base / "revert")
        assert r.returncode == 0, "ship failed in %s root: %s" % (name, r.stdout + r.stderr)
        trees[name] = _tree(vault)
    assert trees["live"] == trees["worktree"], (
        "a ship into an alternate vault root must be byte-identical to a live ship")
    # and it actually shipped something, so the comparison is not two empty trees
    assert any("wiki/note.md" in rel for rel, _ in trees["live"]), trees["live"]


def test_the_shipped_target_lands_under_the_given_root(tmp_path):
    """Guards the vacuous-pass mode: equal trees would also satisfy the test above if ship
    silently wrote nothing, or wrote outside the root entirely."""
    base = tmp_path / "only"
    base.mkdir()
    vault, queue = _build_root(base)
    r = _ship(queue, vault, base / "revert")
    assert r.returncode == 0, r.stdout + r.stderr
    target = vault / "03_Dev" / "wiki" / "note.md"
    assert target.is_file(), "ship did not write the target under the vault root it was given"
    shipped = target.read_text(encoding="utf-8")
    assert "body line" in shipped, "the draft's body did not reach the target"
    # NOT byte-equal to the draft: ship enriches frontmatter (it adds `explored: true`). The point
    # here is that real content landed under the ROOT WE GAVE IT, so the parity test above is
    # comparing two real trees rather than two empty ones.
    assert "type: note" in shipped
    assert not (vault.parent / "03_Dev").exists(), "ship wrote outside the vault root it was given"
