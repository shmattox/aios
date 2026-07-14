#!/usr/bin/env python3
r"""A23 — a staging file referenced by ANY live queue item is NEVER swept.

The 2026-07-04 incident: the live orphan sweep deleted `03_Dev/wiki/staging/2026-06-21.md`
while item `claude-code-2026-06-21-139995cb` was `awaiting` and pointing at it. The liveness
check was built on two LAST-WRITER-WINS dicts — `stage_by_key[(kb, slug)]` and
`stage_by_draft[path]` — so a live item could be shadowed by a DONE item that iterated later
on the same key (journal/daily slug collisions are routine), and `payload_path` (how a
draftless garden proposal references its staging payload) was never indexed at all.

The fix under test: liveness is a SET-membership check — a file is protected if any LIVE
(non-shipped/rejected/reverted) item references it by `draft_path`, `payload_path`, or its
conflict_key-derived (kb, slug); DONE stages are kept only for reporting. No iteration-order
dependence, fail-closed by construction (an item with a missing/unknown stage counts as live).

Hermetic: everything under tempfile.mkdtemp(); the live install is never touched.
Run: python engine/tools/tests/test_a23_sweep_live_refs.py
"""
import os, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)
import garden_sweep

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok   {name}")
    else:
        FAIL += 1; print(f"  FAIL {name}")


KB_MAP = {"personal": "01_Personal", "familyoffice": "02_FamilyOffice", "dev": "03_Dev"}


def _mkfile(path, body="x\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    return path


def item(iid, stage, kb, ck, **kw):
    it = {"id": iid, "stage": stage, "kb": kb, "conflict_key": ck}
    it.update(kw)
    return it


def main():
    root = tempfile.mkdtemp(prefix="aios-a23-")
    try:
        vault = os.path.join(root, "SecondBrain")
        install = os.path.join(root, "install")
        os.makedirs(os.path.join(install, "state"))

        staging = os.path.join(vault, "03_Dev", "wiki", "staging")
        _mkfile(os.path.join(staging, "2026-06-21.md"))       # the incident shape
        _mkfile(os.path.join(staging, "harness.md"))          # draftless payload shape
        _mkfile(os.path.join(staging, "spent.md"))            # genuinely spent litter
        _mkfile(os.path.join(staging, "stray.md"))            # no queue reference at all
        _mkfile(os.path.join(staging, "no-stage.md"))         # unresolvable stage -> fail closed
        _mkfile(os.path.join(staging, "collide-legacy.md"))   # live, NO draft_path, key collision
        _mkfile(os.path.join(staging, "shared-dp.md"))        # one draft_path, live + DONE items

        sweep_q = [
            # THE 07-04 INCIDENT, exact iteration order: the LIVE item first, then DONE
            # same-slug siblings (journal vs daily cks -> identical (kb, slug)). Last-writer-
            # wins made the final sibling own the verdict and delete the live draft.
            item("live-journal", "awaiting", "dev", "dev/wiki/journal/2026-06-21.md",
                 draft_path="03_Dev/wiki/staging/2026-06-21.md"),
            item("done-daily-1", "rejected", "dev", "dev/wiki/daily/2026-06-21.md"),
            item("done-daily-2", "shipped", "dev", "dev/wiki/daily/2026-06-21.md"),
            # DRAFTLESS garden proposal: payload lives in staging, draft_path null, and its
            # ck slug ("gate-dup") does NOT match the payload filename ("harness").
            item("debloat", "awaiting", "dev", "dev/wiki/entities/gate-dup.md",
                 draftless=True, draft_path=None,
                 payload_path="03_Dev/wiki/staging/harness.md"),
            # a later DONE item colliding with the payload file's basename-derived key
            item("done-harness", "shipped", "dev", "dev/wiki/entities/harness.md"),
            # SAME draft_path shared by a live item (first) and a DONE item (later): the old
            # stage_by_draft dict was last-writer-wins too — the DONE twin must not shadow it
            item("shared-dp-live", "awaiting", "dev", "dev/wiki/journal/shared-dp.md",
                 draft_path="03_Dev/wiki/staging/shared-dp.md"),
            item("shared-dp-done", "shipped", "dev", "dev/wiki/sources/other-slug.md",
                 draft_path="03_Dev/wiki/staging/shared-dp.md"),
            # legacy pre-A13 live item: NO draft_path, protected only by its ck-derived
            # (kb, slug) — which a LATER done sibling must not be able to overwrite
            item("legacy-live", "sorted", "dev", "dev/wiki/journal/collide-legacy.md"),
            item("legacy-done", "shipped", "dev", "dev/wiki/daily/collide-legacy.md"),
            # spent litter: only DONE references -> must still sweep
            item("spent-item", "shipped", "dev", "dev/wiki/sources/spent.md"),
            # fail-closed: an item with NO stage field referencing a file -> treat as live
            item("no-stage", None, "dev", "dev/wiki/sources/no-stage.md"),
            item("no-stage-done-twin", "shipped", "dev", "dev/wiki/knowledge/no-stage.md"),
        ]
        # strip the None stage to simulate a truly missing field
        for it in sweep_q:
            if it["stage"] is None:
                del it["stage"]

        real_load = garden_sweep._load_queue_clean
        garden_sweep._load_queue_clean = lambda qp: (sweep_q, True)
        try:
            _, orphans, _ = garden_sweep.sweep(
                install, ttl_days=7, apply=False, vault_root=vault, kb_map=KB_MAP)
            names = {os.path.basename(p) for p, st in orphans}

            # NOTE: this first check also passed on the PRE-A23 code (the draft_path index
            # already rescued it); it locks the invariant. The regression itself is pinned by
            # the collide-legacy / harness.md / no-stage / shared-dp / reversal checks below.
            check("incident invariant: live draft_path beats later same-slug DONE items",
                  "2026-06-21.md" not in names)
            check("shared draft_path: live item not shadowed by a later DONE twin",
                  "shared-dp.md" not in names)
            check("draftless proposal: payload_path in staging protects the file",
                  "harness.md" not in names)
            check("spent litter (only DONE refs) still flagged", "spent.md" in names)
            check("stray (no refs at all) still flagged", "stray.md" in names)
            check("fail-closed: missing-stage item counts as live", "no-stage.md" not in names)
            check("legacy live item (no draft_path): ck-derived key not shadowed by later DONE",
                  "collide-legacy.md" not in names)

            # order independence: reverse the queue -> identical verdicts
            garden_sweep._load_queue_clean = lambda qp: (list(reversed(sweep_q)), True)
            _, orphans_rev, _ = garden_sweep.sweep(
                install, ttl_days=7, apply=False, vault_root=vault, kb_map=KB_MAP)
            check("order independence: reversed queue yields identical orphan set",
                  {os.path.basename(p) for p, _ in orphans_rev} == names)
        finally:
            garden_sweep._load_queue_clean = real_load

        print(f"\n{PASS} passed, {FAIL} failed")
        sys.exit(1 if FAIL else 0)
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
