#!/usr/bin/env python3
"""standing_checks.py — the perpetual-invariant runner (A94, engine leg of env-ops H87).

Every other safeguard in the env fires ONCE, at a decision point (the review gate, `test_cmd`,
a Paper-Governs sign-off, a `## Watching` hand-check). Nothing re-verifies a closed item's
invariant over time. *A task you verified once is an assumption with a date on it* — this runner
re-checks a registry of cheap predicates on the nightly brief-cache gather and surfaces the reds.

Two lifetimes, one runner (the H87 Watching-unification):
  - `kind: standing` — checked forever; a red renders `⛑ standing-check red: <id> — <on_violation>`.
  - `kind: watch`    — a one-shot Watching line; expires on first green (marked `observed` and its
                       paired backlog line listed under `watching_clear`), or, if it is still red
                       past `check_by`, renders `👁 watch expired unobserved: <id>`.

Contract (env-health-collect envelope): `run` ALWAYS exits 0 — a missing registry degrades silent,
a corrupt/torn registry becomes a loud `finding`, an unrunnable predicate is a RED with its reason
(never a silent skip — the A79 lesson: a scan that scans nothing must not report clean). The runner
writes ONLY its own zone (`results.json`); it NEVER edits BACKLOG.md — cleared Watching lines are
LISTED for the next interactive session to delete, not deleted here.

Runner state (last_run / first_red / observed) lives in the `results.json` sidecar, NOT written
back into the human-authored `checks.yaml`: round-tripping a hand-parsed YAML would silently drop
its comments and reformat it. The registry stays read-only human data; the sidecar is the runner's
memory (and is what cadence gating + the delta-gate read on the next run).

The render line is delta-gated by the SHARED A93 health-gate (`brief_render.filter_health_lines`)
exactly like the pipeline/factory/economic lines — steady-state all-green is silence. Render lives
here (not in `pipeline_health`) for cohesion: the tool that runs the checks owns their formatting,
mirroring how `resolve_brief` owns its own economic-header line.

stdlib only; fact-free (the registry path is an argument — the gather resolves it from the profile).
Usage:
  python standing_checks.py run --registry <checks.yaml> --out <results.json> [--cwd <dir>]
                                [--now ISO] [--timeout SECONDS]
  python standing_checks.py render --results <results.json>
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

DEFAULT_TIMEOUT = 30
_WEEK_SECONDS = 7 * 86400


# ─────────────────────────── time ───────────────────────────

def _parse_ts(ts):
    """ISO-8601 (date or datetime) → aware datetime, or None. A naive value is taken as UTC."""
    if ts in (None, ""):
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _now(now=None):
    return _parse_ts(now) or datetime.now(timezone.utc)


# ─────────────────────────── registry parse ───────────────────────────
# A tightly-scoped parser for the registry's ONE shape: a top-level `checks:` key whose value is a
# block sequence of flat scalar maps. NOT a general YAML parser — the engine is stdlib-only by
# discipline (state_validate.py set the hand-rolled precedent), and a general parser is far more
# surface than this file needs. Anything outside the shape raises ValueError → a loud finding.

def _strip_comment(line):
    """Drop a trailing `#…` comment that is not inside a quote. A `#` mid-value (no leading space)
    or inside quotes is data, not a comment."""
    out, q, i = [], None, 0
    while i < len(line):
        c = line[i]
        if q:
            out.append(c)
            if c == q:
                q = None
        elif c in ("'", '"'):
            q = c
            out.append(c)
        elif c == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        else:
            out.append(c)
        i += 1
    return "".join(out).rstrip()


def _scalar(v):
    """A YAML scalar value → Python. Quotes stripped; null/~ → None; true/false → bool.

    Outer quotes are stripped ONLY when the value is a single balanced quoted token — i.e. that
    quote char does not appear again inside. A shell predicate like `"grep a" && "grep b"` both
    starts and ends with `"` but is NOT one token; stripping its ends would corrupt it, so it is
    left raw for the shell to parse (which handles the embedded quotes correctly)."""
    v = v.strip()
    if v == "":
        return None
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"') and v[0] not in v[1:-1]:
        return v[1:-1]
    low = v.lower()
    if low in ("null", "~"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    return v


def parse_registry(text):
    """`checks.yaml` text → list[dict]. Raises ValueError on any shape it cannot parse (torn file,
    a stray line outside a list item, no `checks:` key) so the caller can turn it into a finding."""
    records, cur, seen_checks, dash_indent = [], None, False, None
    for raw in text.splitlines():
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        content = stripped.strip()
        if not seen_checks:
            if content == "checks:":
                seen_checks = True
                continue
            # tolerate leading document markers / other top-level keys before `checks:`
            if content in ("---", "...") or (":" in content and indent == 0):
                continue
            raise ValueError("unexpected content before 'checks:': %r" % content)
        if content == "-" or content.startswith("- "):
            cur = {}
            records.append(cur)
            dash_indent = indent
            item = content[1:].strip()
            if item:
                if ":" not in item:
                    raise ValueError("list item is not 'key: value': %r" % content)
                k, _, val = item.partition(":")
                cur[k.strip()] = _scalar(val)
        else:
            if cur is None or dash_indent is None or indent <= dash_indent:
                raise ValueError("line outside any list item: %r" % content)
            if ":" not in content:
                raise ValueError("expected 'key: value', got %r" % content)
            k, _, val = content.partition(":")
            cur[k.strip()] = _scalar(val)
    if not seen_checks:
        raise ValueError("no top-level 'checks:' key")
    return records


# ─────────────────────────── predicate execution ───────────────────────────

def _execute(predicate, cwd, timeout):
    """Run a predicate. Returns (ok, reason): ok=True iff exit 0. Any non-zero exit, timeout, or
    launch failure is ok=False WITH a reason — an unrunnable predicate is a red, never a skip."""
    try:
        proc = subprocess.run(predicate, shell=True, cwd=cwd, timeout=timeout,
                              capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return False, "timed out after %ss" % timeout
    except OSError as e:
        return False, "could not execute: %s" % e
    if proc.returncode == 0:
        return True, ""
    err = (proc.stderr or proc.stdout or "").strip().splitlines()
    reason = "exit %d" % proc.returncode
    if err:
        reason += ": %s" % err[-1][:200]
    return False, reason


# ─────────────────────────── run ───────────────────────────

def _due(check, prior_entry, now_dt):
    """Weekly entries skip unless a full window has passed since their last run; daily always runs.
    An unknown cadence fails toward checking (run it) rather than silently never running."""
    cadence = (check.get("cadence") or "daily").lower()
    if cadence != "weekly":
        return True
    last = _parse_ts((prior_entry or {}).get("last_run"))
    if last is None:
        return True
    return (now_dt - last).total_seconds() >= _WEEK_SECONDS


def run(registry_path, cwd=".", now=None, timeout=DEFAULT_TIMEOUT, prior=None):
    """Read the registry, execute every due predicate, and return the results dict (also what the
    `run` op writes to disk). Never raises on registry/predicate problems — they become findings or
    reds. `prior` (the previous results dict) drives cadence gating + first_red/last_run carry-forward;
    the CLI loads it from `--out` if that file exists."""
    now_dt = _now(now)
    now_iso = now_dt.isoformat()
    result = {"generated_utc": now_iso, "checks": [], "watching_clear": [], "findings": []}
    prior_by_id = {c.get("id"): c for c in ((prior or {}).get("checks") or []) if isinstance(c, dict)}

    try:
        with open(registry_path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return result  # degrade silent — no registry means nothing to check
    except OSError as e:
        result["findings"].append("registry unreadable: %s" % e)
        return result

    try:
        checks = parse_registry(text)
    except ValueError as e:
        result["findings"].append("registry parse error: %s" % e)
        return result

    for chk in checks:
        cid = chk.get("id")
        predicate = chk.get("predicate")
        if not cid or not predicate:
            result["findings"].append("check missing id/predicate: %r" % chk)
            continue
        kind = (chk.get("kind") or "standing").lower()
        prior_entry = prior_by_id.get(cid, {})
        entry = {
            "id": cid,
            "kind": kind,
            "cadence": (chk.get("cadence") or "daily").lower(),
            "origin": chk.get("origin"),
            "on_violation": chk.get("on_violation"),
            "last_run": prior_entry.get("last_run"),
            "first_red": prior_entry.get("first_red"),
            "reason": prior_entry.get("reason"),
            "status": prior_entry.get("status", "pending"),
        }

        if not _due(chk, prior_entry, now_dt):
            # not due — carry the prior verdict forward unchanged (a weekly red persists between runs)
            result["checks"].append(entry)
            continue

        ok, reason = _execute(predicate, cwd, timeout)
        entry["last_run"] = now_iso
        entry["reason"] = reason or None
        if ok:
            entry["first_red"] = None
            if kind == "watch":
                entry["status"] = "observed"
                result["watching_clear"].append(chk.get("watching_line") or cid)
            else:
                entry["status"] = "green"
        else:
            if not entry["first_red"]:
                entry["first_red"] = now_iso
            if kind == "watch":
                raw_cb = chk.get("check_by")
                cb = _parse_ts(raw_cb)
                if raw_cb and cb is None:
                    # an author typo in check_by must not silently hide a red watch (it renders
                    # nothing until it expires) — surface it as a loud finding instead
                    result["findings"].append("check %s: unparseable check_by %r" % (cid, raw_cb))
                entry["status"] = "expired" if (cb is not None and now_dt.date() > cb.date()) else "watching"
            else:
                entry["status"] = "red"
        result["checks"].append(entry)
    return result


# ─────────────────────────── render ───────────────────────────

def render(result):
    """The standing-check health line(s), lifted verbatim into the brief header and then delta-gated
    by the shared A93 health-gate. Reds only: `⛑` for a broken standing invariant, `👁` for a watch
    that expired unobserved, and a loud `⛑ … registry error` for any finding. Empty string when
    clean — the delta-gate renders nothing on steady-state all-green."""
    lines = []
    for c in (result.get("checks") or []):
        st = c.get("status")
        if st == "red":
            lines.append("⛑ standing-check red: %s — %s"
                         % (c.get("id"), c.get("on_violation") or c.get("reason") or "invariant broken"))
        elif st == "expired":
            lines.append("👁 watch expired unobserved: %s" % c.get("id"))
    for finding in (result.get("findings") or []):
        lines.append("⛑ standing-check registry error: %s" % finding)
    return "\n".join(lines)


# ─────────────────────────── io ───────────────────────────

def _atomic_write(path, obj):
    """Atomic JSON write (tmp + os.replace; short PermissionError retry for a concurrent Windows
    reader). Returns True on success."""
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        for _ in range(3):
            try:
                os.replace(tmp, path)
                return True
            except PermissionError:
                time.sleep(0.2)
        return False
    except OSError:
        return False


def _load_prior(out_path):
    try:
        with open(out_path, encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


# ─────────────────────────── cli ───────────────────────────

def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # native Windows console is cp1252
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(
        prog="standing_checks.py",
        description="Re-run a registry of standing invariants; surface reds for the brief.")
    sub = ap.add_subparsers(dest="op", required=True)

    r = sub.add_parser("run", help="execute due predicates and write the results sidecar")
    r.add_argument("--registry", required=True, help="checks.yaml path")
    r.add_argument("--out", required=True, help="results.json sidecar path")
    r.add_argument("--cwd", default=".", help="predicate working directory (default: cwd)")
    r.add_argument("--now", help="ISO timestamp override (tests)")
    r.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                   help="per-predicate timeout seconds (default %d)" % DEFAULT_TIMEOUT)

    d = sub.add_parser("render", help="print the delta-gate-able health line from a results sidecar")
    d.add_argument("--results", required=True, help="results.json sidecar path")

    args = ap.parse_args(argv)

    if args.op == "run":
        prior = _load_prior(args.out)
        result = run(args.registry, cwd=args.cwd, now=args.now,
                     timeout=args.timeout, prior=prior)
        if not _atomic_write(args.out, result):
            # a failed write is itself a finding, but never a non-zero exit (collector contract)
            print("WARN: could not write %s" % args.out, file=sys.stderr)
        reds = sum(1 for c in result["checks"] if c.get("status") in ("red", "expired"))
        print("standing-checks: %d checks, %d red/expired, %d findings"
              % (len(result["checks"]), reds, len(result["findings"])), file=sys.stderr)
        return 0

    if args.op == "render":
        result = _load_prior(args.results) or {}
        line = render(result)
        if line:
            print(line)
        return 0

    return 0  # unreachable (subparser required)


if __name__ == "__main__":
    sys.exit(main())
