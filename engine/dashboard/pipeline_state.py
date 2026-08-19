"""Pipeline-state aggregator for the dashboard's master Flow view. Classifies every backlog
item into one of 8 fixed factory stages and emits {stages, edges, flows}. Pure classifier +
model (unit-tested); the source gather is best-effort (a down/idle source contributes zero,
never raises)."""

STAGES = [
    ("backlog", "Backlog"), ("brainstorm", "Brainstorm"), ("spec", "Spec"),
    ("implement", "Implement"), ("subagent", "Subagent builds"),
    ("review", "Review"), ("gate", "Gate"), ("complete", "Complete"),
]
EDGES = [(STAGES[i][0], STAGES[i + 1][0]) for i in range(len(STAGES) - 1)]
_ORDER = {sid: i for i, (sid, _) in enumerate(STAGES)}


def classify_stage(item, ctx):
    """Most-advanced-stage-wins. `ctx` sets are id-membership per advanced stage."""
    iid = item.get("id") or ""
    if item.get("status") == "done" or iid in ctx["done_ids"]:
        return "complete"
    if iid in ctx["held_ids"]:
        return "gate"
    if iid in ctx["review_ids"]:
        return "review"
    if iid in ctx["active_ids"]:
        return "subagent"
    if iid in ctx["plan_ids"] or item.get("factory_ready"):
        return "implement"
    if iid in ctx["spec_ids"]:
        return "spec"
    if item.get("seed"):
        return "brainstorm"
    return "backlog"


def build_model(items, ctx, prev_stage_by_id=None):
    stage_items = {sid: [] for sid, _ in STAGES}
    cur = {}
    for it in items:
        sid = classify_stage(it, ctx)
        iid = it.get("id") or ""
        cur[iid] = sid
        stage_items[sid].append({"id": it.get("id"), "title": it.get("title"), "repo": it.get("repo")})
    stages = [{"id": sid, "label": lbl, "count": len(stage_items[sid]), "items": stage_items[sid][:12]}
              for sid, lbl in STAGES]
    edges = [{"from": a, "to": b} for a, b in EDGES]
    flows = []
    for iid, sid in cur.items():
        prev = (prev_stage_by_id or {}).get(iid)
        if prev and prev != sid and _ORDER.get(sid, 0) > _ORDER.get(prev, 0):   # forward only
            flows.append({"item_id": iid, "from": prev, "to": sid})
    return {"stages": stages, "edges": edges, "flows": flows, "stage_by_id": cur}
