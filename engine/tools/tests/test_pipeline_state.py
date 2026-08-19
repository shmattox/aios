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
