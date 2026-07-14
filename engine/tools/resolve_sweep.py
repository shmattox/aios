#!/usr/bin/env python3
"""resolve_sweep.py — flag open tasks that likely need cross-system resolution.

HIGH-RECALL by design: a missed flag means the task is acted on blind (the black box). Trigger =
a money figure OR an economic keyword (profile-supplied) OR (Task-5 wiring) a subject with no
crosswalk. Flag only — it does not resolve. Fact-free (keywords come from the profile), stdlib-only.
"""
import argparse, json, re, sys

# High-recall money detector: a $-figure OR a currency-worded figure ("4,200 USD", "4200 dollars").
# Deliberately does NOT match bare numbers (dates/ids) — over-flagging every digit would drown the signal.
MONEY_RE = re.compile(r"\$\s?\d|\b\d[\d,]*(?:\.\d+)?\s?(?:usd|dollars?)\b", re.I)


def flag_task(task, economic_keywords, resolved_ids):
    """task -> {id, title, reason, domain} if it needs resolution, else None. `domain` is the task's
    domain-group key (pass-through) so the sweep is domain-attributed across every group (A35)."""
    tid = task.get("id")
    if tid in (resolved_ids or set()):
        return None
    text = "%s %s" % (task.get("title", ""), task.get("body", ""))
    dom = task.get("domain")
    if MONEY_RE.search(text):
        return {"id": tid, "title": task.get("title"), "reason": "figure", "domain": dom}
    low = text.lower()
    if any(kw.lower() in low for kw in (economic_keywords or [])):
        return {"id": tid, "title": task.get("title"), "reason": "economic-keyword", "domain": dom}
    return None


def sweep(tasks, economic_keywords, resolved_ids):
    out = []
    for t in tasks or []:
        f = flag_task(t, economic_keywords, resolved_ids or set())
        if f:
            out.append(f)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Flag open tasks that need resolution")
    ap.add_argument("payload", help="JSON: {tasks:[...], economic_keywords:[...], resolved_ids:[...]}")
    args = ap.parse_args(argv)
    with open(args.payload, encoding="utf-8") as f:
        d = json.load(f)
    flagged = sweep(d.get("tasks"), d.get("economic_keywords"), set(d.get("resolved_ids") or []))
    print(json.dumps({"flagged": flagged}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
