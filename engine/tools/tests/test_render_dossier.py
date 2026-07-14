import os, sys
_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _TOOLS)
import brief_render as br

def test_render_papered_shows_green_and_canonical():
    card = br.render_dossier({"title": "Pay insurance $4,200", "verdict": "papered",
                              "canonical": "$4,200 — cited to drive:file-123", "conflict": None})
    assert "Pay insurance $4,200" in card
    assert "🟢" in card and "Papered" in card and "drive:file-123" in card

def test_render_conflict_shows_red_held_and_reason():
    card = br.render_dossier({"title": "Metropolis tax", "verdict": "conflict", "canonical": None,
                              "conflict": "trello says $9,000; paper says $8,200"})
    assert "🔴" in card and "Conflict" in card and "held" in card.lower()
    assert "trello says $9,000; paper says $8,200" in card

def test_render_verbal_only_shows_orange_no_paper():
    card = br.render_dossier({"title": "Vendor invoice", "verdict": "verbal-only",
                              "canonical": None, "conflict": None, "provenance": ["notion"]})
    assert "🟠" in card and "no executed paper" in card.lower()

def test_render_silent_is_dim_line():
    card = br.render_dossier({"title": "X", "verdict": "silent", "canonical": None, "conflict": None})
    assert "silent" in card.lower()

def test_render_unknown_verdict_does_not_crash():
    card = br.render_dossier({"title": "X", "verdict": "weird"})
    assert "X" in card

def test_render_verbal_only_with_null_provenance_entry_does_not_crash():
    # resolve_verdict.compute_verdict can produce provenance=[None] (a candidate with no
    # 'source' field); the join must coerce/filter, not raise, and fall back to "verbal".
    card = br.render_dossier({"title": "Vendor invoice", "verdict": "verbal-only",
                              "canonical": None, "conflict": None, "provenance": [None]})
    assert "🟠" in card and "no executed paper" in card.lower()
    assert "(verbal)" in card

def test_render_verbal_only_with_mixed_none_provenance_does_not_crash():
    card = br.render_dossier({"title": "Vendor invoice", "verdict": "verbal-only",
                              "canonical": None, "conflict": None, "provenance": ["notion", None]})
    assert "🟠" in card and "no executed paper" in card.lower()
    assert "notion" in card


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
