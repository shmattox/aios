import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import resolve_sweep as rs

KW = ["insurance", "premium", "policy", "loan", "tax", "renew", "wire", "payoff"]

def test_flags_task_with_money_figure():
    t = {"id": "t1", "title": "Pay property insurance $4,200"}
    f = rs.flag_task(t, KW, set())
    assert f["reason"] == "figure"

def test_flags_bare_currency_figure_without_dollar_sign():
    # a currency-worded figure with NO '$' and NO economic keyword must still flag (high-recall)
    t = {"id": "t4", "title": "Send the 4,200 USD deposit"}
    f = rs.flag_task(t, KW, set())
    assert f is not None
    assert f["reason"] == "figure"

def test_flags_economic_keyword_without_figure():
    # "Renew the policy" has no dollar amount but still needs paper -> must be flagged
    t = {"id": "t2", "title": "Renew the policy before it lapses"}
    f = rs.flag_task(t, KW, set())
    assert f["reason"] == "economic-keyword"

def test_does_not_flag_non_economic_task():
    t = {"id": "t3", "title": "Call mom about the weekend"}
    assert rs.flag_task(t, KW, set()) is None

def test_skips_already_resolved_task():
    t = {"id": "t1", "title": "Pay property insurance $4,200"}
    assert rs.flag_task(t, KW, {"t1"}) is None

def test_sweep_returns_only_flagged():
    tasks = [
        {"id": "t1", "title": "Pay property insurance $4,200"},
        {"id": "t3", "title": "Call mom about the weekend"},
        {"id": "t2", "title": "Renew the policy"},
    ]
    out = rs.sweep(tasks, KW, set())
    assert {f["id"] for f in out} == {"t1", "t2"}


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
