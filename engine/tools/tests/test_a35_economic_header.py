"""A35 — all-domain sweep + brief header "⚠ N economic figures with no paper".

Two guarantees under test:
  1. The sweep is DOMAIN-ATTRIBUTED across every domain group it gathers (not just FO): each flagged
     figure carries the domain-group key of the source db it came from, and build_cache reports a
     multi-domain no-paper count.
  2. The brief header is a PURE FUNCTION of the sweep's own flagged list — it renders "N economic
     figures with no paper" where N is recomputed from sweep['flagged'] and provably equals the
     sweep's own no_paper_count. The header cannot render a number the sweep did not produce.
"""
import os, sys, json, tempfile, shutil

_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _TOOLS)
import resolve_sweep_task as rst
import resolve_brief as rb


KW = ["insurance", "premium", "policy", "loan", "tax", "wire", "renew"]


# ── domain attribution (unit 1) ───────────────────────────────────────────────────────────────

def test_flag_task_carries_domain():
    import resolve_sweep as rs
    f = rs.flag_task({"id": "t1", "title": "Wire $9,000", "domain": "family_office"}, KW, set())
    assert f["reason"] == "figure" and f["domain"] == "family_office"


def test_normalize_tasks_tags_source_domain_and_prefers_task_own():
    # a source-level domain tags every item; an item's OWN domain wins if present
    out = rst._normalize_tasks([{"id": "a", "title": "x"}, {"id": "b", "title": "y", "domain": "lifeos"}],
                               domain="gm")
    by = {t["id"]: t for t in out}
    assert by["a"]["domain"] == "gm"
    assert by["b"]["domain"] == "lifeos", "an item's own domain overrides the source default"


def test_tasks_from_gather_doc_tags_domain_from_source_db():
    doc = {"live": True, "sources": [
        {"db": "collection://fo", "ok": True, "error": None, "items": [{"id": "a", "title": "insurance $1"}]},
        {"db": "collection://gm", "ok": True, "error": None, "items": [{"id": "b", "title": "wire $2"}]}]}
    db_domain = {"collection://fo": "family_office", "collection://gm": "gm"}
    tasks, source = rst._tasks_from_gather_doc(doc, db_domain)
    assert source == "notion"
    by = {t["id"]: t for t in tasks}
    assert by["a"]["domain"] == "family_office" and by["b"]["domain"] == "gm"


def test_domain_group_map_reads_tasks_dbs():
    root = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(root, "profile"))
        with open(os.path.join(root, "profile", "domains.yaml"), "w", encoding="utf-8") as f:
            f.write("domain_groups:\n"
                    "  family_office:\n    tasks_db: \"collection://fo\"\n"
                    "  gm:\n    tasks_db: \"collection://gm\"\n")
        m = rst.domain_group_map(root)
        assert m == {"collection://fo": "family_office", "collection://gm": "gm"}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_build_cache_reports_multidomain_no_paper_count():
    # two flagged figures with NO candidate paper, in TWO different domains -> no_paper_count 2 across both
    tasks = [{"id": "t1", "title": "Wire $9,000", "domain": "family_office"},
             {"id": "t2", "title": "Pay tax bill $2,000", "domain": "gm"},
             {"id": "t3", "title": "Call mom", "domain": "lifeos"}]
    cache = rst.build_cache(tasks, KW, [], "tasks-file")   # no entities -> every figure has candidates:[]
    assert cache["flagged_count"] == 2
    assert cache["no_paper_count"] == 2
    assert set(cache["no_paper_domains"]) == {"family_office", "gm"}
    assert {f["domain"] for f in cache["flagged"]} == {"family_office", "gm"}


# ── header renderer (unit 2) ──────────────────────────────────────────────────────────────────

def _sweep(d, flagged):
    p = os.path.join(d, "sweep.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"flagged": flagged, "content_hash": "h"}, f)
    return p


def test_economic_header_counts_only_no_paper_figures():
    d = tempfile.mkdtemp()
    try:
        p = _sweep(d, [
            {"id": "t1", "title": "A", "domain": "family_office", "candidates": [{"ref": "drive-1"}]},  # HAS paper
            {"id": "t2", "title": "B", "domain": "gm", "candidates": []},                                 # no paper
            {"id": "t3", "title": "C", "domain": "lifeos", "candidates": []},                             # no paper
        ])
        r = rb.economic_header(p)
        assert r["count"] == 2, "only the two candidate-less figures count as 'no paper'"
        assert set(r["domains"]) == {"gm", "lifeos"}
        assert r["line"].startswith("⚠") and "2 economic figures with no paper" in r["line"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_economic_header_clean_when_all_have_paper():
    d = tempfile.mkdtemp()
    try:
        p = _sweep(d, [{"id": "t1", "title": "A", "domain": "gm", "candidates": [{"ref": "x"}]}])
        r = rb.economic_header(p)
        assert r["count"] == 0 and "⚠" not in r["line"] and "✓" in r["line"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_economic_header_missing_sweep_fails_loud():
    r = rb.economic_header("/no/such/sweep.json")
    assert r["count"] is None and "UNAVAILABLE" in r["line"]


# ── the invariant: header count == the sweep's OWN output, multi-domain (goal condition 2) ──────

def test_header_count_equals_the_multidomain_sweeps_own_output():
    """Run the REAL sweep on a fixture carrying economic figures in >=2 domains; the header count
    must equal the sweep's own no_paper_count and span >=2 domains. Proves the number is produced by
    the sweep, then merely rendered — not invented at render time."""
    root = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(root, "profile"))
        dom = os.path.join(root, "profile", "domains.yaml")
        with open(dom, "w", encoding="utf-8") as f:
            f.write("resolve:\n  economic_keywords: [insurance, tax, wire]\n  cache_dir: state/resolve-cache\n")
        cfg = rst.resolve_config(root)
        # figures in FO, GM, LifeOS — none have candidate paper (no entities configured)
        tf = os.path.join(root, "tasks.json")
        with open(tf, "w", encoding="utf-8") as f:
            json.dump({"tasks": [
                {"id": "fo1", "title": "Wire the Metropolis insurance $4,200", "domain": "family_office"},
                {"id": "gm1", "title": "Pay the LLC tax bill $2,000", "domain": "gm"},
                {"id": "life1", "title": "Buy groceries", "domain": "lifeos"}]}, f)
        res = rst.run(root, tasks_file=tf, now="2026-01-01T00:00:00Z")
        assert res["status"] == "written"
        sweep_path = res["cache_path"]
        with open(sweep_path, encoding="utf-8") as f:
            sweep = json.load(f)
        # the sweep's OWN reported count
        assert sweep["no_paper_count"] == 2
        assert len(set(sweep["no_paper_domains"])) >= 2, "figures came from >=2 domains"
        # the header renders exactly that number, recomputed from the sweep's flagged list
        hdr = rb.economic_header(sweep_path)
        assert hdr["count"] == sweep["no_paper_count"], "header must render the sweep's own count"
        assert str(sweep["no_paper_count"]) in hdr["line"]
        assert set(hdr["domains"]) == set(sweep["no_paper_domains"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_header_number_tracks_the_sweep_cannot_be_invented():
    """The header is a pure function of sweep['flagged']: two different sweeps yield two different
    numbers, and there is no code path that renders a count absent from the flagged list."""
    d = tempfile.mkdtemp()
    try:
        p1 = os.path.join(d, "s1.json")
        with open(p1, "w", encoding="utf-8") as f:
            json.dump({"flagged": [{"id": "a", "domain": "gm", "candidates": []}]}, f)
        p2 = os.path.join(d, "s2.json")
        with open(p2, "w", encoding="utf-8") as f:
            json.dump({"flagged": [{"id": "a", "domain": "gm", "candidates": []},
                                   {"id": "b", "domain": "family_office", "candidates": []},
                                   {"id": "c", "domain": "lifeos", "candidates": [{"ref": "x"}]}]}, f)
        assert rb.economic_header(p1)["count"] == 1
        assert rb.economic_header(p2)["count"] == 2   # c has paper -> excluded
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_header_cli_prints_line(tmp_path_factory=None):
    import subprocess
    d = tempfile.mkdtemp()
    try:
        p = _sweep(d, [{"id": "t1", "title": "A", "domain": "gm", "candidates": []}])
        tool = os.path.join(_TOOLS, "resolve_brief.py")
        r = subprocess.run([sys.executable, tool, "header", p], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stderr
        assert "economic figure" in r.stdout and "no paper" in r.stdout
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
