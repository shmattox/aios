#!/usr/bin/env python3
"""gate_metrics.py — A73: read-only acceptance metrics over queue terminal items.

Reads state/queue.json via queue_tx.load (never hand-parsed) and rolls up, per window
(all / 30d / 7d keyed on the injected --today):
  outcome (accepted / rejected / reverted)  x  decider class (human / auto / scheduled / unknown)
  x  recommendation agreement (agree / override / hold / na)  x  (kb, lane).

EXTRACTION METHOD (load-bearing, method-sensitive — spec 2026-07-15 §Ecosystem-check):
history values are read as the MOST RECENT history entry CARRYING the key (reverse scan,
entries without the key are skipped). One method, tested; do not add variants.

Fact-free: the decider classifier hardcodes no person names — approved_by is either one of
the two auto constants or the approver's name (gate SKILL contract), so any other non-empty
value classifies `human`. Read-only; fail-soft `render` (loud "unavailable", never zeros).
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import queue_tx  # noqa: E402

TERMINAL = ("shipped", "rejected", "reverted")
_OUTCOME = {"shipped": "accepted", "rejected": "rejected", "reverted": "reverted"}
WINDOWS = (("all", None), ("30d", 30), ("7d", 7))
OVERRIDE_ID_CAP = 20  # rendered list cap; the count is never capped


def _hist_value(item, key):
    for h in reversed(item.get("history", []) or []):
        if key in h:
            return h[key]
    return None


def decider_class(item):
    v = _hist_value(item, "decided_by")
    if v in ("human", "auto", "scheduled"):
        return v
    raw = _hist_value(item, "approved_by")
    if raw is None or not str(raw).strip():
        return "unknown"
    r = str(raw).strip().lower()
    if r == "auto-ship-scheduled":
        return "scheduled"
    if r == "auto-ship":
        return "auto"
    return "human"


def outcome(item):
    return _OUTCOME.get(item.get("stage"), "")


def agreement(item):
    out = outcome(item)
    if out == "reverted" or out == "":
        return "na"
    rec = item.get("recommended")
    if rec == "hold":
        return "hold"
    if rec not in ("approve", "reject"):
        return "na"
    hit = (rec == "approve" and out == "accepted") or (rec == "reject" and out == "rejected")
    return "agree" if hit else "override"


def terminal_date(item):
    for h in reversed(item.get("history", []) or []):
        if h.get("stage") in TERMINAL and h.get("ts"):
            return str(h["ts"])[:10]
    for h in reversed(item.get("history", []) or []):
        if h.get("ts"):
            return str(h["ts"])[:10]
    return None


def _empty_window():
    return {"n": 0, "unknown_ts": 0,
            "totals": {"accepted": 0, "rejected": 0, "reverted": 0},
            "deciders": {"human": 0, "auto": 0, "scheduled": 0, "unknown": 0},
            "agreement": {"agree": 0, "override": 0, "hold": 0, "na": 0},
            "override_ids": [], "by_kb_lane": {}}


def _days_ago(today, d):
    try:
        ty, tm, td = (int(x) for x in today.split("-"))
        y, m, dd = (int(x) for x in d.split("-"))
        return (date(ty, tm, td) - date(y, m, dd)).days
    except (ValueError, TypeError):
        return None


def rollup(items, today):
    wins = {name: _empty_window() for name, _ in WINDOWS}
    for it in items:
        out = outcome(it)
        if not out:
            continue
        tdate = terminal_date(it)
        age = _days_ago(today, tdate) if tdate else None
        for name, span in WINDOWS:
            w = wins[name]
            if span is None:
                if age is None:
                    w["unknown_ts"] += 1
            else:
                if age is None or age < 0 or age > span:
                    continue
            w["n"] += 1
            w["totals"][out] += 1
            w["deciders"][decider_class(it)] += 1
            agr = agreement(it)
            w["agreement"][agr] += 1
            if agr == "override" and len(w["override_ids"]) < OVERRIDE_ID_CAP:
                w["override_ids"].append(it.get("id", "?"))
            key = f"{it.get('kb') or '?'}|{it.get('lane') or '?'}"
            cell = w["by_kb_lane"].setdefault(key, {"accepted": 0, "rejected": 0, "reverted": 0})
            cell[out] += 1
    return {"generated": today, "windows": wins}


def report(queue_path, today):
    data = queue_tx.load(queue_path)
    return rollup(data.get("queue", []), today)
