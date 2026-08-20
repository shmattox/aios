"""Content-pipeline (capture → sort → ingest → gate → garden) summary for the dashboard, from
the queue. Best-effort — a missing/corrupt queue contributes zero, never raises.

The queue has three live resting states — captured, sorted, awaiting (canonical vocab in
engine/tools/queue_tx.py) — plus terminals (shipped/rejected/reverted/reference). The five
pipeline *phases* don't each own a resting queue: `sort` and `gate` are decision phases, `ingest`
(Phase A drafting) flips sorted→awaiting, and `garden` weaves shipped items into the KB post-ship.
So we render each node from a real signal:
  - capture / sort / gate → the live BACKLOG waiting at that phase (captured / sorted / awaiting)
  - ingest / garden       → 24h THROUGHPUT (items that entered awaiting / shipped in the last day),
                            since those phases hold no resting queue of their own.
`kind` on each node marks which lens it is, so the UI can label the throughput nodes honestly."""

import io
import os
import re
import json
import time
import datetime
from collections import Counter

STAGES = ["capture", "sort", "ingest", "gate", "garden"]
_LABELS = {s: s.capitalize() for s in STAGES}

# backlog nodes: live count of the resting queue waiting at that phase
_BACKLOG_STATE = {"capture": "captured", "sort": "sorted", "gate": "awaiting"}
# throughput nodes: items that ENTERED this stage (per history) within the window
_THROUGHPUT_STAGE = {"ingest": "awaiting", "garden": "shipped"}
_WINDOW_S = 86400   # 24h

_ID_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}-")
_ID_TAIL = re.compile(r"-[a-z0-9]{5,8}(?:-\d{3,6})?$")   # random slug + optional shard suffix


def _title_from_id(iid):
    """Best-effort human title from a queue id like 2026-07-30-supabase-invoice-ljdtac-00015."""
    s = str(iid or "")
    s = _ID_DATE.sub("", s)
    s = _ID_TAIL.sub("", s)
    return s.replace("-", " ").strip() or str(iid or "")


def _read_queue(env_root):
    try:
        with io.open(os.path.join(env_root, "state", "queue.json"), encoding="utf-8") as f:
            return (json.load(f) or {}).get("queue", []) or []
    except (OSError, ValueError):
        return []


def _live(env_root):
    return [it for it in _read_queue(env_root) if isinstance(it, dict) and not it.get("retired")]


def _entered_at(it, stage):
    """UTC epoch when `it` most recently entered `stage` (from its history), or None."""
    best = None
    for h in it.get("history", []) or []:
        if isinstance(h, dict) and h.get("stage") == stage:
            ts = h.get("ts")
            try:
                t = datetime.datetime.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S").replace(
                    tzinfo=datetime.timezone.utc).timestamp()
            except (ValueError, TypeError):
                continue
            if best is None or t > best:
                best = t
    return best


def _entered_recently(items, stage, now):
    return [it for it in items
            if (lambda t: t is not None and (now - t) <= _WINDOW_S)(_entered_at(it, stage))]


def summary(env_root):
    items = _live(env_root)
    by_stage = Counter(it.get("stage") for it in items)
    now = time.time()
    nodes = []
    for s in STAGES:
        if s in _BACKLOG_STATE:
            nodes.append({"id": s, "label": _LABELS[s], "count": by_stage.get(_BACKLOG_STATE[s], 0),
                          "kind": "backlog"})
        else:
            n = len(_entered_recently(items, _THROUGHPUT_STAGE[s], now))
            nodes.append({"id": s, "label": _LABELS[s], "count": n, "kind": "phase"})
    # items awaiting the human gate, split by knowledge base (the FO/personal/gm mix)
    by_kb = Counter(it.get("kb") for it in items if it.get("stage") == "awaiting")
    return {
        "nodes": nodes,
        "shipped": by_stage.get("shipped", 0),
        "rejected": by_stage.get("rejected", 0),
        "sorted": by_stage.get("sorted", 0),
        "gate": by_stage.get("awaiting", 0),
        "by_kb": dict(by_kb),
    }


def _row(it):
    """A light drill-in row for a queue item — enough to list and open a read-only info card.
    (Gate items are richer via /api/held; this covers the pre-gate stages.)"""
    return {
        "id": it.get("id"),
        "title": it.get("title") or _title_from_id(it.get("id")),
        "kb": it.get("kb"),
        "source": it.get("source"),
        "lane": it.get("lane"),
        "when": it.get("first_drafted_utc") or it.get("captured_utc"),
        "draft_path": it.get("draft_path"),
        "payload_path": it.get("payload_path"),
        "recommended": it.get("recommended"),
    }


def stage_detail(env_root, stage_id):
    """Full (uncapped) item list for ONE content node — the drill-in source, fetched on node click.
    Backlog nodes list the resting queue; throughput nodes list what entered in the last 24h.
    Unknown stage id returns None so the caller 404s. Best-effort like the rest."""
    if stage_id not in STAGES:
        return None
    items = _live(env_root)
    if stage_id in _BACKLOG_STATE:
        want = _BACKLOG_STATE[stage_id]
        picked = [it for it in items if it.get("stage") == want]
    else:
        picked = _entered_recently(items, _THROUGHPUT_STAGE[stage_id], time.time())
    rows = [_row(it) for it in picked]
    rows.sort(key=lambda r: str(r.get("when") or ""), reverse=True)   # newest first
    return {"id": stage_id, "label": _LABELS[stage_id], "count": len(rows), "items": rows}
