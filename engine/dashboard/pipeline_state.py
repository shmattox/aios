"""Pipeline-state aggregator for the dashboard's master Flow view. Classifies every backlog
item into one of 8 fixed factory stages and emits {stages, edges, flows}. Pure classifier +
model (unit-tested); the source gather is best-effort (a down/idle source contributes zero,
never raises)."""

import os
import re
import sys
import glob
import json
import time

_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
try:
    from backlog_parse import parse_backlog
except Exception:                                  # pragma: no cover - defensive
    def parse_backlog(text):
        return []

_ID_RE = re.compile(r"\b([A-Za-z]{1,4}\d+[a-z]?)\b")

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
    if item.get("state") == "done" or iid in ctx["done_ids"]:
        return "complete"
    if iid in ctx["held_ids"] or item.get("gate_human"):
        return "gate"
    if iid in ctx["review_ids"]:
        return "review"
    if iid in ctx["active_ids"]:
        return "subagent"
    if iid in ctx["plan_ids"]:
        return "implement"
    if iid in ctx["spec_ids"]:
        return "spec"
    if item.get("state") == "seed":
        return "brainstorm"
    return "backlog"


def _group_by_stage(items, ctx):
    """Full stage_id -> [item dict] map (uncapped) + the item-id -> stage map.
    Shared by build_model (which caps to a poll-light preview) and stage_detail (full)."""
    stage_items = {sid: [] for sid, _ in STAGES}
    cur = {}
    for it in items:
        sid = classify_stage(it, ctx)
        iid = it.get("id") or ""
        cur[iid] = sid
        stage_items[sid].append({"id": it.get("id"), "title": it.get("headline"), "repo": it.get("repo")})
    return stage_items, cur


def build_model(items, ctx, prev_stage_by_id=None):
    stage_items, cur = _group_by_stage(items, ctx)
    # The 4s poll carries only counts + a small preview; the full per-stage list is fetched
    # on demand via stage_detail() when a node is clicked (keeps the poll light).
    stages = [{"id": sid, "label": lbl, "count": len(stage_items[sid]), "items": stage_items[sid][:12]}
              for sid, lbl in STAGES]
    edges = [{"from": a, "to": b} for a, b in EDGES]
    flows = []
    for iid, sid in cur.items():
        prev = (prev_stage_by_id or {}).get(iid)
        if prev and prev != sid and _ORDER.get(sid, 0) > _ORDER.get(prev, 0):   # forward only
            flows.append({"item_id": iid, "from": prev, "to": sid})
    return {"stages": stages, "edges": edges, "flows": flows, "stage_by_id": cur}


def _repo_roots(env_root):
    """The env root + each Projects/<repo> dir — the only places backlogs and superpowers docs
    live. Backlogs/docs are scanned ONLY here, never via a recursive ** walk of the whole env
    (which would traverse the SecondBrain vault, every node_modules, and .git — seconds per call)."""
    roots = [env_root]
    proj = os.path.join(env_root, "Projects")
    if os.path.isdir(proj):
        try:
            roots += [os.path.join(proj, d) for d in sorted(os.listdir(proj))
                      if os.path.isdir(os.path.join(proj, d))]
        except OSError:
            pass
    return roots


def _backlog_files(env_root):
    out = []
    for root in _repo_roots(env_root):
        bl = os.path.join(root, "BACKLOG.md")
        if os.path.isfile(bl):
            out.append(("env-ops" if root == env_root else os.path.basename(root), bl))
    return out


def _ids_in_docs(env_root, subdir):
    """Item ids that appear in any docs/superpowers/<subdir>/*.md filename or first line.
    Scans only the known repo roots (env + Projects/*), never a recursive ** walk."""
    ids = set()
    for root in _repo_roots(env_root):
        for f in glob.glob(os.path.join(root, "docs", "superpowers", subdir, "*.md")):
            ids.update(_ID_RE.findall(os.path.basename(f)))
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    ids.update(_ID_RE.findall(fh.readline()))
            except OSError:
                pass
    return ids


def _active_and_review_ids(env_root):
    """Live factory drains → subagent/review stage, from state/activity/factory-*.json."""
    active, review = set(), set()
    try:
        import time as _t
        now = _t.time()
        for f in glob.glob(os.path.join(env_root, "state", "activity", "factory-*.json")):
            try:
                with open(f, encoding="utf-8") as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                continue
            if rec.get("status") != "running" or (now - rec.get("heartbeat", 0)) > 90:
                continue
            detail = str(rec.get("detail") or "").lower()
            bucket = review if "review" in detail or "gate" in detail else active
            for i in rec.get("item_ids") or []:
                bucket.add(i)
    except Exception:                                  # pragma: no cover - defensive
        return set(), set()
    return active, review


def _held_ids(env_root):
    try:
        for name in ("brief-cache.json", "brief.json"):
            f = os.path.join(env_root, "state", name)
            if os.path.isfile(f):
                with open(f, encoding="utf-8") as fh:
                    d = json.load(fh)
                return {h.get("id") for h in (d.get("held") or []) if h.get("id")}
        return set()
    except Exception:                                  # pragma: no cover - defensive
        return set()


def gather(env_root):
    items = []
    done_ids = set()
    for repo, bl in _backlog_files(env_root):
        try:
            with open(bl, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for it in parse_backlog(text):
            it["repo"] = repo
            items.append(it)
            if it.get("state") == "done" and it.get("id"):
                done_ids.add(it["id"])
    # id-precision: only genuine backlog ids may stamp a stage, so _ID_RE false positives
    # (stray tokens like "v2"/"s3" picked up from doc filenames/first-lines) can't classify.
    real_ids = {it["id"] for it in items if it.get("id")}
    active, review = _active_and_review_ids(env_root)
    ctx = {
        "spec_ids": _ids_in_docs(env_root, "specs") & real_ids,
        "plan_ids": _ids_in_docs(env_root, "plans") & real_ids,
        "active_ids": active & real_ids, "review_ids": review & real_ids,
        "held_ids": _held_ids(env_root) & real_ids, "done_ids": done_ids,
    }
    # an item can't be both spec- and plan-having in two stages: plan wins (handled by classify order)
    return items, ctx


_GATHER_CACHE = {}      # env_root -> (ts, items, ctx)
_GATHER_TTL = 3.0       # gather() is a full multi-repo FS scan; a poll + drill-in share one recent scan


def reset_cache():
    """Drop the gather TTL cache — for tests, or to force an immediate re-scan."""
    _GATHER_CACHE.clear()


def _cached_gather(env_root):
    """gather() re-scans every repo's backlog/specs/plans/activity — too costly to run on every
    /api/pipeline poll (4s) AND every drill-in fetch. A short TTL lets concurrent requests share
    one recent scan; TTL < poll interval keeps counts live."""
    now = time.time()
    hit = _GATHER_CACHE.get(env_root)
    if hit and now - hit[0] < _GATHER_TTL:
        return hit[1], hit[2]
    items, ctx = gather(env_root)
    _GATHER_CACHE[env_root] = (now, items, ctx)
    return items, ctx


def model(env_root, prev_stage_by_id=None):
    items, ctx = _cached_gather(env_root)
    return build_model(items, ctx, prev_stage_by_id)


def stage_detail(env_root, stage_id):
    """Full (uncapped) item list for ONE stage — the drill-in source, fetched on node click.
    Best-effort like the rest of the module; an unknown stage id returns None so the caller 404s."""
    labels = dict(STAGES)
    if stage_id not in labels:
        return None
    items, ctx = _cached_gather(env_root)
    stage_items, _ = _group_by_stage(items, ctx)
    lst = stage_items.get(stage_id, [])
    return {"id": stage_id, "label": labels[stage_id], "count": len(lst), "items": lst}
