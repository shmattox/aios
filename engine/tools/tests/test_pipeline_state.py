import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "dashboard"))
import pipeline_state as p

def _ctx(**kw):
    base = {k: set() for k in ("spec_ids", "plan_ids", "active_ids", "review_ids", "held_ids", "done_ids")}
    base.update(kw); return base

def test_stages_and_edges_shape():
    assert [s[0] for s in p.STAGES] == ["backlog","brainstorm","spec","implement","subagent","review","gate","complete"]
    assert p.EDGES == [("backlog","brainstorm"),("brainstorm","spec"),("spec","implement"),
                       ("implement","subagent"),("subagent","review"),("review","gate"),("gate","complete")]

def test_classify_precedence_most_advanced_wins():
    it = {"id": "A1", "status": "open", "seed": False, "factory_ready": False}
    assert p.classify_stage(it, _ctx()) == "backlog"
    assert p.classify_stage({**it, "seed": True}, _ctx()) == "brainstorm"
    assert p.classify_stage(it, _ctx(spec_ids={"A1"})) == "spec"
    assert p.classify_stage(it, _ctx(plan_ids={"A1"})) == "implement"
    assert p.classify_stage({**it, "factory_ready": True}, _ctx()) == "implement"
    assert p.classify_stage(it, _ctx(active_ids={"A1"})) == "subagent"
    assert p.classify_stage(it, _ctx(review_ids={"A1"})) == "review"
    assert p.classify_stage(it, _ctx(held_ids={"A1"})) == "gate"
    assert p.classify_stage(it, _ctx(done_ids={"A1"})) == "complete"
    assert p.classify_stage({**it, "status": "done"}, _ctx()) == "complete"
    # a spec-having item that is ALSO in review classifies as review (more advanced)
    assert p.classify_stage(it, _ctx(spec_ids={"A1"}, review_ids={"A1"})) == "review"

def test_build_model_counts_edges_and_forward_flows():
    items = [
        {"id": "A1", "status": "open", "seed": True, "repo": "aios", "title": "seed one"},
        {"id": "A2", "status": "open", "seed": False, "repo": "aios", "title": "backlog two"},
        {"id": "A3", "status": "done", "seed": False, "repo": "aios", "title": "done three"},
    ]
    m = p.build_model(items, _ctx(), prev_stage_by_id={"A1": "backlog"})
    by = {s["id"]: s for s in m["stages"]}
    assert by["brainstorm"]["count"] == 1 and by["brainstorm"]["items"][0]["id"] == "A1"
    assert by["backlog"]["count"] == 1 and by["complete"]["count"] == 1
    assert {"from": "backlog", "to": "brainstorm"} in m["edges"]
    # A1 moved backlog -> brainstorm (forward) => a flow; A2/A3 first-seen => no flow
    assert m["flows"] == [{"item_id": "A1", "from": "backlog", "to": "brainstorm"}]
    assert m["stage_by_id"]["A1"] == "brainstorm"

def test_build_model_backward_and_unchanged_are_not_flows():
    items = [{"id": "B1", "status": "open", "seed": False, "title": "x"}]
    # prev said B1 was in review; now it's backlog (backward) -> NOT a flow
    m = p.build_model(items, _ctx(), prev_stage_by_id={"B1": "review"})
    assert m["flows"] == []
    # unchanged -> not a flow
    m2 = p.build_model(items, _ctx(), prev_stage_by_id={"B1": "backlog"})
    assert m2["flows"] == []
