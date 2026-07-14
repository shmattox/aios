import os, sys, json, tempfile, shutil
_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _TOOLS)
import resolve_brief as rb

def _sweep(d, flagged, content_hash="h1"):
    p = os.path.join(d, "sweep.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"flagged": flagged, "content_hash": content_hash}, f)
    return p

def test_worklist_enumerates_flagged_tasks_and_candidates():
    d = tempfile.mkdtemp()
    try:
        p = _sweep(d, [{"id": "t1", "title": "Pay insurance $4,200",
                        "candidates": [{"source": "drive", "ref": "file-123"}]},
                       {"id": "t2", "title": "Wire escrow", "candidates": []}])
        wl = rb.worklist(p)
        assert [w["task_id"] for w in wl] == ["t1", "t2"]
        assert wl[0]["title"] == "Pay insurance $4,200"
        assert wl[0]["candidates"][0]["ref"] == "file-123"
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_worklist_missing_or_empty_sweep_is_empty_list():
    assert rb.worklist("/no/such/sweep.json") == []
    d = tempfile.mkdtemp()
    try:
        p = _sweep(d, [])
        assert rb.worklist(p) == []
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_check_complete_when_all_flagged_have_dossiers():
    d = tempfile.mkdtemp()
    try:
        p = _sweep(d, [{"id": "t1", "title": "A"}, {"id": "t2", "title": "B"}], content_hash="h1")
        cache = os.path.join(d, "cache"); os.makedirs(cache)
        for tid in ("t1", "t2"):
            with open(os.path.join(cache, tid + ".json"), "w", encoding="utf-8") as f:
                json.dump({"task_id": tid, "verdict": "verbal-only", "sweep_hash": "h1"}, f)
        r = rb.check(p, cache)
        assert r["complete"] is True and r["missing"] == [] and r["line"] == ""
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_check_reports_missing_dossiers_with_loud_line():
    d = tempfile.mkdtemp()
    try:
        p = _sweep(d, [{"id": "t1", "title": "A"}, {"id": "t2", "title": "B"}], content_hash="h1")
        cache = os.path.join(d, "cache"); os.makedirs(cache)
        with open(os.path.join(cache, "t1.json"), "w", encoding="utf-8") as f:
            json.dump({"task_id": "t1", "sweep_hash": "h1"}, f)
        r = rb.check(p, cache)
        assert r["complete"] is False and r["missing"] == ["t2"]
        assert r["line"].startswith("⚠ resolve INCOMPLETE — 1 of 2")
        assert "t2" in r["line"]
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_check_empty_sweep_is_complete_noop():
    d = tempfile.mkdtemp()
    try:
        p = _sweep(d, [])
        r = rb.check(p, os.path.join(d, "cache"))
        assert r["complete"] is True and r["line"] == ""
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_check_missing_sweep_file_fails_loud_not_complete():
    d = tempfile.mkdtemp()
    try:
        r = rb.check(os.path.join(d, "no-such-sweep.json"), os.path.join(d, "cache"))
        assert r["complete"] is False
        assert "resolve cache MISSING" in r["line"]
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_check_unreadable_corrupt_sweep_fails_loud_not_complete():
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, "sweep.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{ not valid json")
        r = rb.check(p, os.path.join(d, "cache"))
        assert r["complete"] is False
        assert "resolve cache MISSING" in r["line"]
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_check_stale_dossier_with_mismatched_sweep_hash_is_missing():
    d = tempfile.mkdtemp()
    try:
        p = _sweep(d, [{"id": "t1", "title": "A"}], content_hash="h2")
        cache = os.path.join(d, "cache"); os.makedirs(cache)
        # dossier was written for a PRIOR sweep (h1) -> stale, must be re-resolved
        with open(os.path.join(cache, "t1.json"), "w", encoding="utf-8") as f:
            json.dump({"task_id": "t1", "sweep_hash": "h1"}, f)
        r = rb.check(p, cache)
        assert r["complete"] is False and r["missing"] == ["t1"]
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_check_matching_sweep_hash_is_resolved():
    d = tempfile.mkdtemp()
    try:
        p = _sweep(d, [{"id": "t1", "title": "A"}], content_hash="h2")
        cache = os.path.join(d, "cache"); os.makedirs(cache)
        with open(os.path.join(cache, "t1.json"), "w", encoding="utf-8") as f:
            json.dump({"task_id": "t1", "sweep_hash": "h2"}, f)
        r = rb.check(p, cache)
        assert r["complete"] is True and r["missing"] == [] and r["line"] == ""
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_check_null_id_flagged_task_is_reported_missing():
    d = tempfile.mkdtemp()
    try:
        p = _sweep(d, [{"id": None, "title": "A"}], content_hash="h1")
        cache = os.path.join(d, "cache"); os.makedirs(cache)
        r = rb.check(p, cache)
        assert r["complete"] is False
        assert "None" in str(r["missing"])
        assert "resolve INCOMPLETE" in r["line"]
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_check_sanitization_collision_does_not_mark_a_distinct_task_resolved():
    # two raw ids that sanitize to the SAME stem ("a/b" and "a_b" -> "a_b"); a dossier stamped for
    # "a_b" must NOT satisfy the flagged "a/b" (identity guard on the stored task_id).
    d = tempfile.mkdtemp()
    try:
        assert rb._safe_id("a/b") == rb._safe_id("a_b")   # precondition: they collide
        p = _sweep(d, [{"id": "a/b", "title": "A"}, {"id": "a_b", "title": "B"}], content_hash="h1")
        cache = os.path.join(d, "cache"); os.makedirs(cache)
        rb.write_dossier(cache, "a_b", {"title": "B", "verdict": "verbal-only"}, "h1")
        r = rb.check(p, cache)
        assert r["complete"] is False and r["missing"] == ["a/b"], r
        assert "resolve INCOMPLETE" in r["line"] and "a/b" in r["line"]
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_safe_id_basic():
    assert rb._safe_id("t1") == "t1"

def test_safe_id_blocks_traversal():
    sid = rb._safe_id("../../etc")
    assert "/" not in sid and ".." not in sid

def test_safe_id_none_and_blank():
    assert rb._safe_id(None) is None
    assert rb._safe_id("  ") is None

def test_write_dossier_round_trip():
    d = tempfile.mkdtemp()
    try:
        cache = os.path.join(d, "cache")
        path = rb.write_dossier(cache, "t1", {"title": "A", "verdict": "papered"}, "h1")
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        assert rec["sweep_hash"] == "h1" and rec["task_id"] == "t1" and rec["title"] == "A"
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_write_dossier_sanitizes_traversal_id_stays_inside_cache_dir():
    d = tempfile.mkdtemp()
    try:
        cache = os.path.join(d, "cache")
        path = rb.write_dossier(cache, "../../evil", {"title": "A"}, "h1")
        assert os.path.dirname(os.path.abspath(path)) == os.path.abspath(cache)
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_write_dossier_blank_id_raises():
    d = tempfile.mkdtemp()
    try:
        cache = os.path.join(d, "cache")
        try:
            rb.write_dossier(cache, None, {"title": "A"}, "h1")
            assert False, "expected ValueError"
        except ValueError:
            pass
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --- A60: steady-state demotion (a known-stable unresolved backlog stops crying wolf) ---------

def _status(cache_dir, **fields):
    os.makedirs(cache_dir, exist_ok=True)
    with open(os.path.join(cache_dir, "sweep-status.json"), "w", encoding="utf-8") as f:
        json.dump(fields, f)


def test_check_demotes_to_steady_state_after_threshold_days():
    d = tempfile.mkdtemp()
    try:
        p = _sweep(d, [{"id": "t1", "title": "A", "candidates": []}], content_hash="h1")
        cache = os.path.join(d, "cache")
        _status(cache, candidates_unchanged_days=4, consecutive_degraded=0)   # >= threshold, healthy
        r = rb.check(p, cache)
        assert r["complete"] is False                       # still unresolved (no dossier)
        assert "steady-state" in r["line"] and "4d" in r["line"]
        assert "⚠ resolve INCOMPLETE" not in r["line"]      # the alarm is demoted, not raised
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_check_stays_loud_below_threshold():
    d = tempfile.mkdtemp()
    try:
        p = _sweep(d, [{"id": "t1", "title": "A", "candidates": []}], content_hash="h1")
        cache = os.path.join(d, "cache")
        _status(cache, candidates_unchanged_days=2, consecutive_degraded=0)   # below threshold
        r = rb.check(p, cache)
        assert r["line"].startswith("⚠ resolve INCOMPLETE")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_check_stays_loud_when_no_status_sidecar():
    # back-compat: a pre-A60 cache with no sweep-status.json behaves exactly as before (loud).
    d = tempfile.mkdtemp()
    try:
        p = _sweep(d, [{"id": "t1", "title": "A"}], content_hash="h1")
        cache = os.path.join(d, "cache"); os.makedirs(cache)
        r = rb.check(p, cache)
        assert r["line"].startswith("⚠ resolve INCOMPLETE")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_check_stays_loud_when_degraded_even_if_days_high():
    # a degraded sweep can't reach the source, so "steady-state" would be a lie — stay loud + degraded.
    d = tempfile.mkdtemp()
    try:
        p = _sweep(d, [{"id": "t1", "title": "A", "candidates": []}], content_hash="h1")
        cache = os.path.join(d, "cache")
        _status(cache, candidates_unchanged_days=9, consecutive_degraded=2, last_good_utc="2026-01-01T00:00:00Z")
        r = rb.check(p, cache)
        assert "DEGRADED" in r["line"]
        assert "⚠ resolve INCOMPLETE" in r["line"] and "steady-state" not in r["line"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
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
