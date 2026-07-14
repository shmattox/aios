import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # .../engine/tools
import resolve_verdict as rv

PAPER = {"source": "drive", "ref": "F1", "says": "$4,200/yr", "value": 4200.0,
         "qty": "annual-premium", "tier": "paper", "executed": True}
TRELLO_SAME = {"source": "trello", "ref": "C1", "says": "$4,200", "value": 4200.0,
               "qty": "annual-premium", "tier": "verbal", "executed": False}
TRELLO_DIFF = {"source": "trello", "ref": "C1", "says": "$4,500", "value": 4500.0,
               "qty": "annual-premium", "tier": "verbal", "executed": False}
TRELLO_MONTHLY = {"source": "trello", "ref": "C1", "says": "$350/mo", "value": 350.0,
                  "qty": "monthly-premium", "tier": "verbal", "executed": False}
PAPER2_DIFF = {"source": "drive", "ref": "F2", "says": "$4,900/yr", "value": 4900.0,
               "qty": "annual-premium", "tier": "paper", "executed": True}
UNTYPED = {"source": "trello", "ref": "C9", "says": "$4,200", "value": 4200.0,
           "qty": None, "tier": "verbal", "executed": False}
PAPER_DUP = {"source": "drive", "ref": "F3", "says": "$4,200/yr", "value": 4200.0,
             "qty": "annual-premium", "tier": "paper", "executed": True}

# The verdict is the whole contract (the former auto-promote boolean was retired 2026-07-10 —
# resolve-fate decision; the verdict is advisory, every economic promotion holds for approval).

def test_clean_match_is_papered():
    out = rv.compute_verdict("annual-premium", [PAPER, TRELLO_SAME])
    assert out["verdict"] == "papered"
    assert "drive:F1" in out["canonical"]

def test_doc_conflicts_with_card():
    out = rv.compute_verdict("annual-premium", [PAPER, TRELLO_DIFF])
    assert out["verdict"] == "conflict"
    assert "4,500" in out["conflict"] or "4500" in out["conflict"]

def test_two_candidate_docs_conflict():
    out = rv.compute_verdict("annual-premium", [PAPER, PAPER2_DIFF])
    assert out["verdict"] == "conflict"

def test_qty_mismatch_is_not_a_conflict():
    # a monthly figure is a DIFFERENT quantity — it must NOT create a false conflict
    out = rv.compute_verdict("annual-premium", [PAPER, TRELLO_MONTHLY])
    assert out["verdict"] == "papered"

def test_verbal_only_no_paper():
    out = rv.compute_verdict("annual-premium", [TRELLO_SAME])
    assert out["verdict"] == "verbal-only"
    assert out["canonical"] is None

def test_silent_when_no_aligned_evidence():
    out = rv.compute_verdict("annual-premium", [])
    assert out["verdict"] == "silent"

def test_untyped_candidate_blocks_clean_verdict():
    # the model failed to type a value-bearing figure -> cannot confirm alignment -> never clean
    out = rv.compute_verdict("annual-premium", [PAPER, UNTYPED])
    assert out["verdict"] == "conflict"

def test_two_agreeing_executed_papers_still_conflict():
    # the clean rule requires EXACTLY ONE governing doc — two, even agreeing, is ambiguous
    out = rv.compute_verdict("annual-premium", [PAPER, PAPER_DUP])
    assert out["verdict"] == "conflict"

def test_mislabeled_nonpaper_source_not_papered():
    # a verbal/trello row claiming tier="paper" must NOT reach a papered verdict
    fake = {"source": "trello", "ref": "C1", "says": "$4,200", "value": 4200.0,
            "qty": "annual-premium", "tier": "paper", "executed": True}
    out = rv.compute_verdict("annual-premium", [fake])
    assert out["verdict"] != "papered"

def test_governing_paper_without_value_is_conflict():
    novalue = {"source": "drive", "ref": "F1", "says": "$?", "value": None,
               "qty": "annual-premium", "tier": "paper", "executed": True}
    out = rv.compute_verdict("annual-premium", [novalue])
    assert out["verdict"] == "conflict"

def test_none_claim_qty_is_silent():
    out = rv.compute_verdict(None, [PAPER])
    assert out["verdict"] == "silent"


def test_a40_explicit_empty_paper_sources_trusts_no_paper_tier():
    # A40: `paper_sources: []` means "trust nothing as paper" — a drive doc must NOT reach papered.
    # The CLI is the only place the default is applied, so drive the CLI to exercise the None-vs-[] fix.
    import json, os, subprocess, sys, tempfile
    tool = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resolve_verdict.py")
    def _run(payload):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "payload.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        r = subprocess.run([sys.executable, tool, p], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)
    base = {"claim_qty": "annual-premium", "evidence": [PAPER]}
    # explicit empty -> no paper tier -> verbal-only
    empty = _run(dict(base, paper_sources=[]))
    assert empty["verdict"] == "verbal-only", empty
    # absent -> default {drive} -> the executed drive paper governs -> papered
    default = _run(base)
    assert default["verdict"] == "papered", default
    # explicit non-empty custom set works too (drive not in it -> no paper tier)
    custom = _run(dict(base, paper_sources=["registry"]))
    assert custom["verdict"] == "verbal-only", custom


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
