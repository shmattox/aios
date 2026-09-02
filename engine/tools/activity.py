"""Run-record contract: the single home for 'what is running right now' across every
agent surface. One JSON file per run under state/activity/; live logs under
state/activity/logs/. Every write is best-effort and MUST NOT raise into a producer's
real work — callers wrap nothing; this module swallows its own I/O errors."""
import contextlib, errno, json, os, sys, time
from pathlib import Path

LIVE_WINDOW_S = 90
SESSION_LIVE_WINDOW_S = 300   # a session whose transcript was written this recently is "running now"
SURFACES = ("factory", "pipeline", "session", "goal", "workflow")
TERMINAL = ("shipped", "parked", "no-op", "failed", "ended")
SAFE_ID_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-")


def ACTIVITY_DIR(env_root):
    return Path(env_root) / "state" / "activity"


def LOGS_DIR(env_root):
    return ACTIVITY_DIR(env_root) / "logs"


def _safe_id(rid):
    return bool(rid) and len(rid) <= 160 and all(c in SAFE_ID_CHARS for c in rid)


def _now(now):
    return time.time() if now is None else now


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _atomic_write(path, obj):
    """Best-effort atomic JSON write. Never raises."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        os.replace(tmp, path)
    except OSError:
        pass


# errnos meaning "someone else holds it, try again" -- everything else means the filesystem
# does not implement locking, which must NOT be retried (see the retry loop below).
_LOCK_TIMEOUT_S = 5.0   # module-level so a test can shrink it and still exercise the REAL lock
_LOCK_CONTENDED = frozenset(x for x in (
    getattr(errno, "EACCES", None),      # msvcrt.locking, Windows contention
    getattr(errno, "EAGAIN", None),      # fcntl.flock, POSIX contention
    getattr(errno, "EWOULDBLOCK", None), # == EAGAIN on POSIX; on Windows this is
                                         # WSAEWOULDBLOCK (10035, a Winsock errno a
                                         # file lock never raises) -- harmless, and
                                         # EAGAIN is in the set separately.
    getattr(errno, "EDEADLK", None),
    getattr(errno, "EDEADLOCK", None),
) if x is not None)


LOCK_UNSUPPORTED_MARKER = ".lock-unsupported"
_lock_unsupported_marked = False   # in-process latch; see _mark_lock_unsupported


def _mark_lock_unsupported(lock_path, err):
    """Drop ONE marker next to the run records when the filesystem cannot lock at all.

    Why this exists: an unlockable mount (ENOLCK/EINVAL/EOPNOTSUPP on SMB, NFS, some FUSE mounts)
    degrades PERMANENTLY and silently -- every write from then on runs with zero concurrency
    protection, which is precisely the corruption A124 added the lock to prevent. Before the errno
    fix it at least announced itself as a multi-second stall per call; removing that stall made the
    failure quieter rather than rarer, so it needs a trace of its own.

    Deliberately a marker FILE, not a record field: it is an install-level property, not a property
    of any one run, and this keeps the record contract untouched. Written once (skipped if present)
    so a hot loop cannot churn it. Best-effort and silent like the rest of this module's I/O.
    """
    global _lock_unsupported_marked
    if _lock_unsupported_marked:
        return                       # unconditional: os.path.exists() alone is not a latch (below)
    try:
        marker = os.path.join(os.path.dirname(lock_path), LOCK_UNSUPPORTED_MARKER)
        # NOT `if os.path.exists(marker): return` on its own. exists() swallows OSError and
        # returns False, so on the flaky SMB/NFS mount this marker EXISTS for, a transient stat
        # failure would re-write it every call — and it costs a syscall per mutator call forever.
        # The in-process flag above is the real latch; this only avoids clobbering a marker a
        # PREVIOUS process wrote. A present-but-EMPTY marker is repaired rather than trusted: a
        # torn or interrupted first write would otherwise latch permanently while saying nothing.
        try:
            if os.path.getsize(marker) > 0:
                _lock_unsupported_marked = True
                return
        except OSError:
            pass                     # absent, or unstattable — fall through and (re)write
        name = errno.errorcode.get(getattr(err, "errno", None), str(getattr(err, "errno", "?")))
        # Written atomically (tmp + os.replace, the convention _atomic_write already sets here) so
        # a partial write can never latch as a content-free marker.
        tmp = "%s.%d.tmp" % (marker, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(
                "advisory file locking is unavailable, or failed with a non-contention errno\n"
                "errno: %s (%s)\n"
                "when:  %s\n"
                "note:  a non-contention errno is TREATED as permanent (retrying it would stall\n"
                "  every call). ENOLCK in particular can also mean transient kernel lock-record\n"
                "  exhaustion, so this marker is a lead, not a verdict on the filesystem.\n"
                "meaning: activity.py run-record writes are proceeding UNLOCKED, so concurrent\n"
                "  span writes from parallel agent processes can drop spans, duplicate span_ids\n"
                "  and undercount run_cost (A124). Move state/activity/ to a filesystem that\n"
                "  supports locking, or accept single-writer use. Delete this file to re-arm.\n"
                % (name, getattr(err, "strerror", ""),
                   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            )
        os.replace(tmp, marker)
        _lock_unsupported_marked = True
    except Exception:
        pass


@contextlib.contextmanager
def _run_lock(path, timeout=None, poll=0.02):
    """Cross-process advisory lock guarding one run record's read-modify-write window
    (msvcrt on Windows, flock on POSIX). Fan-out agents each shell out to this CLI as
    SEPARATE OS PROCESSES against one run record, so the lock must be OS-level -- an
    in-process lock would serialize nothing. Best-effort like the rest of this module:
    any locking failure still yields (the write proceeds unlocked) rather than raising
    into a producer, and a wedged lock degrades to 'proceed' at the deadline instead of
    blocking a producer's real work forever.

    NOT REENTRANT: never call one locked mutator from inside another -- a nested acquire on
    the same path stalls the full deadline and then proceeds UNLOCKED."""
    timeout = _LOCK_TIMEOUT_S if timeout is None else timeout
    lock_path = f"{path}.lock"
    fh = None
    locked = False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fh = open(lock_path, "a+b")
        if os.fstat(fh.fileno()).st_size < 1:
            fh.write(b"L")   # any single byte; msvcrt.locking needs >=1 byte to lock a range
            fh.flush()
        deadline = time.time() + timeout
        while True:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError as e:
                # CONTENTION means another holder has it: retry. Anything else means this
                # filesystem cannot lock at all (ENOLCK/EINVAL/EOPNOTSUPP on SMB, NFS, some
                # FUSE mounts) -- retrying that is dead time on EVERY call, forever, and would
                # stall a producer for the full deadline per mutator. Give up immediately and
                # proceed unlocked, which is exactly the pre-lock behaviour.
                if e.errno not in _LOCK_CONTENDED:
                    # PERMANENT: this filesystem cannot lock. Leave a trace, since from here on
                    # every write is unprotected and nothing else would ever say so.
                    _mark_lock_unsupported(lock_path, e)
                    break
                if time.time() >= deadline:
                    break   # contended past the deadline: the designed, self-limiting degradation
                time.sleep(poll)
    except Exception:
        # deliberately broad: this module's contract is that its own I/O NEVER raises into a
        # producer's real work, and the acquire path can fail in non-OSError ways (e.g. ImportError
        # on a build without fcntl). Failing to lock must degrade, never propagate.
        pass
    try:
        yield
    finally:
        if fh is not None:
            if locked:
                try:
                    if sys.platform == "win32":
                        import msvcrt
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                fh.close()
            except OSError:
                pass


def _rec_path(env_root, rid):
    return str(ACTIVITY_DIR(env_root) / f"{rid}.json")


def session_run_id(slug, pid):
    """A filesystem/SAFE_ID-safe run id for a session record."""
    safe = "".join(c if c in SAFE_ID_CHARS else "-" for c in (slug or "session"))
    return f"session-{safe}-{pid}"


def start_run(env_root, *, id, surface, title, item_ids=(), repo=None, pid=None,
              log_path=None, worktree=None, detail="", parent_id=None, root=None, now=None):
    if not _safe_id(id) or surface not in SURFACES:
        return None
    ts = _now(now)
    rec = {"id": id, "surface": surface, "title": title, "item_ids": list(item_ids),
           "repo": repo, "pid": pid, "started": ts, "heartbeat": ts, "ended": None,
           "status": "running", "tokens": 0, "cost_usd": 0.0,
           "input_tokens": 0, "output_tokens": 0,
           "parent_id": parent_id, "root": (parent_id is None) if root is None else bool(root),
           "spans": [], "pending_approval": None,
           "log_path": log_path, "detail": detail or "", "worktree": worktree}
    _atomic_write(_rec_path(env_root, id), rec)
    return rec


def heartbeat(env_root, id, *, now=None, **updates):
    if not _safe_id(id):  # symmetric with start_run — never resolve a path outside state/activity/
        return
    path = _rec_path(env_root, id)
    with _run_lock(path):
        rec = _read_json(path)
        if not rec:
            return
        rec["heartbeat"] = _now(now)
        for k in ("tokens", "cost_usd", "detail", "log_path", "worktree", "pid"):
            if k in updates:
                rec[k] = updates[k]
        _atomic_write(path, rec)


def start_span(env_root, run_id, *, name, kind="internal", parent_span_id=None, now=None):
    if not _safe_id(run_id):
        return None
    path = _rec_path(env_root, run_id)
    with _run_lock(path):
        rec = _read_json(path)
        if not rec:
            return None
        spans = rec.setdefault("spans", [])
        span_id = f"{run_id}#{len(spans) + 1}"
        spans.append({"span_id": span_id, "parent_span_id": parent_span_id, "name": name,
                      "kind": kind, "status": "running", "start": _now(now), "end": None,
                      "input_tokens": 0, "output_tokens": 0, "cost": None, "error": None})
        rec["heartbeat"] = _now(now)
        _atomic_write(path, rec)
    return span_id


def end_span(env_root, run_id, span_id, *, status="ok", input_tokens=0, output_tokens=0,
             cost=None, error=None, now=None):
    if not _safe_id(run_id):
        return
    path = _rec_path(env_root, run_id)
    with _run_lock(path):
        rec = _read_json(path)
        if not rec:
            return
        for s in rec.get("spans", []):
            if s.get("span_id") == span_id:
                s.update({"status": status, "end": _now(now), "input_tokens": input_tokens,
                          "output_tokens": output_tokens, "cost": cost, "error": error})
                break
        rec["heartbeat"] = _now(now)
        _atomic_write(path, rec)


def request_approval(env_root, run_id, *, kind, prompt, resume_token=None, now=None):
    if not _safe_id(run_id):
        return
    path = _rec_path(env_root, run_id)
    with _run_lock(path):
        rec = _read_json(path)
        if not rec:
            return
        ts = _now(now)
        rec["status"] = "awaiting_approval"
        rec["pending_approval"] = {"awaiting": True, "kind": kind, "prompt": prompt,
                                   "resume_token": resume_token, "requested_at": ts,
                                   "responded_at": None, "decision": None}
        rec["heartbeat"] = ts
        _atomic_write(path, rec)


def resolve_approval(env_root, run_id, *, decision, now=None):
    if not _safe_id(run_id):
        return
    path = _rec_path(env_root, run_id)
    with _run_lock(path):
        rec = _read_json(path)
        if not rec:
            return
        ts = _now(now)
        pa = rec.get("pending_approval") or {}
        pa.update({"awaiting": False, "decision": decision, "responded_at": ts})
        rec["pending_approval"] = pa
        rec["status"] = "running"
        rec["heartbeat"] = ts
        _atomic_write(path, rec)


def finish_run(env_root, id, status, *, now=None, **updates):
    if not _safe_id(id):  # symmetric with start_run — never resolve a path outside state/activity/
        return
    path = _rec_path(env_root, id)
    with _run_lock(path):
        rec = _read_json(path)
        if not rec:
            return
        ts = _now(now)
        rec["status"] = status if status in TERMINAL else "ended"
        rec["ended"] = ts
        rec["heartbeat"] = ts
        for k in ("tokens", "cost_usd", "detail", "log_path", "worktree"):
            if k in updates:
                rec[k] = updates[k]
        _atomic_write(path, rec)


def run_cost(rec):
    """Derived run cost = sum of span costs (None-safe). Cost is never stored raw as truth."""
    return float(sum(s.get("cost") or 0.0 for s in (rec.get("spans") or [])))


def build_graph(records):
    """Project the span tree into {nodes, edges} for a layout engine (dagre). One rule:
    an edge exists wherever a span declares a parent_span_id — intra-run tree AND cross-run
    fan-out both fall out of it (the OTel parent_span_id contract)."""
    nodes, edges = [], []
    for r in records or []:
        for s in r.get("spans") or []:
            nodes.append({"id": s["span_id"], "label": s.get("name", ""),
                          "status": s.get("status", "running"), "kind": s.get("kind", "internal"),
                          "run_id": r.get("id")})
            if s.get("parent_span_id"):
                edges.append({"source": s["parent_span_id"], "target": s["span_id"]})
    return {"nodes": nodes, "edges": edges}


def read_all(env_root):
    out = []
    d = ACTIVITY_DIR(env_root)
    now = time.time()
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return out
    for n in names:
        if not n.endswith(".json"):
            continue
        rec = _read_json(d / n)
        if not rec:
            continue
        rec["live"] = is_live(rec, now)
        rec["age_s"] = max(0.0, now - rec.get("started", now))
        out.append(rec)
    return out


def _pid_alive(pid):
    """Cross-platform 'does this pid exist' — never raises, returns bool."""
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k = ctypes.windll.kernel32
        h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if k.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True  # handle opened but query failed -> assume alive
        finally:
            k.CloseHandle(h)
    else:
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # exists, not ours
        except OSError:
            return False
        return True


def is_live(rec, now):
    if rec.get("status") != "running":
        return False
    if _pid_alive(rec.get("pid")):
        return True
    if (now - rec.get("heartbeat", 0)) <= LIVE_WINDOW_S:
        return True
    # A session's transcript (log_path) is appended on every turn, so a fresh mtime is the real
    # "this session is active right now" signal — the id-encoded pid is the ephemeral hook process
    # and the record isn't otherwise heartbeated, so without this a running session decays to dead.
    lp = rec.get("log_path")
    if lp:
        try:
            return (now - os.path.getmtime(lp)) <= SESSION_LIVE_WINDOW_S
        except OSError:
            return False
    return False


def run_with_activity(env_root, *, id, title, stages, now=None):
    """Run an ordered [(name, callable)] sequence as a 'pipeline' run, heartbeating the
    current stage. Best-effort recording; a stage callable's own exception propagates
    (the caller owns its error handling), but a finish_run(failed) is written first."""
    start_run(env_root, id=id, surface="pipeline", title=title, pid=os.getpid(), now=now)
    try:
        for name, fn in stages:
            heartbeat(env_root, id, detail=name, now=now)
            fn()
    except BaseException:
        finish_run(env_root, id, "failed", now=now)
        raise
    finish_run(env_root, id, "ended", now=now)
    return True


def prune(env_root, *, retain_s=86400, now=None):
    now = _now(now)
    d = ACTIVITY_DIR(env_root)
    removed = 0
    try:
        names = list(os.listdir(d))
    except OSError:
        return 0
    for n in names:
        if not n.endswith(".json"):
            continue
        rec = _read_json(d / n)
        if not rec:
            continue
        ended = rec.get("ended")
        if rec.get("status") in TERMINAL and ended is not None and (now - ended) > retain_s:
            # d/n came from listdir so it is in-dir; the log path is built from the record's own
            # id field (JSON content, never validated) — guard it so a crafted "../" id can't
            # os.remove an arbitrary .log outside LOGS_DIR.
            # the record's own `.lock` sidecar (A124) rides along: same in-dir name from
            # listdir plus a fixed suffix, so it inherits d/n's traversal safety.
            paths = [d / n, d / f"{n}.lock"]
            rid = rec.get("id")
            if _safe_id(rid):
                paths.append(LOGS_DIR(env_root) / f"{rid}.log")
            for p in paths:
                try:
                    os.remove(p)
                except OSError:
                    pass
            removed += 1
    return removed


# ── CLI (so a scheduled-task RUNNER can bracket each stage's `claude -p` from shell/PS) ──
# The deploy runners (deploy/{windows,mac,linux}) call:
#   activity.py start  --env-root <r> --id <id> --surface pipeline --title <task> --detail <stage> --pid <RUNNER_PID>
#   activity.py finish --env-root <r> --id <id> --status ended|failed
# Passing the RUNNER's own pid makes the record live for exactly the stage's lifetime with no
# periodic heartbeat (the runner process is alive iff the stage is running). Every subcommand is
# best-effort: a runtime failure NEVER breaks the producer's run (exit 0); only malformed CLI
# *usage* (unknown/absent subcommand, missing required flag) exits non-zero, for tests + humans.

def main(argv=None):
    import argparse
    try:
        from _util import utf8_stdio
        utf8_stdio()
    except Exception:
        pass
    p = argparse.ArgumentParser(
        prog="activity.py",
        description="Live-run activity record CLI (best-effort; never breaks a producer).")
    sub = p.add_subparsers(dest="cmd")

    def _common(sp):
        sp.add_argument("--env-root", required=True)
        sp.add_argument("--id", required=True)

    st = sub.add_parser("start", help="write a running record")
    _common(st)
    st.add_argument("--surface", required=True)
    st.add_argument("--title", default="")
    st.add_argument("--detail", default=None)
    st.add_argument("--pid", type=int, default=None)
    st.add_argument("--repo", default=None)
    st.add_argument("--item-ids", default="", help="comma-separated backlog ids")
    st.add_argument("--log-path", default=None)
    st.add_argument("--worktree", default=None)

    hb = sub.add_parser("heartbeat", help="refresh a running record")
    _common(hb)
    hb.add_argument("--detail", default=None)
    hb.add_argument("--tokens", type=int, default=None)
    hb.add_argument("--cost", type=float, default=None)
    hb.add_argument("--log-path", default=None)

    fi = sub.add_parser("finish", help="close a record to a terminal status")
    _common(fi)
    fi.add_argument("--status", default="ended")
    fi.add_argument("--tokens", type=int, default=None)
    fi.add_argument("--cost", type=float, default=None)
    fi.add_argument("--detail", default=None)

    pr = sub.add_parser("prune", help="remove terminal records past retention")
    pr.add_argument("--env-root", required=True)
    pr.add_argument("--retain-s", type=int, default=86400)

    ss = sub.add_parser("span-start", help="open a span on a run")
    _common(ss)
    ss.add_argument("--name", required=True)
    ss.add_argument("--kind", default="internal")
    ss.add_argument("--parent-span-id", default=None)

    se = sub.add_parser("span-end", help="close a span")
    _common(se)
    se.add_argument("--span-id", required=True)
    se.add_argument("--status", default="ok")
    se.add_argument("--in-tokens", type=int, default=0)
    se.add_argument("--out-tokens", type=int, default=0)
    se.add_argument("--cost", type=float, default=None)
    se.add_argument("--error", default=None)

    ap_ = sub.add_parser("approve", help="mark a run awaiting human approval")
    _common(ap_)
    ap_.add_argument("--kind", default="gate")
    ap_.add_argument("--prompt", default="")
    ap_.add_argument("--resume-token", default=None)

    rs = sub.add_parser("resolve", help="resolve a pending approval")
    _common(rs)
    rs.add_argument("--decision", required=True)

    args = p.parse_args(argv)
    if not args.cmd:
        p.print_help(sys.stderr)
        return 2

    try:
        if args.cmd == "start":
            item_ids = [s for s in args.item_ids.split(",") if s.strip()]
            start_run(args.env_root, id=args.id, surface=args.surface, title=args.title,
                      item_ids=item_ids, repo=args.repo, pid=args.pid,
                      log_path=args.log_path, worktree=args.worktree,
                      detail=(args.detail or ""))
        elif args.cmd == "heartbeat":
            up = {}
            if args.detail is not None:
                up["detail"] = args.detail
            if args.tokens is not None:
                up["tokens"] = args.tokens
            if args.cost is not None:
                up["cost_usd"] = args.cost
            if args.log_path is not None:
                up["log_path"] = args.log_path
            heartbeat(args.env_root, args.id, **up)
        elif args.cmd == "finish":
            up = {}
            if args.tokens is not None:
                up["tokens"] = args.tokens
            if args.cost is not None:
                up["cost_usd"] = args.cost
            if args.detail is not None:
                up["detail"] = args.detail
            finish_run(args.env_root, args.id, args.status, **up)
        elif args.cmd == "prune":
            prune(args.env_root, retain_s=args.retain_s)
        elif args.cmd == "span-start":
            sid = start_span(args.env_root, args.id, name=args.name, kind=args.kind,
                             parent_span_id=args.parent_span_id)
            if sid:
                print(sid)
        elif args.cmd == "span-end":
            end_span(args.env_root, args.id, args.span_id, status=args.status,
                     input_tokens=args.in_tokens, output_tokens=args.out_tokens,
                     cost=args.cost, error=args.error)
        elif args.cmd == "approve":
            request_approval(args.env_root, args.id, kind=args.kind, prompt=args.prompt,
                             resume_token=args.resume_token)
        elif args.cmd == "resolve":
            resolve_approval(args.env_root, args.id, decision=args.decision)
    except Exception:
        return 0  # best-effort: a runtime hiccup must never break the runner
    return 0


if __name__ == "__main__":
    sys.exit(main())
