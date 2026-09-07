#!/usr/bin/env python3
"""pr_author.py — D rev B: the cockpit authors a PR instead of writing the live vault.

RULING 4: PRs are the approval verb. The cockpit therefore does not write state — it produces a
branch and a pull request, and the canonical vault changes only when that PR merges.

The seam is `ship.py --vault-root`, which is a free path (`ship.py:219,227`; proven identical to a
live ship by `tests/test_d_vault_root_worktree.py`). We point it at a git worktree of the vault.

Two decisions from D Task 2 (Seth, 2026-09-06) are load-bearing here:

  1. THE REVERT POINTER STAYS IN `state/revert/`. We do NOT pass `--revert-dir`, so ship.py's
     default (`<queue dir>/revert`) keeps writing there. `rewind.py undo_ship` reads it from there
     and needs it for three things a `git revert` cannot do: restore the pre-merge incumbent of a
     merged journal (`rewind.py:269-274`), restore the staging husk so `awaiting` is re-shippable
     (A30, `staging_archived`), and leave the history marker `gate_metrics` counts reverts from.

  2. ONE PR PER STATION — see `group_by_station`. Per item would have made the historical 46-item
     drain 46 PRs; the ledger's own `station_order` is the boundary, and it isolates the
     Paper-Governs silo into a separately reviewable PR.

THE PR IS THE APPROVAL UNIT; THE POINTER IS THE UNDO UNIT. They are deliberately different
granularities. To undo ONE item out of a merged multi-item PR, use `rewind.py undo-ship <id>` —
never `git revert`, which would take the whole station's batch with it. Do not "fix" this mismatch.

THE LIFECYCLE (P1-1, 2026-09-07 assessment). Preparing a PR is not approval, so preparation must
not finalize canonical queue state:

    author_ship        ship --proposal <branch>: files into the worktree, item stays `awaiting`
                       carrying a proposal ref. A dry run, a push failure and a `gh` failure all
                       leave it exactly there — actionable, and re-preparable on a fresh branch.
    PR merged          finalize_proposals(..., "merged")  -> `shipped`
    PR closed unmerged finalize_proposals(..., "closed")  -> proposal + revert pointer dropped,
                       item still `awaiting`

`ship.py finalize` is the SOLE writer of `shipped` on this path, and it is driven by an OBSERVED
outcome, never by a cockpit click — which is why it is not in the dashboard ACTIONS registry.
Before this, the ship call below passed the LIVE queue with `--human-approved`, so merely PREPARING
a PR (even `dry_run=True`) flipped the item to `shipped` with `pr_url: null`.

This module is the ONLY place the cockpit touches git or gh. Everything else stays read-only.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dashboard_server import _connectors  # noqa: E402  (one resolver, no second copy)


class AuthorError(RuntimeError):
    """A git/gh/ship step failed. Never partially reported as success."""


def _run(args, cwd=None):
    r = subprocess.run(args, cwd=str(cwd) if cwd else None, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise AuthorError("%s failed (%d): %s"
                          % (args[0], r.returncode, (r.stderr or r.stdout).strip()[:400]))
    return r.stdout


def _items(env):
    data = json.loads((Path(env) / "state" / "queue.json").read_text(encoding="utf-8"))
    return {it.get("id"): it for it in data.get("queue", [])}


def group_by_station(env, item_ids) -> dict:
    """{station: [ids]} — the batching boundary, from the ledger rather than invented (Task 2 Q2).

    An item with no station falls under "unstationed" rather than being dropped: silently losing an
    approved item is worse than an oddly-named PR.
    """
    items, out = _items(env), {}
    for cid in item_ids:
        station = (items.get(cid) or {}).get("station") or "unstationed"
        out.setdefault(station, []).append(cid)
    return {k: sorted(v) for k, v in sorted(out.items())}


def author_ship(env, item_ids, *, branch, dry_run=False, tools_dir=None) -> dict:
    """Ship `item_ids` into a worktree of the vault, commit, and (unless dry_run) open a PR.

    Returns {branch, worktree, shipped, pr_url}. `pr_url` is None on a dry run.

    KNOWN EDGE, stated rather than handled: the worktree is created from the vault's HEAD, so if the
    live vault has uncommitted changes the incumbent backed up by ship.py is HEAD's version, not
    what is on disk. That is the correct base for a PR — the PR must apply to committed state — but
    it means a ship authored while the vault is dirty backs up a slightly different incumbent than a
    live ship would. Callers should refuse to author while the vault is dirty; enforcing that is
    the caller's job, not this module's.
    """
    env = Path(env)
    tools = Path(tools_dir) if tools_dir else env / "Projects" / "aios" / "engine" / "tools"
    vault_root, kb_map = _connectors(env)
    wt = env / ".worktrees" / branch
    if wt.exists():
        raise AuthorError("worktree already exists: %s" % wt)

    _run(["git", "-C", str(vault_root), "worktree", "add", "-b", branch, str(wt), "HEAD"])
    shipped = []
    for cid in item_ids:
        # NOTE: no --revert-dir. ship.py's default is <queue dir>/revert = state/revert, which is
        # exactly where Task 2 decided the pointer lives and where rewind.py reads it.
        # P1-1 (2026-09-07): --proposal. Preparing a PR is not approval, so this writes the files
        # into the worktree and records `branch` as the item's proposal — it does NOT flip the live
        # queue to `shipped`. Only `finalize_proposals(..., "merged")` does, off an observed merge.
        _run([sys.executable, str(tools / "ship.py"), "ship",
              "--queue", str(env / "state" / "queue.json"),
              "--vault-root", str(wt),
              "--kb-map", json.dumps(kb_map),
              "--id", cid, "--approved-by", "cockpit", "--human-approved",
              "--proposal", branch])
        shipped.append(cid)

    _run(["git", "-C", str(wt), "add", "-A"])
    _run(["git", "-C", str(wt), "commit", "-m",
          "gate: ship %s (authored from the cockpit)" % ", ".join(shipped)])

    if dry_run:
        return {"branch": branch, "worktree": str(wt), "proposed": shipped, "pr_url": None}

    _run(["git", "-C", str(wt), "push", "-u", "origin", branch])
    url = _run(["gh", "pr", "create",
                "--title", "gate: ship %s" % ", ".join(shipped),
                "--body", "Authored from the cockpit. The approval verb is this PR (ruling 4).\n\n"
                          "Undo ONE item with `rewind.py undo-ship <id>`, not `git revert` — the "
                          "revert pointer is per-item and lives in `state/revert/`."],
               cwd=wt).strip()
    return {"branch": branch, "worktree": str(wt), "proposed": shipped, "pr_url": url}


def finalize_proposals(env, item_ids, outcome, *, ref=None, tools_dir=None) -> list:
    """P1-1: apply an OBSERVED PR outcome to the queue. The explicit authority for the
    compensating change, so no accidental direct-write path has to be inferred.

    This is deliberately NOT a dashboard ACTION. A cockpit button would finalize on a click rather
    than on a merge, which is the defect being fixed. Its caller is whatever OBSERVES the outcome
    (`gh pr view --json state,mergedAt`, a merge webhook, a sync pass). Idempotent, so re-running
    an observer over settled state is free.
    """
    tools = Path(tools_dir) if tools_dir else Path(env) / "Projects" / "aios" / "engine" / "tools"
    vault_root, _kb_map = _connectors(env)
    return [json.loads(_run([sys.executable, str(tools / "ship.py"), "finalize",
                             "--queue", str(Path(env) / "state" / "queue.json"),
                             "--id", cid, "--outcome", outcome, "--vault-root", str(vault_root)]
                            + (["--ref", ref] if ref else [])).strip().splitlines()[-1])
            for cid in item_ids]


def author_by_station(env, item_ids, *, dry_run=False, tools_dir=None, stamp=None) -> list:
    """One PR per station (Task 2 Q2). Returns a list of author_ship results, one per station."""
    import datetime
    stamp = stamp or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = []
    for station, ids in group_by_station(env, item_ids).items():
        out.append(author_ship(env, ids, branch="gate-%s-%s" % (station, stamp),
                               dry_run=dry_run, tools_dir=tools_dir))
    return out


def main(argv=None):
    """CLI so the cockpit's ACTIONS registry can keep its argv contract — the dashboard runs
    subprocesses, not callables, and changing that dispatch is a larger blast radius than a CLI."""
    import argparse
    ap = argparse.ArgumentParser(description="Author a gate ship as a PR, one PR per station.")
    ap.add_argument("--env", required=True)
    ap.add_argument("--id", action="append", required=True, dest="ids")
    ap.add_argument("--tools-dir")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    try:
        res = author_by_station(args.env, args.ids, dry_run=args.dry_run,
                                tools_dir=args.tools_dir)
    except AuthorError as e:
        print("pr_author: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
