"""Content-pipeline (capture → sort → ingest → gate → garden) summary for the dashboard, from
the queue. Best-effort — a missing/corrupt queue contributes zero, never raises."""

import io
import os
import json
from collections import Counter

# The 5 pipeline stages, and which queue `stage` values map to each current node.
STAGES = ["capture", "sort", "ingest", "gate", "garden"]
_NODE_FOR = {"captured": "capture", "sort": "sort", "sorted": "sort", "drafting": "ingest",
             "awaiting": "gate"}   # shipped/rejected are terminal (lifetime totals, not a node)


def _read_queue(env_root):
    try:
        with io.open(os.path.join(env_root, "state", "queue.json"), encoding="utf-8") as f:
            return (json.load(f) or {}).get("queue", []) or []
    except (OSError, ValueError):
        return []


def summary(env_root):
    items = [it for it in _read_queue(env_root) if isinstance(it, dict) and not it.get("retired")]
    by_stage = Counter(it.get("stage") for it in items)
    nodes = {s: 0 for s in STAGES}
    for qs, cnt in by_stage.items():
        node = _NODE_FOR.get(qs)
        if node:
            nodes[node] += cnt
    # items awaiting the human gate, split by knowledge base (the FO/personal/gm mix)
    by_kb = Counter(it.get("kb") for it in items if it.get("stage") == "awaiting")
    return {
        "nodes": [{"id": s, "label": s.capitalize(), "count": nodes[s]} for s in STAGES],
        "shipped": by_stage.get("shipped", 0),
        "rejected": by_stage.get("rejected", 0),
        "sorted": by_stage.get("sorted", 0),
        "gate": by_stage.get("awaiting", 0),
        "by_kb": dict(by_kb),
    }
