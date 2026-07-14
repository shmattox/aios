#!/usr/bin/env python3
r"""A28/rewind — reconcile recognizes the retire/prune lifecycle.

A page shipped through the pipeline and LATER removed ON PURPOSE (garden distill->retire moves
the husk to raw/archive/; an approved prune deletes the page) read to `reconcile` as corruption
("shipped w/o vault file") FOREVER — permanent false-positives that make a blanket live-vault
`reconcile --apply` unsafe (it would rewind + redraft deliberately-retired content). Re-keying
items to archive paths is fenced off by the queue schema (correct fence, wrong escape hatch).

The fix under test (the `draftless` precedent at the awaiting stage, applied to shipped):
  retired marker   = `retired: true` on the item; `_reconcile_scan`'s shipped branch skips it.
                     Stage stays `shipped` (it DID ship — the marker records the lifecycle exit).
  mark-retired op  = sets the marker + a history event, snapshotted (undoable via `undo`),
                     committed through queue_tx. By explicit ids (dies on a missing id) or by
                     --ck conflict_key (marks every SHIPPED item with that ck; 0 matches is a
                     no-op, not an error — gate idempotency). Already-retired items are skipped.
  migration        = `reconcile --migrate-retired`: a shipped-w/o-file item whose slug exists at
                     `<kb>/raw/archive/wiki-sources-retired-*/<slug>.md` (garden_distill.retire's
                     exact archive convention) is EVIDENCED — dry-run reports it; --apply marks
                     it retired instead of rewinding it. No evidence -> stays flagged (a prune
                     case needs an explicit mark-retired; never guess).

Hermetic: everything under tempfile.mkdtemp(). Run: python engine/tools/tests/test_rewind_retired.py
"""
import json, os, shutil, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)
import queue_tx
import rewind

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok   {name}")
    else:
        FAIL += 1; print(f"  FAIL {name}")


def w(root, rel, text="x\n"):
    p = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def load_items(qp):
    return {it["id"]: it for it in queue_tx.load(qp)["queue"]}


def main():
    root = tempfile.mkdtemp(prefix="retired-")
    try:
        vault = os.path.join(root, "vault")
        qp = os.path.join(root, "state", "queue.json")
        snap_dir = os.path.join(root, "state", "rewind")
        KB_MAP = {"dev": "03_Dev"}

        # vault: one healthy ship, one archived husk (distill-retire evidence)
        w(vault, "03_Dev/wiki/knowledge/alive.md", "# alive\n")
        w(vault, "03_Dev/raw/archive/wiki-sources-retired-2026-07-01/old-stub.md", "husk\n")

        os.makedirs(os.path.dirname(qp), exist_ok=True)
        # every shipped item carries draft_path (the A13 drafted-before-awaiting invariant holds
        # on the way back down too: shipped -> awaiting keeps the recorded path, and the A19 sweep
        # having deleted the draft file is what sends the phantom on to `sorted`)
        items = [
            {"id": "a", "stage": "shipped", "kb": "dev",
             "conflict_key": "dev/wiki/knowledge/alive.md",
             "draft_path": "03_Dev/wiki/staging/alive.md"},                       # healthy
            {"id": "b", "stage": "shipped", "kb": "dev", "retired": True,
             "conflict_key": "dev/wiki/sources/gone-marked.md",
             "draft_path": "03_Dev/wiki/staging/gone-marked.md"},                 # marked
            {"id": "c", "stage": "shipped", "kb": "dev",
             "conflict_key": "dev/wiki/sources/old-stub.md",
             "draft_path": "03_Dev/wiki/staging/old-stub.md"},                    # evidenced
            {"id": "d", "stage": "shipped", "kb": "dev",
             "conflict_key": "dev/wiki/knowledge/pruned-page.md",
             "draft_path": "03_Dev/wiki/staging/pruned-page.md"},                 # prune, no evidence
        ]
        with open(qp, "w", encoding="utf-8") as f:
            json.dump({"queue": items}, f)
        check("fixture queue validates", queue_tx.validate(queue_tx.load(qp)) is None)

        # 1. dry-run scan: marker skips b; a has its file; c+d flag
        _, ship_missing = rewind.reconcile(qp, vault, apply=False, snap_dir=snap_dir, kb_map=KB_MAP)
        check("retired marker skips the deliberately-removed ship", sorted(ship_missing) == ["c", "d"])

        # 2. migration dry-run: c is evidenced (archived husk), d is not; queue unchanged
        rep = rewind.reconcile(qp, vault, apply=False, snap_dir=snap_dir, kb_map=KB_MAP,
                               migrate_retired=True)
        check("migration dry-run reports the evidenced case",
              sorted(rep[1]) == ["c", "d"] and load_items(qp)["c"].get("retired") is not True)

        # 3. migration apply: c marked retired (not rewound), d rewound by the heal loop
        rewind.reconcile(qp, vault, apply=True, snap_dir=snap_dir, kb_map=KB_MAP,
                         migrate_retired=True)
        after = load_items(qp)
        check("evidenced case marked retired, stage stays shipped",
              after["c"].get("retired") is True and after["c"]["stage"] == "shipped")
        check("unevidenced case healed normally (loop settles the phantom at sorted)",
              after["d"]["stage"] == "sorted" and after["d"].get("retired") is not True)
        check("healthy + pre-marked untouched",
              after["a"]["stage"] == "shipped" and after["b"]["stage"] == "shipped")
        check("queue validates after migration", queue_tx.validate(queue_tx.load(qp)) is None)

        # 4. mark-retired by ck (the gate's flow, prune case): restore d to shipped first
        rewind.reset(qp, ["d"], "sorted", "test reset", snap_dir)
        d = load_items(qp)["d"]; d["stage"] = "shipped"
        d["conflict_key"] = "dev/wiki/knowledge/pruned-page.md"
        obj = queue_tx.load(qp)
        obj["queue"] = [d if it["id"] == "d" else it for it in obj["queue"]]
        with open(qp, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        marked, sid = rewind.mark_retired(qp, ck="dev/wiki/knowledge/pruned-page.md",
                                          note="prune shipped: page deleted on approval",
                                          snap_dir=snap_dir)
        check("mark-retired --ck reports what it marked", marked == ["d"] and sid)
        after = load_items(qp)
        check("mark-retired --ck marks the shipped item",
              after["d"].get("retired") is True
              and any("retired" in h.get("note", "") for h in after["d"].get("history", [])))
        _, ship_missing = rewind.reconcile(qp, vault, apply=False, snap_dir=snap_dir, kb_map=KB_MAP)
        check("scan is clean once all lifecycle exits are marked", ship_missing == [])
        check("mark-retired by ck on 0 matches is a no-op, not an error",
              rewind.mark_retired(qp, ck="dev/wiki/knowledge/no-such.md",
                                  snap_dir=snap_dir)[0] == [])

        # 5. the marker is undoable (snapshot round-trip)
        rewind.undo(qp, sid, snap_dir)
        check("undo restores the pre-marker item",
              load_items(qp)["d"].get("retired") is not True)

        # 6. mark-retired by explicit ids
        marked, _ = rewind.mark_retired(qp, ids=["d"], note="manual", snap_dir=snap_dir)
        check("mark-retired by id marks and reports", marked == ["d"]
              and load_items(qp)["d"].get("retired") is True)

        # 7. a manual reset resurrects the item -> the lifecycle marker must clear with it
        rewind.reset(qp, ["d"], "sorted", "bring it back", snap_dir)
        check("reset clears the retired marker (a rewound item is live again)",
              load_items(qp)["d"].get("retired") is not True)
        check("queue validates at the end", queue_tx.validate(queue_tx.load(qp)) is None)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
