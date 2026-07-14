#!/usr/bin/env python3
"""pipeline_health.py — render the ONE overnight-pipeline health line for the brief headline.

The brief render lifts this line verbatim into the header at render time (deterministic
render: the engine emits the format, the model never reproduces it from prose). It answers
"how did the overnight pipeline run go" without a manual ask: runs + stages in the window,
shipped (summed — ship events are per-run increments), queue held (latest snapshot — held is
a queue size, summing across runs would double-count), and anomalies by stage.

Tolerates malformed log lines (the context-log has a known torn-line history):
unparseable lines are skipped, never fatal. A missing or empty
log renders a "no runs" line — the brief must always paint.

stdlib only; fact-free (the log path is an argument).
Usage: python pipeline_health.py --path <context-log.jsonl> [--hours 30] [--now ISO]
"""
import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone


def _parse_ts(ts):
    """ISO-8601 → aware datetime, or None. Log lines carry 'Z' suffixes; a naive
    timestamp (improvised log writes exist — see A25) is taken as UTC, not fatal."""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _count(val):
    """Numeric log field → int, tolerating string counts; anything else → 0."""
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def render(path, hours=30, now=None):
    """One markdown line summarizing pipeline runs in the last `hours`.

    `now` (ISO string) exists for deterministic tests; defaults to wall clock.
    """
    now_dt = _parse_ts(now) or datetime.now(timezone.utc)
    cutoff = now_dt - timedelta(hours=hours)

    runs = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # torn/clobbered line — skip, never fatal
                if not isinstance(rec, dict):
                    continue  # parses-but-wrong-shape line — same policy
                ts = _parse_ts(rec.get("ts"))
                if ts and ts >= cutoff and rec.get("stage"):
                    runs.append((ts, rec))
    except OSError:
        pass

    window = f"last {hours}h"
    if not runs:
        return f"⚙️ Pipeline ({window}): no runs logged"

    runs.sort(key=lambda r: r[0])
    stages = {rec["stage"] for _, rec in runs}
    shipped = sum(_count(rec.get("shipped")) for _, rec in runs)
    held = next((_count(rec["held"]) for _, rec in reversed(runs)
                 if rec.get("held") is not None), None)
    anomalies = Counter()
    for _, rec in runs:
        a = rec.get("anomalies")
        n = len(a) if isinstance(a, list) else 0
        if n:
            anomalies[rec["stage"]] += n

    parts = [f"{len(runs)} runs / {len(stages)} stages",
             f"shipped {shipped}"]
    if held is not None:
        parts.append(f"queue held {held}")
    if anomalies:
        by_stage = ", ".join(f"{s} ×{n}" for s, n in anomalies.most_common())
        parts.append(f"⚠ {sum(anomalies.values())} anomalies ({by_stage})")
    else:
        parts.append("✅ no anomalies")
    return f"⚙️ Pipeline ({window}): " + " · ".join(parts)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # native Windows console is cp1252
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(
        prog="pipeline_health.py",
        description="Render the one-line overnight-pipeline health summary for the brief headline.")
    ap.add_argument("--path", required=True, help="context-log.jsonl path")
    ap.add_argument("--hours", type=int, default=30, help="lookback window (default 30)")
    ap.add_argument("--now", help="ISO timestamp override (tests)")
    args = ap.parse_args(argv)
    print(render(args.path, hours=args.hours, now=args.now))
    return 0


if __name__ == "__main__":
    sys.exit(main())
