#!/usr/bin/env python3
"""A13 drafted-before-awaiting guard — an item may only ENTER stage 'awaiting' carrying a
draft_path (the vault-relative staging file the ingest run wrote). Prevention leg of the
"godaddy class" desync that rewind.reconcile() recovers from: on 2026-07-02 an in-session run
bulk-advanced 48 sorted items to awaiting with no drafts on disk; gate-auto then rejected them
all "no draft found". Items ALREADY at awaiting (legacy, pre-guard) are grandfathered —
the guard fires on the transition, not the steady state."""
import json, os, sys, tempfile, shutil, glob

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../engine/tools
sys.path.insert(0, HARNESS)
import queue_tx
import rewind

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ok  " if cond else " FAIL ") + name)

def refused(fn, *a, **kw):
    """queue_tx refusals call _die -> SystemExit(1). True if the call was refused."""
    try:
        fn(*a, **kw)
        return False
    except SystemExit as e:
        return e.code == 1

def mk_item(i, stage, ck=None, lane=None, **extra):
    it = {"id": i, "stage": stage,
          "history": [{"ts": "2026-07-01T00:00:00Z", "stage": "captured"}]}
    if ck: it["conflict_key"] = ck
    if lane is not None: it["lane"] = lane
    it.update(extra)
    return it

def apply_update(live, items, mode="update"):
    itf = live + ".items"
    json.dump(items, open(itf, "w", encoding="utf-8"), indent=2)
    queue_tx._apply_items(live, queue_tx._read_items(itf), mode)

def by_id(live):
    return {it["id"]: it for it in queue_tx.load(live)["queue"]}

d = tempfile.mkdtemp(prefix="qtx_a13_")
try:
    live = os.path.join(d, "queue.json")

    # Seed a pre-guard queue: one legacy item ALREADY awaiting with no draft_path (grandfathered),
    # two sorted items ready to be drafted.
    seed = {"queue": [
        mk_item("legacy-awaiting", "awaiting", "personal/wiki/sources/legacy.md", "review",
                recommended="hold", rec_reason="pre-guard legacy"),
        mk_item("s1", "sorted", "dev/wiki/sources/s1.md", "auto-ship"),
        mk_item("s2", "sorted", "dev/wiki/sources/s2.md", "auto-ship"),
    ]}
    json.dump(seed, open(live, "w", encoding="utf-8"), indent=2)
    check("seed: legacy awaiting item folds in without draft_path (steady state legal)",
          queue_tx.validate(queue_tx.load(live)) is None)

    # 1. THE BUG: sorted -> awaiting with NO draft_path must be refused, live untouched.
    bad = dict(by_id(live)["s1"]); bad.update(stage="awaiting", recommended="approve")
    check("guard: entering awaiting without draft_path is REFUSED",
          refused(apply_update, live, [bad]))
    check("guard: refusal leaves the item at 'sorted' on disk",
          by_id(live)["s1"]["stage"] == "sorted")

    # 2. The contract path: same transition WITH a vault-relative draft_path is applied.
    good = dict(by_id(live)["s1"]); good.update(stage="awaiting", recommended="approve",
                                                draft_path="03_Dev/wiki/staging/s1.md")
    apply_update(live, [good])
    it = by_id(live)["s1"]
    check("guard: entering awaiting WITH draft_path applies",
          it["stage"] == "awaiting" and it["draft_path"] == "03_Dev/wiki/staging/s1.md")

    # 3. Grandfather: mutating a legacy already-awaiting item (still no draft_path) stays legal —
    #    the gate rewrites awaiting items (recommended/rec_reason) without re-drafting.
    leg = dict(by_id(live)["legacy-awaiting"]); leg.update(rec_reason="gate touched")
    apply_update(live, [leg])
    check("grandfather: updating an already-awaiting legacy item is allowed",
          by_id(live)["legacy-awaiting"]["rec_reason"] == "gate touched")

    # 4. add/upsert of a NEW item directly at awaiting is the same transition — guarded.
    check("guard: add of a new item at awaiting without draft_path is REFUSED",
          refused(apply_update, live,
                  [mk_item("new-await", "awaiting", "dev/wiki/sources/new.md", "auto-ship")], "add"))

    # 5. Traversal fence: draft_path with a '..' segment is rejected by validate.
    trav = dict(by_id(live)["s2"]); trav.update(stage="awaiting",
                                                draft_path="../../etc/passwd.md")
    check("fence: draft_path with '..' segment is REFUSED",
          refused(apply_update, live, [trav]))
    check("fence: refusal leaves s2 at 'sorted'", by_id(live)["s2"]["stage"] == "sorted")

    # 6. Declared exception: a draftless action-proposal (garden de-bloat/prune — the whole action
    #    lives in rec_reason, there is no page draft by design) enters awaiting with draftless:true.
    apply_update(live, [mk_item("prune-prop", "awaiting", "personal/wiki/sources/old-stub.md",
                                "review", source="garden", recommended="approve",
                                rec_reason="prune: watch-later stub superseded", draftless=True)],
                 "add")
    check("draftless: declared action-proposal enters awaiting without draft_path",
          by_id(live)["prune-prop"]["stage"] == "awaiting")

    # 7. reconcile honors the same contract: draftless awaiting items are NOT flagged as desyncs;
    #    an awaiting item whose declared draft_path exists on disk is clean; a missing one is flagged.
    vault = os.path.join(d, "vault")
    sp = os.path.join(vault, "03_Dev", "wiki", "staging", "s1.md")
    os.makedirs(os.path.dirname(sp), exist_ok=True)
    open(sp, "w", encoding="utf-8").write("# s1 draft\n")
    staging_missing, ship_missing = rewind._reconcile_scan(queue_tx.load(live), live, vault)
    check("reconcile: draftless proposal not flagged as awaiting-without-draft",
          "prune-prop" not in staging_missing)
    check("reconcile: awaiting item with draft_path present on disk is clean",
          "s1" not in staging_missing)
    check("reconcile: legacy awaiting item with no draft anywhere IS flagged",
          "legacy-awaiting" in staging_missing)

    # 8. The bulk whole-queue `commit` path honors the same invariant (the 2026-07-02 incident
    #    primitive was a bulk advance): flipping s2 to awaiting with no draft_path via commit is
    #    refused; with draft_path it lands. Legacy already-awaiting items pass through untouched.
    cur = queue_tx.load(live)["queue"]
    proposed = [dict(it, stage="awaiting") if it["id"] == "s2" else it for it in cur]
    pf = os.path.join(d, "proposed.json")
    json.dump({"queue": proposed}, open(pf, "w", encoding="utf-8"), indent=2)
    check("commit: bulk flip to awaiting without draft_path is REFUSED",
          refused(queue_tx.commit, pf, live))
    check("commit: refusal leaves s2 at 'sorted'", by_id(live)["s2"]["stage"] == "sorted")
    proposed = [dict(it, stage="awaiting", draft_path="03_Dev/wiki/staging/s2.md")
                if it["id"] == "s2" else it for it in cur]
    json.dump({"queue": proposed}, open(pf, "w", encoding="utf-8"), indent=2)
    queue_tx.commit(pf, live)
    check("commit: bulk flip WITH draft_path applies (legacy awaiting items pass through)",
          by_id(live)["s2"]["stage"] == "awaiting")

    # 9. Full queue still validates.
    check("queue validates OK after the wave", queue_tx.validate(queue_tx.load(live)) is None)

finally:
    shutil.rmtree(d, ignore_errors=True)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    sys.exit(1)
