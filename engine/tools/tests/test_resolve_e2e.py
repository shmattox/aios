# engine/tools/tests/test_resolve_e2e.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import resolve_sweep as rs, resolve_verdict as rv
import resolve_fetch as rf

KW = ["insurance", "premium", "policy", "renew"]

# Simulates the spine: sweep flags -> (model assembles typed evidence, stubbed here) -> verdict.
# Two tasks prove the fan-out set resolves independently.
EVIDENCE_BY_TASK = {
    "t1": ("annual-premium", [
        {"source": "drive", "ref": "F1", "says": "$4,200/yr", "value": 4200.0,
         "qty": "annual-premium", "tier": "paper", "executed": True},
        {"source": "trello", "ref": "C1", "says": "$4,200", "value": 4200.0,
         "qty": "annual-premium", "tier": "verbal", "executed": False}]),
    "t2": ("annual-premium", [
        {"source": "drive", "ref": "F9", "says": "$8,000/yr", "value": 8000.0,
         "qty": "annual-premium", "tier": "paper", "executed": True},
        {"source": "trello", "ref": "C9", "says": "$9,500", "value": 9500.0,
         "qty": "annual-premium", "tier": "verbal", "executed": False}]),
}

def test_spine_flags_then_verdicts_two_tasks():
    tasks = [
        {"id": "t1", "title": "Pay property insurance $4,200"},
        {"id": "t2", "title": "Confirm Bayview insurance premium"},
        {"id": "t3", "title": "Call mom"},
    ]
    flagged = rs.sweep(tasks, KW, set())
    assert {f["id"] for f in flagged} == {"t1", "t2"}

    dossiers = {}
    for f in flagged:                       # each task resolves independently (fan-out unit)
        claim_qty, evidence = EVIDENCE_BY_TASK[f["id"]]
        dossiers[f["id"]] = rv.compute_verdict(claim_qty, evidence)

    assert dossiers["t1"]["verdict"] == "papered"      # clean -> cite the declaration
    assert dossiers["t2"]["verdict"] == "conflict"     # doc != card -> hold, wait


ENTITY = """---
title: Bayview
links:
  drive: ["F1 — 2024 insurance declaration"]
  trello: ["https://trello.com/c/abc — Bayview OPS"]
  aliases: ["Bayview"]
---
body
"""

def test_fetch_produces_candidates_the_pipeline_can_use():
    out = rf.candidates_for(ENTITY)
    assert out["has_crosswalk"] is True
    srcs = {c["source"] for c in out["candidates"]}
    assert {"drive", "trello"} <= srcs
    drive = [c for c in out["candidates"] if c["source"] == "drive"][0]
    assert drive["ref"] == "F1"


if __name__ == "__main__":
    # also runnable without pytest, matching the repo's other test files (suite_test.py runs each as a subprocess)
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print("  ok  " + fn.__name__)
        except Exception:
            failed += 1; print(" FAIL " + fn.__name__); traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
