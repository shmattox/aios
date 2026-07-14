#!/usr/bin/env python3
"""Logic-mirror tests for garden_distill.py — the deterministic distill-and-retire envelope.
Run: python engine/tools/tests/test_garden_distill.py   (or via the suite: python -m pytest engine/tools/tests/ -q)
"""
import os, sys, shutil, tempfile, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # .../aios/engine/tools
import garden_distill as gd
import queue_tx


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _stub(title, raw_path="raw/inbox/x.md", tier="secondary", typ="source"):
    return (f"---\ntitle: {title}\ntype: {typ}\nexplored: false\n"
            f"source_tier: {tier}\nraw_path: {raw_path}\nlinks: []\n---\n\n"
            f"## Point A\n- durable a\n\n## Point B\n- durable b\n")


def _vault(tmp):
    """Build a fixture vault at tmp with a dev KB holding two source stubs + one non-source page."""
    root = os.path.join(tmp, "vault")
    _write(os.path.join(root, "dev", "wiki", "sources", "alpha.md"), _stub("Alpha"))
    _write(os.path.join(root, "dev", "wiki", "sources", "beta.md"), _stub("Beta"))
    _write(os.path.join(root, "dev", "wiki", "sources", "note.md"),
           _stub("A concept", typ="knowledge"))   # not type:source -> not a candidate
    _write(os.path.join(root, "dev", "raw", "inbox", "x.md"), "raw origin")
    return root


def test_enumerate_returns_only_source_stubs():
    tmp = tempfile.mkdtemp()
    try:
        vault = _vault(tmp)
        got = gd.enumerate_stubs(vault, "dev")
        slugs = sorted(s["slug"] for s in got)
        assert slugs == ["alpha", "beta"]              # note.md excluded (type: knowledge)
        assert got[0]["fm"]["type"] == "source"
    finally:
        shutil.rmtree(tmp)


def test_provenance_raw_resolves_when_raw_path_present():
    tmp = tempfile.mkdtemp()
    try:
        vault = _vault(tmp)                       # alpha.md has raw_path: raw/inbox/x.md, which exists
        stub = next(s for s in gd.enumerate_stubs(vault, "dev") if s["slug"] == "alpha")
        assert gd.provenance_check(vault, "dev", stub) == "raw_resolves"
    finally:
        shutil.rmtree(tmp)


def test_provenance_archive_as_new_raw_when_no_raw_anywhere():
    tmp = tempfile.mkdtemp()
    try:
        vault = _vault(tmp)
        # gamma: raw_path points nowhere AND no content match exists -> archive-as-new-raw
        _write(os.path.join(vault, "dev", "wiki", "sources", "gamma.md"),
               _stub("Gamma", raw_path="raw/inbox/missing.md"))
        stub = next(s for s in gd.enumerate_stubs(vault, "dev") if s["slug"] == "gamma")
        assert gd.provenance_check(vault, "dev", stub) == "archive_as_new_raw"
    finally:
        shutil.rmtree(tmp)


def test_provenance_raw_resolves_via_filename_token_match():
    tmp = tempfile.mkdtemp()
    try:
        vault = _vault(tmp)
        # epsilon: NO raw_path, but a raw file's stem contains the slug as a whole token -> resolves
        _write(os.path.join(vault, "dev", "wiki", "sources", "epsilon.md"),
               "---\ntitle: Epsilon\ntype: source\nsource_tier: secondary\nlinks: []\n---\n\nbody\n")
        _write(os.path.join(vault, "dev", "raw", "inbox", "epsilon-origin.md"), "origin content")
        stub = next(s for s in gd.enumerate_stubs(vault, "dev") if s["slug"] == "epsilon")
        assert gd.provenance_check(vault, "dev", stub) == "raw_resolves"
    finally:
        shutil.rmtree(tmp)


def test_provenance_coincidental_substring_does_not_falsely_resolve():
    tmp = tempfile.mkdtemp()
    try:
        vault = _vault(tmp)
        # slug 'note' must NOT match raw 'notebook.md' (substring but not a whole token) -> safe archive
        _write(os.path.join(vault, "dev", "wiki", "sources", "note.md"),
               "---\ntitle: Note\ntype: source\nsource_tier: secondary\nlinks: []\n---\n\nbody\n")
        _write(os.path.join(vault, "dev", "raw", "inbox", "notebook.md"),
               "notebook prose that contains the bare substring note somewhere")
        stub = next(s for s in gd.enumerate_stubs(vault, "dev") if s["slug"] == "note")
        assert gd.provenance_check(vault, "dev", stub) == "archive_as_new_raw"
    finally:
        shutil.rmtree(tmp)


def test_extract_checklist_pulls_h2_and_bullets():
    text = _stub("Alpha")     # body: "## Point A / - durable a / ## Point B / - durable b"
    got = gd.extract_checklist(text)
    assert "Point A" in got and "Point B" in got
    assert "durable a" in got and "durable b" in got
    assert len(got) == 4


def test_extract_checklist_excludes_frontmatter_list_items():
    # a YAML list item in frontmatter (indented `- `) must NOT leak into the checklist
    text = ("---\ntitle: X\ntype: source\ntags:\n  - fmtag\nlinks: []\n---\n\n"
            "## Real Heading\n- real bullet\n")
    got = gd.extract_checklist(text)
    assert "Real Heading" in got and "real bullet" in got
    assert "fmtag" not in got          # frontmatter list item excluded (proves _body strips frontmatter)
    assert got == ["Real Heading", "real bullet"]


def test_build_proposal_is_queue_valid_and_gated():
    stub = {"slug": "alpha", "path": "dev/wiki/sources/alpha.md", "fm": {"type": "source"}}
    item = gd.build_proposal("dev", stub, "alpha-topic", "raw_resolves",
                             "dev/wiki/staging/alpha-topic.md", "2026-07-01T04:00:00Z")
    assert item["id"] == "distill-dev-alpha"
    assert item["stage"] == "awaiting"
    assert item["lane"] == "review"                      # propose-only in MVP
    assert item["source"] == "wiki"
    assert item["conflict_key"] == "dev/wiki/knowledge/alpha-topic.md"
    assert item["retire_stub"] == "dev/wiki/sources/alpha.md"
    assert item["provenance"] == "raw_resolves"
    # must satisfy the real queue validator as a one-item queue
    assert queue_tx.validate({"queue": [item]}) is None


def test_build_proposal_id_is_stable_for_idempotency():
    stub = {"slug": "alpha", "path": "p", "fm": {}}
    a = gd.build_proposal("dev", stub, "t", "raw_resolves", "d", "2026-07-01T04:00:00Z")
    b = gd.build_proposal("dev", stub, "t", "raw_resolves", "d", "2026-07-02T04:00:00Z")
    assert a["id"] == b["id"]        # id keyed on kb+slug, NOT time -> queue_tx add dedupe-fences a re-run


def test_retire_relinks_archives_and_verifies_clean():
    tmp = tempfile.mkdtemp()
    try:
        vault = _vault(tmp)
        # a knowledge target + an inbound page linking the stub via [[sources/alpha]]
        _write(os.path.join(vault, "dev", "wiki", "knowledge", "alpha-topic.md"),
               "---\ntitle: Alpha Topic\ntype: knowledge\n---\n\ndistilled body\n")
        _write(os.path.join(vault, "dev", "wiki", "knowledge", "ref.md"),
               "see [[sources/alpha]] for detail\n")
        res = gd.retire(vault, "dev", "alpha", "alpha-topic", "2026-07-01")
        # husk moved out of sources/, into raw/archive/
        assert not gd._present(os.path.join(vault, "dev", "wiki", "sources", "alpha.md"))
        assert gd._present(res["archived"])
        # inbound link repointed to the knowledge target; zero dangling
        ref = gd._read(os.path.join(vault, "dev", "wiki", "knowledge", "ref.md"))
        assert "[[sources/alpha]]" not in ref
        assert "[[knowledge/alpha-topic]]" in ref
        assert res["dangling"] == []
    finally:
        shutil.rmtree(tmp)


def test_retire_raises_if_a_dangling_link_would_survive():
    tmp = tempfile.mkdtemp()
    try:
        vault = _vault(tmp)
        _write(os.path.join(vault, "dev", "wiki", "knowledge", "alpha-topic.md"), "x\n")
        # a link written in a form the relinker won't catch (piped alias) -> verify must catch the residue
        _write(os.path.join(vault, "dev", "wiki", "knowledge", "ref.md"),
               "see [[sources/alpha|Alpha]] still\n")
        try:
            gd.retire(vault, "dev", "alpha", "alpha-topic", "2026-07-01")
            assert False, "expected RuntimeError on surviving dangling link"
        except RuntimeError as e:
            assert "dangling" in str(e).lower()
            # ATOMIC: husk was NOT moved (verify runs before the move) -> the piped link still resolves
            assert gd._present(os.path.join(vault, "dev", "wiki", "sources", "alpha.md"))
    finally:
        shutil.rmtree(tmp)


def test_retire_ignores_unrelated_prefix_collision_stub():
    tmp = tempfile.mkdtemp()
    try:
        vault = _vault(tmp)
        _write(os.path.join(vault, "dev", "wiki", "knowledge", "alpha-topic.md"), "x\n")
        # an unrelated page links a DIFFERENT stub whose slug merely starts with 'alpha' -> must NOT block
        _write(os.path.join(vault, "dev", "wiki", "knowledge", "ref.md"),
               "see [[sources/alphabet]] which is a different stub\n")
        res = gd.retire(vault, "dev", "alpha", "alpha-topic", "2026-07-01")
        assert not gd._present(os.path.join(vault, "dev", "wiki", "sources", "alpha.md"))  # alpha retired
        assert gd._present(res["archived"])
        assert "[[sources/alphabet]]" in gd._read(
            os.path.join(vault, "dev", "wiki", "knowledge", "ref.md"))  # unrelated link untouched
        assert res["dangling"] == []
    finally:
        shutil.rmtree(tmp)


def test_end_to_end_enqueue_through_queue_tx():
    tmp = tempfile.mkdtemp()
    try:
        vault = _vault(tmp)
        state = os.path.join(tmp, "state"); os.makedirs(state)
        queue_path = os.path.join(state, "queue.json")
        stub = next(s for s in gd.enumerate_stubs(vault, "dev") if s["slug"] == "alpha")
        prov = gd.provenance_check(vault, "dev", stub)
        item = gd.build_proposal("dev", stub, "alpha-topic", prov,
                                 "dev/wiki/staging/alpha-topic.md", "2026-07-01T04:00:00Z")
        items_path = os.path.join(tmp, "new.json")
        with open(items_path, "w", encoding="utf-8") as f:
            json.dump([item], f)
        # enqueue via the real queue_tx add primitive (dedupe-fenced, atomic)
        queue_tx._apply_items(queue_path, queue_tx._read_items(items_path), "add")
        loaded = queue_tx.load(queue_path)["queue"]
        assert any(i["id"] == "distill-dev-alpha" and i["lane"] == "review" for i in loaded)
    finally:
        shutil.rmtree(tmp)


def test_retire_blocks_anchored_link_the_relinker_wont_rewrite():
    tmp = tempfile.mkdtemp()
    try:
        vault = _vault(tmp)
        _write(os.path.join(vault, "dev", "wiki", "knowledge", "alpha-topic.md"), "x\n")
        # a heading-anchored link the plain relinker doesn't rewrite -> must be caught as dangling (atomic)
        _write(os.path.join(vault, "dev", "wiki", "knowledge", "ref.md"),
               "see [[sources/alpha#Section]] for detail\n")
        try:
            gd.retire(vault, "dev", "alpha", "alpha-topic", "2026-07-01")
            assert False, "expected RuntimeError on surviving anchored dangling link"
        except RuntimeError as e:
            assert "dangling" in str(e).lower()
            assert gd._present(os.path.join(vault, "dev", "wiki", "sources", "alpha.md"))  # atomic: husk stays
    finally:
        shutil.rmtree(tmp)


def test_retire_refuses_if_knowledge_target_absent():
    tmp = tempfile.mkdtemp()
    try:
        vault = _vault(tmp)
        # NO knowledge/alpha-topic.md -> ship didn't happen -> retire must refuse and mutate NOTHING
        _write(os.path.join(vault, "dev", "wiki", "knowledge", "ref.md"), "see [[sources/alpha]] here\n")
        try:
            gd.retire(vault, "dev", "alpha", "alpha-topic", "2026-07-01")
            assert False, "expected RuntimeError when knowledge target absent"
        except RuntimeError as e:
            assert "ship" in str(e).lower() or "does not exist" in str(e).lower()
        assert gd._present(os.path.join(vault, "dev", "wiki", "sources", "alpha.md"))          # husk untouched
        assert "[[sources/alpha]]" in gd._read(os.path.join(vault, "dev", "wiki", "knowledge", "ref.md"))  # link untouched
    finally:
        shutil.rmtree(tmp)


def test_retire_blocks_noncanonical_terminator_links():
    tmp = tempfile.mkdtemp()
    try:
        vault = _vault(tmp)
        _write(os.path.join(vault, "dev", "wiki", "knowledge", "alpha-topic.md"), "x\n")
        # trailing-space and .md terminator forms the plain relinker won't rewrite -> must be caught
        _write(os.path.join(vault, "dev", "wiki", "knowledge", "ref.md"),
               "space form [[sources/alpha ]] and ext form [[sources/alpha.md]] both dangle\n")
        try:
            gd.retire(vault, "dev", "alpha", "alpha-topic", "2026-07-01")
            assert False, "expected RuntimeError on non-canonical terminator dangling links"
        except RuntimeError as e:
            assert "dangling" in str(e).lower()
            assert gd._present(os.path.join(vault, "dev", "wiki", "sources", "alpha.md"))  # atomic: husk stays
    finally:
        shutil.rmtree(tmp)


def test_stub_class_reads_concept_and_defaults_reference():
    assert gd.stub_class({"distill_class": "concept"}) == "concept"
    assert gd.stub_class({"distill_class": "Concept"}) == "concept"   # case-insensitive
    assert gd.stub_class({"distill_class": "reference"}) == "reference"
    assert gd.stub_class({}) == "reference"                          # absent -> reference (legacy-safe)
    assert gd.stub_class({"distill_class": "garbage"}) == "reference"  # unknown -> reference
    assert gd.stub_class({"distill_class": None}) == "reference"      # None guard


def _cstub(slug, cls, when="2026-07-01"):
    return {"slug": slug, "path": f"p/{slug}.md",
            "fm": {"distill_class": cls, "last_reconciled": when}}

def test_select_distill_batch_caps_concepts_and_separates_references():
    stubs = [_cstub("c1", "concept", "2026-07-01"),
             _cstub("c2", "concept", "2026-07-03"),
             _cstub("c3", "concept", "2026-07-02"),
             _cstub("r1", "reference"), _cstub("r2", "reference")]
    batch, overflow, refs = gd.select_distill_batch(stubs, cap_k=2)
    assert [s["slug"] for s in batch] == ["c1", "c3"]      # oldest-first by last_reconciled
    assert [s["slug"] for s in overflow] == ["c2"]         # remaining concepts carry forward
    assert sorted(s["slug"] for s in refs) == ["r1", "r2"] # references never compete for the budget

def test_select_distill_batch_cap_zero_defers_all_concepts():
    stubs = [_cstub("c1", "concept"), _cstub("r1", "reference")]
    batch, overflow, refs = gd.select_distill_batch(stubs, cap_k=0)
    assert batch == []
    assert [s["slug"] for s in overflow] == ["c1"]
    assert [s["slug"] for s in refs] == ["r1"]

def test_select_distill_batch_missing_date_sorts_oldest():
    stubs = [_cstub("has_date", "concept", "2026-07-05"),
             {"slug": "no_date", "path": "p", "fm": {"distill_class": "concept"}}]
    batch, overflow, refs = gd.select_distill_batch(stubs, cap_k=1)
    assert [s["slug"] for s in batch] == ["no_date"]   # empty last_reconciled sorts first (most overdue)


def test_noise_retire_fence_blocks_undistilled_concept():
    try:
        gd.assert_noise_retire_allowed({"distill_class": "concept"})
        assert False, "expected RuntimeError — a concept stub cannot noise-retire un-attempted"
    except RuntimeError as e:
        assert "concept" in str(e).lower()

def test_noise_retire_fence_allows_attempted_concept():
    gd.assert_noise_retire_allowed(
        {"distill_class": "concept", "distill_attempted": "no-durable-concept"})  # no raise

def test_noise_retire_fence_allows_reference_and_legacy():
    gd.assert_noise_retire_allowed({"distill_class": "reference"})  # reference passes
    gd.assert_noise_retire_allowed({})                              # legacy (no class) passes


def test_distill_run_metrics_mean_and_line():
    m = gd.distill_run_metrics(concept_in=3, knowledge_pages_touched=7,
                               reference_retired=40, fanout_counts=[1, 3, 5])
    assert m["mean_fanout"] == 3.0
    assert "concept_in=3" in m["line"]
    assert "knowledge_touched=7" in m["line"]
    assert "ref_retired=40" in m["line"]
    assert "mean_fanout=3.0" in m["line"]


def test_distill_run_metrics_empty_fanout_is_zero():
    m = gd.distill_run_metrics(0, 0, 0, [])
    assert m["mean_fanout"] == 0.0
    assert "mean_fanout=0.0" in m["line"]


def test_enumerate_archive_lists_source_stubs_with_class():
    tmp = tempfile.mkdtemp()
    try:
        adir = os.path.join(tmp, "wiki-sources-retired-2026-07-10")
        _write(os.path.join(adir, "idea.md"),
               "---\ntitle: Idea\ntype: source\ndistill_class: concept\nlinks: []\n---\n\n## Core idea\n- x\n")
        _write(os.path.join(adir, "link.md"),
               "---\ntitle: Link\ntype: source\nlinks: []\n---\n\nbody\n")   # no class -> reference
        _write(os.path.join(adir, "page.md"),
               "---\ntitle: Page\ntype: knowledge\n---\n\nnot a source\n")   # excluded
        got = gd.enumerate_archive(adir)
        slugs = sorted(i["slug"] for i in got)
        assert slugs == ["idea", "link"]                      # knowledge page excluded
        by = {i["slug"]: i["class"] for i in got}
        assert by["idea"] == "concept" and by["link"] == "reference"
    finally:
        shutil.rmtree(tmp)

def test_tally_classes_counts_and_lists_concepts():
    items = [{"slug": "a", "class": "concept"}, {"slug": "b", "class": "reference"},
             {"slug": "c", "class": "concept"}]
    t = gd.tally_classes(items)
    assert t["total"] == 3
    assert t["concept_count"] == 2
    assert t["reference_count"] == 1
    assert t["concept_slugs"] == ["a", "c"]                    # sorted


if __name__ == "__main__":
    # also runnable without pytest, matching the repo's other test files
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print("  ok  " + fn.__name__)
        except Exception:
            failed += 1; print(" FAIL " + fn.__name__); traceback.print_exc()
    print(f"\n{len(fns)-failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
