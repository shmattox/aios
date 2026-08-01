"""Run-record contract: the single home for 'what is running right now' across every
agent surface. One JSON file per run under state/activity/; live logs under
state/activity/logs/. Every write is best-effort and MUST NOT raise into a producer's
real work — callers wrap nothing; this module swallows its own I/O errors."""
import json, os, sys, time
from pathlib import Path

LIVE_WINDOW_S = 90
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


def _rec_path(env_root, rid):
    return str(ACTIVITY_DIR(env_root) / f"{rid}.json")


def start_run(env_root, *, id, surface, title, item_ids=(), repo=None, pid=None,
              log_path=None, worktree=None, now=None):
    if not _safe_id(id) or surface not in SURFACES:
        return None
    ts = _now(now)
    rec = {"id": id, "surface": surface, "title": title, "item_ids": list(item_ids),
           "repo": repo, "pid": pid, "started": ts, "heartbeat": ts, "ended": None,
           "status": "running", "tokens": 0, "cost_usd": 0.0,
           "log_path": log_path, "detail": "", "worktree": worktree}
    _atomic_write(_rec_path(env_root, id), rec)
    return rec


def heartbeat(env_root, id, *, now=None, **updates):
    rec = _read_json(_rec_path(env_root, id))
    if not rec:
        return
    rec["heartbeat"] = _now(now)
    for k in ("tokens", "cost_usd", "detail", "log_path", "worktree", "pid"):
        if k in updates:
            rec[k] = updates[k]
    _atomic_write(_rec_path(env_root, id), rec)


def finish_run(env_root, id, status, *, now=None, **updates):
    rec = _read_json(_rec_path(env_root, id))
    if not rec:
        return
    ts = _now(now)
    rec["status"] = status if status in TERMINAL else "ended"
    rec["ended"] = ts
    rec["heartbeat"] = ts
    for k in ("tokens", "cost_usd", "detail", "log_path", "worktree"):
        if k in updates:
            rec[k] = updates[k]
    _atomic_write(_rec_path(env_root, id), rec)


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
    return (now - rec.get("heartbeat", 0)) <= LIVE_WINDOW_S


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
            for p in (d / n, LOGS_DIR(env_root) / f"{rec['id']}.log"):
                try:
                    os.remove(p)
                except OSError:
                    pass
            removed += 1
    return removed
