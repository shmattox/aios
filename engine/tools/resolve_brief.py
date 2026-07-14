#!/usr/bin/env python3
"""resolve_brief.py — the brief's deterministic bridge to the A34 warm resolve cache (A31).

Three ops, all stdlib-only:
  worklist <sweep_path>            -> the enumerated flagged economic tasks the brief MUST resolve.
  check <sweep_path> <cache_dir>   -> a tool-emitted, verbatim-lifted line that is loud when any
                                      flagged task has no dossier, or the sweep itself is missing/
                                      unreadable, or a dossier is stale for the current sweep
                                      (the anti-skip forcing function).
  write <cache_dir> <task_id> <dossier_json_file> <sweep_hash> -> sanitizes the id and stamps the
                                      sweep hash before writing the dossier (the write-side of the
                                      same forcing function).
The brief lifts worklist/check verbatim; neither is model-composed.
"""
import argparse, json, os, re, sys

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]")

# A60: after this many consecutive sweeps with an identical worklist shape (id + candidate refs),
# an unresolved-but-stable backlog is a known ceiling, not fresh news — check() demotes its loud
# 'INCOMPLETE' alarm to a quiet steady-state line. Any real change (new/resolved task, changed
# candidates) resets the sweep-side counter, so the alarm returns at full volume the same day.
STEADY_STATE_DAYS = 3


def _safe_id(task_id):
    """A filesystem-safe dossier stem for a task id, or None if the id is unusable.
    Blocks path traversal / absolute paths / null-blank ids — such a task can never be
    'resolved' (its dossier can't be safely written or found), so callers treat None as unresolved."""
    if task_id is None:
        return None
    s = str(task_id).strip()
    if not s or s in (".", ".."):
        return None
    s = _SAFE_ID_RE.sub("_", s)          # '../x' -> '.._x', '/etc/x' -> '_etc_x'
    s = re.sub(r"\.{2,}", "_", s)        # collapse surviving '..' runs (single dots allowed
                                          # by the char class above still traverse when repeated)
    return s or None


def _load_sweep(sweep_path):
    """Returns the sweep dict, or None if the file is absent/unreadable/not a dict."""
    try:
        with open(sweep_path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _load_flagged(sweep_path):
    return (_load_sweep(sweep_path) or {}).get("flagged") or []


def _staleness_line(cache_dir):
    """A loud line when the LAST sweep(s) degraded (the source was unreachable), read from the
    resolve_sweep_task freshness sidecar (`sweep-status.json`). A degraded sweep PRESERVES the prior
    warm cache — right, but a permanently-misconfigured source degrades forever and the worklist
    silently freezes while looking fresh (A49). This makes that visible. Returns '' when the last sweep
    reached the source, or when no sidecar exists (a pre-A49 cache — never false-alarm)."""
    try:
        with open(os.path.join(cache_dir, "sweep-status.json"), encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(st, dict):
        return ""
    try:
        cd = int(st.get("consecutive_degraded") or 0)
    except (TypeError, ValueError):
        cd = 0
    if cd < 1:
        return ""
    last_good = st.get("last_good_utc") or "never (no good sweep on record)"
    return ("⚠ resolve sweep DEGRADED — the last %d sweep attempt(s) could not reach the source; the "
            "economic worklist may be STALE (showing the last good sweep from %s)" % (cd, last_good))


def _candidates_unchanged_days(cache_dir):
    """A60: how many consecutive good sweeps produced an identical worklist shape (0 if unknown),
    from the resolve_sweep_task stability counter in the same `sweep-status.json` sidecar."""
    try:
        with open(os.path.join(cache_dir, "sweep-status.json"), encoding="utf-8") as f:
            st = json.load(f)
        return int((st or {}).get("candidates_unchanged_days") or 0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0


def worklist(sweep_path):
    """sweep.json -> [{task_id, title, candidates}]; [] when absent/empty/unreadable."""
    out = []
    for t in _load_flagged(sweep_path):
        out.append({"task_id": t.get("id"), "title": t.get("title") or "",
                    "candidates": t.get("candidates") or []})
    return out


def economic_header(sweep_path):
    """The verbatim '⚠ N economic figures with no paper' headline line (A35), derived from the sweep's
    OWN flagged list across every domain group it gathered. 'no paper' = a flagged economic figure the
    overnight crosswalk found NO candidate governing doc for (candidates == []). The count is recomputed
    here from sweep['flagged'] — a pure function of the sweep, so it CANNOT render a number the sweep did
    not produce (and it provably equals the sweep's own no_paper_count, the same derivation). Returns
    {count, domains, line}; a missing/unreadable sweep fails loud (count None), same discipline as check."""
    sweep = _load_sweep(sweep_path)
    if sweep is None:
        return {"count": None, "domains": [],
                "line": "⚠ economic-figure header UNAVAILABLE — sweep.json absent/unreadable at %s" % sweep_path}
    no_paper = [t for t in (sweep.get("flagged") or []) if not (t.get("candidates") or [])]
    domains = sorted({t.get("domain") for t in no_paper if t.get("domain")})
    n = len(no_paper)
    if n == 0:
        return {"count": 0, "domains": [],
                "line": "🟢 economic figures: every flagged figure has candidate paper ✓"}
    across = (" across %s" % ", ".join(domains)) if domains else ""
    line = "⚠ **%d economic figure%s with no paper**%s" % (n, "s" if n != 1 else "", across)
    return {"count": n, "domains": domains, "line": line}


def check(sweep_path, cache_dir):
    """Every flagged task must have a dossier (<cache_dir>/<safe_id>.json) written for THIS
    sweep (matching sweep_hash). Returns {complete, missing[], line}; line is the verbatim
    loud output ('' when complete). Fails loud (never 'complete') when the sweep itself is
    missing/unreadable — the A34 sweep ALWAYS writes sweep.json (even with 0 flagged), so its
    absence means the overnight resolve sweep never ran / is broken."""
    stale = _staleness_line(cache_dir)   # orthogonal to dossier completeness — a complete sweep can rot
    sweep = _load_sweep(sweep_path)
    if sweep is None:
        line = ("⚠ resolve cache MISSING — sweep.json absent or unreadable at %s; "
                "economic resolution NOT verified this run" % sweep_path)
        return {"complete": False, "missing": [], "stale": bool(stale),
                "line": "\n".join(x for x in (stale, line) if x)}
    flagged = sweep.get("flagged") or []
    sweep_hash = sweep.get("content_hash")
    missing = []
    for t in flagged:
        sid = _safe_id(t.get("id"))
        if sid is None:
            missing.append(str(t.get("id")))     # null/unsafe id can never be resolved -> loud
            continue
        path = os.path.join(cache_dir, sid + ".json")
        if not os.path.exists(path):
            missing.append(t.get("id")); continue
        # staleness: a dossier only counts if it was written for THIS sweep (content_hash match)
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            missing.append(t.get("id")); continue
        if d.get("task_id") != t.get("id"):
            # collision guard: two raw ids can sanitize to the same <stem>.json; a dossier only
            # counts for the task whose EXACT id it stores (write_dossier stamps the raw id) — else a
            # distinct task's file would silently mark this one resolved.
            missing.append(t.get("id")); continue
        if sweep_hash is not None and d.get("sweep_hash") != sweep_hash:
            missing.append(t.get("id"))           # stale dossier from a prior sweep -> re-resolve
    if not missing:
        return {"complete": True, "missing": [], "stale": bool(stale), "line": stale}
    days = _candidates_unchanged_days(cache_dir)
    if not stale and days >= STEADY_STATE_DAYS:
        # A60: a healthy sweep whose unresolved worklist has been identical for >= N days is a known
        # ceiling, not fresh news — demote to a quiet informational line (no id list). NOT applied when
        # degraded (stale): there the source is unreachable, so 'steady-state' would be a false calm.
        line = ("ℹ resolve steady-state — %d of %d flagged economic tasks unresolved, worklist "
                "unchanged %dd (known ceiling; a change re-alarms)" % (len(missing), len(flagged), days))
    else:
        line = "⚠ resolve INCOMPLETE — %d of %d flagged economic tasks unresolved: %s" % (
            len(missing), len(flagged), ", ".join(str(m) for m in missing))
    return {"complete": False, "missing": missing, "stale": bool(stale),
            "line": "\n".join(x for x in (stale, line) if x)}


def write_dossier(cache_dir, task_id, dossier, sweep_hash):
    """Write a dossier to <cache_dir>/<safe_id>.json, stamping sweep_hash. Returns the path.
    Refuses an unsafe/blank id (raises ValueError) — a task that can't be safely keyed is not resolvable."""
    sid = _safe_id(task_id)
    if sid is None:
        raise ValueError("unsafe or blank task id: %r" % (task_id,))
    os.makedirs(cache_dir, exist_ok=True)
    rec = dict(dossier); rec["task_id"] = task_id; rec["sweep_hash"] = sweep_hash
    path = os.path.join(cache_dir, sid + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="Brief bridge to the resolve warm cache")
    sub = ap.add_subparsers(dest="op", required=True)
    w = sub.add_parser("worklist"); w.add_argument("sweep_path")
    h = sub.add_parser("header"); h.add_argument("sweep_path")
    c = sub.add_parser("check"); c.add_argument("sweep_path"); c.add_argument("cache_dir")
    wr = sub.add_parser("write")
    wr.add_argument("cache_dir"); wr.add_argument("task_id")
    wr.add_argument("dossier_json_file"); wr.add_argument("sweep_hash")
    args = ap.parse_args(argv)
    if args.op == "worklist":
        print(json.dumps({"worklist": worklist(args.sweep_path)}, ensure_ascii=False))
    if args.op == "header":
        # verbatim-lifted into the brief headline card; never model-composed (like check)
        try:
            sys.stdout.reconfigure(encoding="utf-8")   # the line carries ⚠/🟢 glyphs
        except (AttributeError, ValueError):
            pass
        print(economic_header(args.sweep_path)["line"])
        return 0
    if args.op == "check":
        r = check(args.sweep_path, args.cache_dir)
        if r["line"]:
            print(r["line"])
        return 0
    if args.op == "write":
        with open(args.dossier_json_file, encoding="utf-8") as f:
            dossier = json.load(f)
        try:
            path = write_dossier(args.cache_dir, args.task_id, dossier, args.sweep_hash)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        print(path)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
