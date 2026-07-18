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
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

# A94/A92: task-log run-file naming — `last-result-YYYYMMDD-HHMMSS.txt`. The embedded stamp is the
# authoritative "when did this job run" signal (mtime is unreliable across git-sync / restore), so
# the missed-run detector reads the stamp; a dir with files but NO parseable stamp falls back to the
# newest file mtime rather than reporting a false miss.
_RUN_STAMP_RE = re.compile(r"(\d{8})-(\d{6})")


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
    floored = 0  # A89: below-bar captures routed to searchable raw (no-silent-caps count)
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
                if not (ts and ts >= cutoff):
                    continue
                if rec.get("stage"):
                    runs.append((ts, rec))
                elif rec.get("event") == "floored":
                    floored += 1  # A89 floored trace carries no `stage` — count it separately
    except OSError:
        pass

    window = f"last {hours}h"
    if not runs:
        base = f"⚙️ Pipeline ({window}): no runs logged"
        return base + (f" · {floored} floored→raw" if floored else "")

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
    if floored:
        parts.append(f"{floored} floored→raw")  # A89: earn-your-line, only when >0
    return f"⚙️ Pipeline ({window}): " + " · ".join(parts)


def _newest_run(task_logs_dir, job_id):
    """Newest run datetime for one job, read from its `state/task-logs/<job>/last-result-*.txt`
    stamps. Returns None when the job dir is absent or holds no run files (degrade silent — a job
    with no history yet is NOT a miss). Falls back to the newest file mtime when files exist but
    none carry a parseable stamp, so a differently-named run file still counts as "it ran"."""
    d = os.path.join(task_logs_dir, job_id)
    try:
        names = os.listdir(d)
    except OSError:
        return None
    newest = None
    newest_mtime = None
    for name in names:
        full = os.path.join(d, name)
        if not os.path.isfile(full):
            continue
        m = _RUN_STAMP_RE.search(name)
        if m:
            try:
                # the run-task wrappers stamp LOCAL wall-clock (`(Get-Date)` / bare `date`), so the
                # naive stamp is interpreted as system-local and converted to an absolute instant —
                # matching the mtime fallback's clock, not offset by the UTC delta (which would eat
                # the grace window and tip a healthy job into a false miss).
                dt = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").astimezone()
            except ValueError:
                dt = None
            if dt and (newest is None or dt > newest):
                newest = dt
        try:
            mt = datetime.fromtimestamp(os.path.getmtime(full), timezone.utc)
            if newest_mtime is None or mt > newest_mtime:
                newest_mtime = mt
        except OSError:
            pass
    return newest if newest is not None else newest_mtime


def missed_runs(task_logs_dir, expected_ids, window_hours=30, now=None):
    """Which expected-daily jobs have not run within `window_hours`. Returns [(job_id, last_run_dt)]
    ordered as `expected_ids`. A job with no log history yet is skipped (degrade silent) — reporting
    a miss for a job that has simply never run would be noise, not a missed run."""
    now_dt = _parse_ts(now) or datetime.now(timezone.utc)
    cutoff = now_dt - timedelta(hours=window_hours)
    missed = []
    for job in expected_ids:
        newest = _newest_run(task_logs_dir, job)
        if newest is None:
            continue  # no history — not a miss
        if newest < cutoff:
            missed.append((job, newest))
    return missed


def render_missed(missed):
    """One `⚠ <job> last ran <date> — expected daily` line per missed job; '' when none missed
    (the brief delta-gate renders nothing on the all-ran steady state)."""
    return "\n".join("⚠ %s last ran %s — expected daily" % (job, dt.date().isoformat())
                     for job, dt in missed)


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
    # A92 missed-run detector — config-driven (the expected-daily set + window come from the profile,
    # passed in by the gather; the tool hardcodes NO job list). Both flags absent → detector off.
    ap.add_argument("--task-logs", help="state/task-logs dir (enables the missed-run detector)")
    ap.add_argument("--expected-daily", default="",
                    help="comma-separated job ids expected to run daily (from the profile)")
    ap.add_argument("--window-hours", type=int, default=30,
                    help="a daily job unseen for this many hours is a miss (default 30 — 24h + grace)")
    args = ap.parse_args(argv)
    print(render(args.path, hours=args.hours, now=args.now))
    if args.task_logs:
        expected = [j.strip() for j in args.expected_daily.split(",") if j.strip()]
        line = render_missed(missed_runs(args.task_logs, expected,
                                         window_hours=args.window_hours, now=args.now))
        if line:
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
