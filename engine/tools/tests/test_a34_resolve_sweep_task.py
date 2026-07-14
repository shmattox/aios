import os, sys, json, tempfile, shutil, subprocess

_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _TOOLS)
import resolve_sweep_task as rst
import seed_resolve_defaults as srd

_TASK_TOOL = os.path.join(_TOOLS, "resolve_sweep_task.py")


def _env(with_entities=False, with_task_dbs=False):
    """A scratch env_root with a seeded resolve block; optional entities dir + a task-view db."""
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "profile"))
    dom = os.path.join(root, "profile", "domains.yaml")
    with open(dom, "w", encoding="utf-8") as f:
        f.write("brief:\n  trigger: go\n")
    srd.seed(dom)                                    # seed the default resolve: block
    if with_entities:
        ent_dir = os.path.join(root, "vault", "entities")
        os.makedirs(ent_dir)
        with open(dom, "a", encoding="utf-8") as f:
            f.write("  entities_dir: vault/entities\n")
        with open(os.path.join(ent_dir, "bayview-flats.md"), "w", encoding="utf-8") as f:
            f.write("---\nlinks:\n  aliases: [Bayview Flats]\n  drive:\n"
                    "    - \"file-123 — Bayview operating agreement\"\n---\nBayview Flats LLC\n")
    if with_task_dbs:
        with open(dom, "a", encoding="utf-8") as f:
            f.write("  task_source_dbs: [\"collection://abc-123\"]\n")   # under the resolve: block
    return root


def _tasks_file(root, tasks):
    p = os.path.join(root, "tasks.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"tasks": tasks}, f)
    return p


# --- config -----------------------------------------------------------------------------------

def test_resolve_config_reads_seeded_keywords_and_cache_dir():
    root = _env()
    try:
        cfg = rst.resolve_config(root)
        assert cfg is not None
        assert "insurance" in cfg["keywords"], "must read the seeded inline keyword list"
        assert os.path.isabs(cfg["cache_dir"]) and cfg["cache_dir"].endswith("resolve-cache")
        assert cfg["task_dbs"] == [], "no connectors -> no task dbs"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_resolve_config_none_when_block_absent():
    root = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(root, "profile"))
        with open(os.path.join(root, "profile", "domains.yaml"), "w", encoding="utf-8") as f:
            f.write("brief:\n  trigger: go\n")           # no resolve: block
        assert rst.resolve_config(root) is None
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_resolve_config_reads_task_source_dbs():
    root = _env(with_task_dbs=True)
    try:
        assert rst.resolve_config(root)["task_dbs"] == ["collection://abc-123"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_read_resolve_list_task_source_dbs_block_form():
    # task_source_dbs must parse in block form too (same reader as economic_keywords)
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, "domains.yaml")
        with open(p, "w", encoding="utf-8") as f:
            f.write("resolve:\n  task_source_dbs:\n    - collection://a\n    - collection://b\n")
        assert rst._read_resolve_list(p, "task_source_dbs") == ["collection://a", "collection://b"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --- gather -----------------------------------------------------------------------------------

def test_gather_from_tasks_file_normalizes():
    root = _env()
    try:
        tf = _tasks_file(root, [{"id": "t1", "title": "Pay insurance $4,200", "props": {"Notes": "urgent"}}])
        tasks, source = rst.gather_tasks(rst.resolve_config(root), tasks_file=tf)
        assert source == "tasks-file"
        assert tasks[0]["id"] == "t1" and "insurance" in tasks[0]["title"].lower()
        assert "urgent" in tasks[0]["body"], "props fold into body so keywords there still flag"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_gather_degrades_cleanly_with_no_source():
    root = _env()  # no connectors, no tasks-file
    try:
        tasks, source = rst.gather_tasks(rst.resolve_config(root))
        assert tasks == [] and source == "none"
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --- build_cache + candidate attach -----------------------------------------------------------

def test_build_cache_flags_figure_and_attaches_entity_candidates():
    root = _env(with_entities=True)
    try:
        cfg = rst.resolve_config(root)
        tasks = [{"id": "t1", "title": "Renew the Bayview Flats insurance $4,200", "body": ""},
                 {"id": "t2", "title": "Call mom", "body": ""}]
        cache = rst.build_cache(tasks, cfg["keywords"], rst._entities(cfg["entities_dir"]), "tasks-file")
        assert cache["flagged_count"] == 1
        f = cache["flagged"][0]
        assert f["id"] == "t1" and f["reason"] == "figure"
        assert any(c["ref"] == "file-123" and c["entity"] == "bayview-flats.md" for c in f["candidates"]), \
            "the matching entity's crosswalk candidate must be pre-scraped into the flag"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_build_cache_attaches_candidates_from_multiple_entity_dirs():
    # A38: entities_dir as a LIST reads entities/ AND companies/ so a task matching an entity in
    # EITHER dir attaches its crosswalk candidates — the single-dir ceiling (the "45/45 unresolved"
    # root cause: companies/ entities like Northwind were unreachable) is removed.
    root = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(root, "profile"))
        dom = os.path.join(root, "profile", "domains.yaml")
        with open(dom, "w", encoding="utf-8") as f:
            f.write("brief:\n  trigger: go\n")
        srd.seed(dom)
        ent = os.path.join(root, "vault", "entities"); os.makedirs(ent)
        com = os.path.join(root, "vault", "companies"); os.makedirs(com)
        with open(os.path.join(ent, "metropolis.md"), "w", encoding="utf-8") as f:
            f.write("---\nlinks:\n  aliases: [Metropolis]\n  drive:\n    - \"d-metropolis — Metropolis OA\"\n---\n")
        with open(os.path.join(com, "northwind.md"), "w", encoding="utf-8") as f:
            f.write("---\nlinks:\n  aliases: [Northwind]\n  drive:\n    - \"d-northwind — Northwind formation\"\n---\n")
        with open(dom, "a", encoding="utf-8") as f:
            f.write("  entities_dir: [vault/entities, vault/companies]\n")
        cfg = rst.resolve_config(root)
        assert len(cfg["entities_dirs"]) == 2, "list form resolves BOTH dirs"
        assert cfg["entities_dir"].replace(os.sep, "/").endswith("vault/entities"), "scalar back-compat = first dir"
        ents = rst._entities(cfg["entities_dirs"])
        cache = rst.build_cache(
            [{"id": "t1", "title": "Pay Metropolis insurance $1", "body": ""},
             {"id": "t2", "title": "Resolve Northwind tax $2", "body": ""}],
            cfg["keywords"], ents, "tasks-file")
        flagged = {f["id"]: f for f in cache["flagged"]}
        assert any(c["ref"] == "d-metropolis" for c in flagged["t1"]["candidates"]), "entities/ dir attaches"
        assert any(c["ref"] == "d-northwind" for c in flagged["t2"]["candidates"]), "companies/ dir attaches"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_build_cache_no_candidates_when_no_entities_configured():
    root = _env()
    try:
        cfg = rst.resolve_config(root)
        cache = rst.build_cache([{"id": "t1", "title": "Wire $9,000", "body": ""}],
                                cfg["keywords"], rst._entities(cfg["entities_dir"]), "tasks-file")
        assert cache["flagged"][0]["candidates"] == []
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --- delta / warm -----------------------------------------------------------------------------

def test_unchanged_true_on_same_content_false_on_change():
    root = _env()
    try:
        cfg = rst.resolve_config(root)
        c1 = rst.build_cache([{"id": "t1", "title": "insurance $1", "body": ""}], cfg["keywords"], [], "x")
        p = os.path.join(root, "sweep.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(c1, f)
        assert rst.unchanged(c1, p) is True
        c2 = rst.build_cache([{"id": "t1", "title": "insurance $1", "body": ""},
                              {"id": "t2", "title": "wire $2", "body": ""}], cfg["keywords"], [], "x")
        assert rst.unchanged(c2, p) is False
        assert rst.unchanged(c1, os.path.join(root, "nope.json")) is False
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --- run() end-to-end --------------------------------------------------------------------------

def test_run_writes_cache_then_stays_warm():
    root = _env()
    try:
        tf = _tasks_file(root, [{"id": "t1", "title": "Pay property insurance $4,200"}])
        r1 = rst.run(root, tasks_file=tf, now="2026-01-01T00:00:00Z")
        assert r1["status"] == "written" and r1["flagged_count"] == 1
        assert os.path.exists(r1["cache_path"])
        with open(r1["cache_path"], encoding="utf-8") as f:
            assert json.load(f)["flagged"][0]["id"] == "t1"
        r2 = rst.run(root, tasks_file=tf, now="2026-01-02T00:00:00Z")
        assert r2["status"] == "warm", "unchanged task set must not rewrite the cache"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_run_degraded_preserves_prior_cache_on_gather_error():
    # the HIGH fix: a transient gather failure must NOT clobber a good warm cache with an empty one.
    root = _env()
    try:
        cfg = rst.resolve_config(root)
        os.makedirs(cfg["cache_dir"])
        good = {"content_hash": "H1", "flagged_count": 5, "flagged": [{"id": "t1"}]}
        cache_path = os.path.join(cfg["cache_dir"], "sweep.json")
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(good, f)
        orig = rst.gather_tasks
        rst.gather_tasks = lambda cfg, tasks_file=None: ([], "error")   # simulate an overnight blip
        try:
            r = rst.run(root, now="2026-01-01T00:00:00Z")
        finally:
            rst.gather_tasks = orig
        assert r["status"] == "degraded"
        with open(cache_path, encoding="utf-8") as f:
            assert json.load(f)["flagged_count"] == 5, "prior warm cache must survive a gather error"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_entity_match_requires_word_boundary():
    # the MEDIUM fix: a short slug/alias must not substring-match inside an unrelated word.
    entities = [("bay.md", "---\nlinks:\n  aliases: [bay]\n  drive:\n    - \"f-9 — doc\"\n---\n")]
    no = rst._candidates_for_task({"id": "t1", "title": "Renew bayview insurance $1", "body": ""}, entities)
    assert no == [], "'bay' inside 'bayview' is not a word match"
    yes = rst._candidates_for_task({"id": "t2", "title": "Wire the bay deposit $1", "body": ""}, entities)
    assert any(c["ref"] == "f-9" for c in yes), "'bay' as a whole word must attach"


def test_read_economic_keywords_inline_and_block_forms():
    # the LOW fix: reformatting keywords as a YAML block list must NOT silently self-disable the sweep.
    d = tempfile.mkdtemp()
    try:
        p1 = os.path.join(d, "inline.yaml")
        with open(p1, "w", encoding="utf-8") as f:
            f.write("resolve:\n  economic_keywords: [insurance, tax]\n  cache_dir: x\n")
        assert rst._read_economic_keywords(p1) == ["insurance", "tax"]
        p2 = os.path.join(d, "block.yaml")
        with open(p2, "w", encoding="utf-8") as f:
            f.write("resolve:\n  economic_keywords:\n    - insurance\n    - mortgage\n  cache_dir: x\n")
        assert rst._read_economic_keywords(p2) == ["insurance", "mortgage"]
        # SAME-INDENT block sequence (yaml.dump's default output) must also parse — not self-disable
        p3 = os.path.join(d, "same_indent.yaml")
        with open(p3, "w", encoding="utf-8") as f:
            f.write("resolve:\n  economic_keywords:\n  - insurance  # primary carrier\n  - tax\n  cache_dir: x\n")
        assert rst._read_economic_keywords(p3) == ["insurance", "tax"], "same-indent + inline comment"
        # a stray economic_keywords OUTSIDE resolve: must be ignored (anchored read)
        p4 = os.path.join(d, "anchored.yaml")
        with open(p4, "w", encoding="utf-8") as f:
            f.write("other:\n  economic_keywords: [wrong]\nresolve:\n  economic_keywords: [right]\n")
        assert rst._read_economic_keywords(p4) == ["right"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_partial_gather_outage_is_degraded_not_a_subset():
    # the re-review MEDIUM: one source down (ok:false) must yield 'error' (preserve prior), never a
    # subset cache that silently drops the down source's economic tasks.
    all_ok = {"live": True, "sources": [
        {"ok": True, "error": None, "items": [{"id": "a", "title": "insurance $1"}]},
        {"ok": True, "error": None, "items": [{"id": "b", "title": "wire $2"}]}]}
    tasks, source = rst._tasks_from_gather_doc(all_ok)
    assert source == "notion" and {t["id"] for t in tasks} == {"a", "b"}
    partial = {"live": True, "sources": [
        {"ok": True, "error": None, "items": [{"id": "a", "title": "insurance $1"}]},
        {"ok": False, "error": "HTTP 429", "items": []}]}
    assert rst._tasks_from_gather_doc(partial) == ([], "error"), "partial outage must degrade"
    assert rst._tasks_from_gather_doc({"live": False, "sources": [{"ok": True}]})[1] == "error"


def test_run_skipped_when_resolve_not_configured():
    root = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(root, "profile"))
        with open(os.path.join(root, "profile", "domains.yaml"), "w", encoding="utf-8") as f:
            f.write("brief:\n  trigger: go\n")
        assert rst.run(root)["status"] == "skipped"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_run_clean_noop_when_no_task_source():
    root = _env()  # resolve configured, but no tasks-file and no connectors
    try:
        r = rst.run(root, now="2026-01-01T00:00:00Z")
        assert r["status"] == "written" and r["source"] == "none" and r["flagged_count"] == 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_cli_end_to_end_writes_cache_and_exits_zero():
    root = _env()
    try:
        tf = _tasks_file(root, [{"id": "t1", "title": "Renew mortgage escrow $12,000"}])
        r = subprocess.run([sys.executable, _TASK_TOOL, "--env-root", root, "--tasks-file", tf,
                            "--no-context-log"], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)
        assert out["status"] == "written" and out["flagged_count"] == 1
        assert os.path.exists(os.path.join(root, "state", "resolve-cache", "sweep.json"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --- A60: candidate-set stability signal (quiet a known-stable resolve backlog) --------------

def test_candidates_fingerprint_stable_across_title_churn_changes_on_real_change():
    # same id + same candidate refs, DIFFERENT title -> same fingerprint (content_hash would differ).
    a = [{"id": "t1", "title": "Pay Metropolis insurance $1", "reason": "figure",
          "candidates": [{"source": "drive", "ref": "d-1"}]}]
    b = [{"id": "t1", "title": "PAY Metropolis INSURANCE NOW $1", "reason": "economic-keyword",
          "candidates": [{"source": "drive", "ref": "d-1"}]}]
    assert rst._candidates_fingerprint(a) == rst._candidates_fingerprint(b)
    # a changed candidate ref -> different
    c = [{"id": "t1", "title": "Pay Metropolis insurance $1", "candidates": [{"source": "drive", "ref": "d-2"}]}]
    assert rst._candidates_fingerprint(a) != rst._candidates_fingerprint(c)
    # a NEW flagged task (even with no candidates) -> different (a new no-paper figure must re-alarm)
    d = a + [{"id": "t2", "title": "Wire escrow $9,000", "candidates": []}]
    assert rst._candidates_fingerprint(a) != rst._candidates_fingerprint(d)
    # candidate REORDERING is not a real change -> same fingerprint (set semantics, must not re-alarm)
    e = [{"id": "t1", "title": "x", "candidates": [{"ref": "d-2"}, {"ref": "d-1"}]}]
    g = [{"id": "t1", "title": "x", "candidates": [{"ref": "d-1"}, {"ref": "d-2"}]}]
    assert rst._candidates_fingerprint(e) == rst._candidates_fingerprint(g)


def test_run_increments_candidates_unchanged_days_on_identical_worklist():
    root = _env()
    try:
        cfg = rst.resolve_config(root)
        tf = _tasks_file(root, [{"id": "t1", "title": "Pay property insurance $4,200"}])
        rst.run(root, tasks_file=tf, now="2026-01-01T00:00:00Z")
        s1 = rst._read_status(cfg["cache_dir"])
        assert s1["candidates_unchanged_days"] == 1 and s1.get("candidates_fingerprint")
        rst.run(root, tasks_file=tf, now="2026-01-02T00:00:00Z")
        s2 = rst._read_status(cfg["cache_dir"])
        assert s2["candidates_unchanged_days"] == 2
        assert s2["candidates_fingerprint"] == s1["candidates_fingerprint"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_run_resets_candidates_unchanged_days_when_worklist_changes():
    root = _env()
    try:
        cfg = rst.resolve_config(root)
        tf1 = _tasks_file(root, [{"id": "t1", "title": "Pay property insurance $4,200"}])
        rst.run(root, tasks_file=tf1, now="2026-01-01T00:00:00Z")
        rst.run(root, tasks_file=tf1, now="2026-01-02T00:00:00Z")
        assert rst._read_status(cfg["cache_dir"])["candidates_unchanged_days"] == 2
        # a NEW flagged task changes the worklist shape -> counter resets to 1
        tf2 = _tasks_file(root, [{"id": "t1", "title": "Pay property insurance $4,200"},
                                 {"id": "t2", "title": "Wire escrow $9,000"}])
        rst.run(root, tasks_file=tf2, now="2026-01-03T00:00:00Z")
        assert rst._read_status(cfg["cache_dir"])["candidates_unchanged_days"] == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_run_degraded_preserves_candidates_unchanged_days():
    # a degraded sweep (source unreachable) recomputes nothing -> it must PRESERVE the stability
    # counter, not reset it (else a transient blip re-loudens a known-stable backlog for N more days).
    root = _env()
    try:
        cfg = rst.resolve_config(root)
        tf = _tasks_file(root, [{"id": "t1", "title": "Pay property insurance $4,200"}])
        rst.run(root, tasks_file=tf, now="2026-01-01T00:00:00Z")
        rst.run(root, tasks_file=tf, now="2026-01-02T00:00:00Z")
        before = rst._read_status(cfg["cache_dir"])
        orig = rst.gather_tasks
        rst.gather_tasks = lambda cfg, tasks_file=None: ([], "error")
        try:
            r = rst.run(root, now="2026-01-03T00:00:00Z")
        finally:
            rst.gather_tasks = orig
        assert r["status"] == "degraded"
        after = rst._read_status(cfg["cache_dir"])
        assert after["candidates_unchanged_days"] == before["candidates_unchanged_days"]
        assert after["candidates_fingerprint"] == before["candidates_fingerprint"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


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
