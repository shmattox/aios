import os, sys, json, threading
import urllib.request, urllib.error
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "dashboard"))
import pipeline_state as p
from dashboard_server import make_server

def _ctx(**kw):
    base = {k: set() for k in ("spec_ids", "plan_ids", "active_ids", "review_ids", "held_ids", "done_ids")}
    base.update(kw); return base

def test_stages_and_edges_shape():
    assert [s[0] for s in p.STAGES] == ["backlog","brainstorm","spec","implement","subagent","review","gate","complete"]
    assert p.EDGES == [("backlog","brainstorm"),("brainstorm","spec"),("spec","implement"),
                       ("implement","subagent"),("subagent","review"),("review","gate"),("gate","complete")]

def test_classify_precedence_most_advanced_wins():
    # real backlog_parse.parse_backlog schema: id, state ("open"|"done"|"seed"), headline,
    # gate_human (bool), markers, closed_date — NOT status/seed/factory_ready.
    it = {"id": "A1", "state": "open"}
    assert p.classify_stage(it, _ctx()) == "backlog"
    assert p.classify_stage({**it, "state": "seed"}, _ctx()) == "brainstorm"
    assert p.classify_stage(it, _ctx(spec_ids={"A1"})) == "spec"
    assert p.classify_stage(it, _ctx(plan_ids={"A1"})) == "implement"
    assert p.classify_stage(it, _ctx(active_ids={"A1"})) == "subagent"
    assert p.classify_stage(it, _ctx(review_ids={"A1"})) == "review"
    assert p.classify_stage(it, _ctx(held_ids={"A1"})) == "gate"
    assert p.classify_stage({**it, "gate_human": True}, _ctx()) == "gate"
    assert p.classify_stage(it, _ctx(done_ids={"A1"})) == "complete"
    assert p.classify_stage({**it, "state": "done"}, _ctx()) == "complete"
    # a spec-having item that is ALSO in review classifies as review (more advanced)
    assert p.classify_stage(it, _ctx(spec_ids={"A1"}, review_ids={"A1"})) == "review"

def test_build_model_counts_edges_and_forward_flows():
    items = [
        {"id": "A1", "state": "seed", "repo": "aios", "headline": "seed one"},
        {"id": "A2", "state": "open", "repo": "aios", "headline": "backlog two"},
        {"id": "A3", "state": "done", "repo": "aios", "headline": "done three"},
    ]
    m = p.build_model(items, _ctx(), prev_stage_by_id={"A1": "backlog"})
    by = {s["id"]: s for s in m["stages"]}
    assert by["brainstorm"]["count"] == 1 and by["brainstorm"]["items"][0]["id"] == "A1"
    assert by["brainstorm"]["items"][0]["title"] == "seed one"   # title sourced from headline
    assert by["backlog"]["count"] == 1 and by["complete"]["count"] == 1
    assert {"from": "backlog", "to": "brainstorm"} in m["edges"]
    # A1 moved backlog -> brainstorm (forward) => a flow; A2/A3 first-seen => no flow
    assert m["flows"] == [{"item_id": "A1", "from": "backlog", "to": "brainstorm"}]
    assert m["stage_by_id"]["A1"] == "brainstorm"

def test_build_model_backward_and_unchanged_are_not_flows():
    items = [{"id": "B1", "state": "open", "headline": "x"}]
    # prev said B1 was in review; now it's backlog (backward) -> NOT a flow
    m = p.build_model(items, _ctx(), prev_stage_by_id={"B1": "review"})
    assert m["flows"] == []
    # unchanged -> not a flow
    m2 = p.build_model(items, _ctx(), prev_stage_by_id={"B1": "backlog"})
    assert m2["flows"] == []

def test_gather_reads_backlogs_and_specs_best_effort(tmp_path):
    (tmp_path / "state").mkdir(); (tmp_path / "profile").mkdir()  # so it reads as an env root
    (tmp_path / "BACKLOG.md").write_text(
        "## Open\n- [ ] **Z1** — needs a spec.\n  - acceptance: x\n"
        "- [ ] **Z2** — has a spec.\n  - acceptance: x\n", encoding="utf-8")
    specs = tmp_path / "docs" / "superpowers" / "specs"; specs.mkdir(parents=True)
    (specs / "2026-08-19-z2-thing-design.md").write_text("# Z2 thing\nbody", encoding="utf-8")
    items, ctx = p.gather(str(tmp_path))
    ids = {it["id"] for it in items}
    assert "Z1" in ids and "Z2" in ids
    assert "Z2" in ctx["spec_ids"] and "Z1" not in ctx["spec_ids"]
    m = p.model(str(tmp_path))
    by = {s["id"]: s for s in m["stages"]}
    assert by["backlog"]["count"] == 1 and by["spec"]["count"] == 1   # Z1 backlog, Z2 spec

def test_gather_matches_backlog_parse_prefix_rule_for_5plus_letter_ids(tmp_path):
    # _ID_RE must accept any prefix length backlog_parse accepts ([A-Za-z]+\d+), not cap at 4
    # letters -- else a real 5+-letter-prefix id parses from BACKLOG.md but can never be
    # extracted from a spec/plan filename/body, so it can never reach the spec/implement stages.
    (tmp_path / "state").mkdir(); (tmp_path / "profile").mkdir()
    (tmp_path / "BACKLOG.md").write_text(
        "## Open\n- [ ] **ZORRO1** — has a spec with a long prefix.\n  - acceptance: x\n",
        encoding="utf-8")
    specs = tmp_path / "docs" / "superpowers" / "specs"; specs.mkdir(parents=True)
    (specs / "2026-08-19-zorro1-thing-design.md").write_text("# ZORRO1 thing\nbody", encoding="utf-8")
    items, ctx = p.gather(str(tmp_path))
    assert "ZORRO1" in ctx["spec_ids"]

def test_gather_missing_sources_never_raises(tmp_path):
    (tmp_path / "state").mkdir(); (tmp_path / "profile").mkdir()
    items, ctx = p.gather(str(tmp_path))          # no backlogs, no docs, no activity
    assert items == [] and all(v == set() for v in ctx.values())
    assert p.model(str(tmp_path))["stages"][0]["count"] == 0

def test_gather_classifies_real_done_and_seed_items(tmp_path):
    # exercises the REAL backlog_parse glyphs: "[x]" -> state=done, "◷" -> state=seed.
    (tmp_path / "state").mkdir(); (tmp_path / "profile").mkdir()
    (tmp_path / "BACKLOG.md").write_text(
        "## Open\n"
        "- [x] **D1** — done thing. ✅ 2026-08-18\n"
        "- ◷ **S1** — seed thing.\n",
        encoding="utf-8")
    items, ctx = p.gather(str(tmp_path))
    m = p.model(str(tmp_path))
    by = {s["id"]: s for s in m["stages"]}
    assert m["stage_by_id"]["D1"] == "complete"
    assert m["stage_by_id"]["S1"] == "brainstorm"
    assert by["complete"]["count"] == 1 and by["brainstorm"]["count"] == 1

def test_gather_intersects_ctx_ids_with_real_backlog_ids(tmp_path):
    # id-precision: a doc filename/first-line token that LOOKS like an id ("v2") but
    # doesn't belong to any real backlog item must not stamp any ctx set.
    (tmp_path / "state").mkdir(); (tmp_path / "profile").mkdir()
    (tmp_path / "BACKLOG.md").write_text(
        "## Open\n- [ ] **Z1** — real item.\n  - acceptance: x\n", encoding="utf-8")
    specs = tmp_path / "docs" / "superpowers" / "specs"; specs.mkdir(parents=True)
    (specs / "2026-08-19-v2-migration-design.md").write_text("# v2 migration\nbody", encoding="utf-8")
    items, ctx = p.gather(str(tmp_path))
    assert ctx["spec_ids"] == set()   # "v2" is a false-positive token, not a real id

def test_active_review_and_held_never_raise_on_malformed_json(tmp_path):
    # a malformed-but-parseable JSON file (held as list of strings; non-numeric heartbeat)
    # must not raise AttributeError/TypeError out of gather.
    (tmp_path / "profile").mkdir()
    (tmp_path / "state").mkdir()
    activity = tmp_path / "state" / "activity"; activity.mkdir()
    (activity / "factory-bad.json").write_text(
        json.dumps({"status": "running", "heartbeat": "not-a-number", "item_ids": ["X1"]}),
        encoding="utf-8")
    (tmp_path / "state" / "brief-cache.json").write_text(
        json.dumps({"held": ["X1", "X2"]}), encoding="utf-8")  # held as list of strings, not dicts
    items, ctx = p.gather(str(tmp_path))          # must not raise
    assert ctx["active_ids"] == set() and ctx["held_ids"] == set()


# --- Task 4: dashboard_server wiring — /api/pipeline + /pipeline/* static -----

def _mk_env(tmp_path):
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "profile").mkdir(exist_ok=True)
    (tmp_path / "BACKLOG.md").write_text(
        "## Open\n- [ ] **Q1** — x.\n  - acceptance: y\n", encoding="utf-8")
    return tmp_path


def _get_json(srv, path):
    port = srv.server_address[1]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return json.loads(r.read())


def test_api_pipeline_route_returns_model_and_tracks_flows(tmp_path):
    # A real end-to-end HTTP hit through the running server — the brief's own draft test only
    # called pipeline_state.model() directly, which would pass even with zero server wiring.
    env = _mk_env(tmp_path)
    srv = make_server(str(env), port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        assert srv._pipeline_prev == {}   # make_server initializes the per-process flow-diff map
        m = _get_json(srv, "/api/pipeline")
        assert any(s["id"] == "backlog" and s["count"] == 1 for s in m["stages"])
        assert m["flows"] == []                       # first poll: no prior state, no flows yet
        assert srv._pipeline_prev == m["stage_by_id"]  # server updates its prev map after the call

        # advance Q1 to the spec stage between polls (a spec doc naming it) and re-poll
        specs = env / "docs" / "superpowers" / "specs"
        specs.mkdir(parents=True)
        (specs / "2026-08-19-q1-thing-design.md").write_text("# Q1 thing\nbody", encoding="utf-8")
        p.reset_cache()   # in prod the two polls are >TTL apart; force the re-scan here
        m2 = _get_json(srv, "/api/pipeline")
        assert {"item_id": "Q1", "from": "backlog", "to": "spec"} in m2["flows"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_stage_detail_returns_full_uncapped_items(tmp_path):
    # build a backlog stage with MORE than the poll preview cap (12) and assert stage_detail
    # returns them all — the drill-in must not silently truncate.
    (tmp_path / "state").mkdir(); (tmp_path / "profile").mkdir()
    lines = "".join(f"- [ ] **B{i}** — item {i}.\n  - acceptance: x\n" for i in range(20))
    (tmp_path / "BACKLOG.md").write_text("## Open\n" + lines, encoding="utf-8")
    detail = p.stage_detail(str(tmp_path), "backlog")
    assert detail["count"] == 20 and len(detail["items"]) == 20   # uncapped
    assert detail["label"] == "Backlog"
    assert {it["id"] for it in detail["items"]} == {f"B{i}" for i in range(20)}
    # poll model still caps its preview to 12 for the same stage
    assert len(p.model(str(tmp_path))["stages"][0]["items"]) == 12


def test_stage_detail_unknown_stage_is_none(tmp_path):
    (tmp_path / "state").mkdir(); (tmp_path / "profile").mkdir()
    assert p.stage_detail(str(tmp_path), "bogus") is None
    assert p.stage_detail(str(tmp_path), "../secret") is None   # not a real stage id -> None


def test_api_pipeline_stage_route(tmp_path):
    env = _mk_env(tmp_path)   # one open item Q1 -> backlog
    srv = make_server(str(env), port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        d = _get_json(srv, "/api/pipeline/stage/backlog")
        assert d["id"] == "backlog" and d["count"] == 1
        assert d["items"][0]["id"] == "Q1"
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(
                f"http://127.0.0.1:{srv.server_address[1]}/api/pipeline/stage/bogus", timeout=5)
        assert e.value.code == 404
    finally:
        srv.shutdown()
        srv.server_close()


# (the /pipeline/* static route + its two tests were removed when the React Flow iframe app was
# replaced by the native Preact cockpit view — /api/pipeline[/stage] remain the live data sources.)


def test_item_detail_extracts_block_and_links_plans(tmp_path):
    (tmp_path / "BACKLOG.md").write_text(
        "## Open\n\n"
        "- [ ] **H10** — first item headline\n"
        "  - a sub-bullet mentioning docs/superpowers/plans/2026-08-20-h10-plan.md\n"
        "  acceptance: it works\n"
        "- [ ] **H11** — second item\n",
        encoding="utf-8")
    plans = tmp_path / "docs" / "superpowers" / "plans"
    plans.mkdir(parents=True)
    (plans / "2026-08-20-h10-plan.md").write_text("# H10 plan\n", encoding="utf-8")
    d = p.item_detail(str(tmp_path), "env-ops", "H10")
    assert d["backlog_path"] == "BACKLOG.md"
    assert "first item headline" in d["body"] and "acceptance: it works" in d["body"]
    assert "**H11**" not in d["body"]                       # block stops at the next item
    assert d["docs"] == [{"kind": "plan", "path": "docs/superpowers/plans/2026-08-20-h10-plan.md"}]
    assert p.item_detail(str(tmp_path), "env-ops", "H99")["body"] == ""   # missing id → empty
