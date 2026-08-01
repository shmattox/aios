# Activity View — Live Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dashboard view that shows what is running right now across every agent surface (factory drains, nightly pipeline, interactive sessions, `/goal`, Workflows) with live-updating logs and running stats.

**Architecture:** One run-record contract (`engine/tools/activity.py`) that every producer writes to `state/activity/`. Two read-only routes on the existing dashboard server surface the records + tail their logs; the existing notify-then-refetch SSE channel gains an `activity` fingerprint so the open view refreshes on change. Producers are instrumented one at a time against the fixed contract, ending with the factory's guard-layer `Popen`/tee change.

**Tech Stack:** Python 3 stdlib only (no new deps, no node); the existing `ThreadingHTTPServer` dashboard; Preact+HTM vendored ESM for the UI; pytest.

## Global Constraints

- **Zero new runtime dependencies.** Stdlib Python + the already-vendored Preact/HTM only. No node anywhere.
- **Emit is best-effort and never load-bearing.** Every `activity.py` call from a producer is wrapped so it can never raise into the producer's real work (factory exit-0 collector contract + pipeline stage integrity are sacred).
- **Security unchanged:** 127.0.0.1 bind, exact Host validation, per-start token, zero write logic in the server. Log tailing is read-only; the run id must match `SAFE_ID` and resolve to a known record whose `log_path` is inside `state/activity/logs/` (or a validated transcript path) — no arbitrary paths, no `..` traversal.
- **`state/activity/` is machine-local and gitignored** (live PIDs/worktree paths are meaningless on the other machine).
- **Liveness is computed, never asserted:** live = `status=="running"` AND (pid alive OR heartbeat fresh within `LIVE_WINDOW_S`). A `running` record failing both renders as `stale`, never dropped.
- **Cross-platform:** Windows-primary env; all pid/time/path code must run on Windows + macOS + Linux.
- **Tests:** run from `Projects/aios` with `python -m pytest engine/tools/tests -q` (pathless `python -m pytest -q` also fine). New pytest files use `def test_*` (import-safe; no module-level `sys.exit`).
- **Ship discipline:** version bump on the engine merge; both-scope plugin reinstall. The factory task (Task 6) edits a guard-layer file — human guard-bless + review-gate PASS required, NOT autonomously drainable.

---

## File Structure

- `engine/tools/activity.py` **(create)** — the run-record contract: schema, `start_run`/`heartbeat`/`finish_run`/`prune`, `is_live`, `_pid_alive`, self-contained `_atomic_write`/`_read_json`. The single module every producer imports.
- `engine/tools/tests/test_activity.py` **(create)** — contract unit tests (frozen clock, fake pids).
- `engine/dashboard/dashboard_server.py` **(modify)** — add `/api/activity` + `/api/activity/<id>/log`; add `activity` to the SSE fingerprint.
- `engine/tools/tests/test_a63_dashboard_api.py` **(modify)** — route tests incl. traversal rejection.
- `engine/dashboard/ui/views/activity.js` **(create)** — the Activity view (list + detail with live log tail).
- `engine/dashboard/ui/app.js` **(modify)** — register the nav entry + route to the view; subscribe the view to the SSE `activity` change.
- `engine/dashboard/ui/tokens.css` **(modify, if needed)** — any run-status chip colors not already present.
- `engine/tools/pipeline_run.py` or the pipeline entrypoint **(modify)** — pipeline adapter (Task 5; exact file confirmed in that task).
- `~/.claude/` session hooks + `Scripts/factory-gate/factory_gate.py` **(modify)** — session + factory adapters (Tasks 4/6).
- `.gitignore` (env root + aios repo as appropriate) **(modify)** — ignore `state/activity/`.

**Build order rationale:** contract → server → view proves the whole rig against synthetic records first; then pipeline + session adapters feed it real data; the **factory adapter lands last** because it is the guard-layer, human-gated change and is safest to introduce into an already-working, already-tested system.

---

## Task 1: The run-record contract (`activity.py`)

**Files:**
- Create: `engine/tools/activity.py`
- Test: `engine/tools/tests/test_activity.py`

**Interfaces:**
- Consumes: nothing (foundation).
- Produces:
  - `ACTIVITY_DIR(env_root) -> Path` (= `<env>/state/activity`), `LOGS_DIR(env_root) -> Path` (= `<env>/state/activity/logs`)
  - `start_run(env_root, *, id, surface, title, item_ids=(), repo=None, pid=None, log_path=None, worktree=None, now=None) -> dict`
  - `heartbeat(env_root, id, *, now=None, **updates) -> None`
  - `finish_run(env_root, id, status, *, now=None, **updates) -> None`
  - `prune(env_root, *, retain_s=86400, now=None) -> int` (returns count removed)
  - `read_all(env_root) -> list[dict]` (every record, each with computed `live` bool + `age_s`)
  - `is_live(rec, now) -> bool`
  - `_pid_alive(pid) -> bool`
  - Constants: `LIVE_WINDOW_S = 90`, `SURFACES = ("factory","pipeline","session","goal","workflow")`, `TERMINAL = ("shipped","parked","no-op","failed","ended")`

- [ ] **Step 1: Write the failing test for start/read round-trip**

```python
# engine/tools/tests/test_activity.py
import os, time
from pathlib import Path
import pytest, sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # engine/tools on path
import activity


def test_start_run_writes_readable_record(tmp_path):
    activity.start_run(tmp_path, id="factory-A1-100", surface="factory",
                       title="Draining A1", item_ids=["A1"], pid=os.getpid(),
                       log_path=str(tmp_path / "state/activity/logs/factory-A1-100.log"),
                       now=1000.0)
    recs = activity.read_all(tmp_path)
    assert len(recs) == 1
    r = recs[0]
    assert r["id"] == "factory-A1-100"
    assert r["surface"] == "factory"
    assert r["status"] == "running"
    assert r["item_ids"] == ["A1"]
    assert r["started"] == 1000.0 and r["heartbeat"] == 1000.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Projects/aios && python -m pytest engine/tools/tests/test_activity.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'activity'`.

- [ ] **Step 3: Write the module core (schema + writers + reader)**

```python
# engine/tools/activity.py
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
```

- [ ] **Step 4: Run the round-trip test to verify it passes**

Run: `cd Projects/aios && python -m pytest engine/tools/tests/test_activity.py -q`
Expected: PASS.

- [ ] **Step 5: Add failing tests for liveness, pid check, finish, and prune**

```python
def test_is_live_pid_alive_current_process(tmp_path):
    r = activity.start_run(tmp_path, id="s-1", surface="session", title="sess",
                           pid=os.getpid(), now=0.0)
    # heartbeat cold (now is far ahead) but pid alive -> still live
    assert activity.is_live(r, now=10_000.0) is True


def test_is_live_stale_when_pid_dead_and_heartbeat_cold(tmp_path):
    r = activity.start_run(tmp_path, id="s-2", surface="session", title="sess",
                           pid=2_000_000_000, now=0.0)  # pid that cannot exist
    assert activity._pid_alive(2_000_000_000) is False
    assert activity.is_live(r, now=10_000.0) is False


def test_finish_run_sets_terminal_and_not_live(tmp_path):
    activity.start_run(tmp_path, id="f-1", surface="factory", title="d", pid=os.getpid(), now=0.0)
    activity.finish_run(tmp_path, "f-1", "shipped", tokens=1234, cost_usd=0.5, now=5.0)
    r = activity.read_all(tmp_path)[0]
    assert r["status"] == "shipped" and r["ended"] == 5.0 and r["tokens"] == 1234
    assert activity.is_live(r, now=6.0) is False  # terminal is never live


def test_prune_removes_terminal_past_retention_only(tmp_path):
    activity.start_run(tmp_path, id="old", surface="factory", title="d", now=0.0)
    activity.finish_run(tmp_path, "old", "shipped", now=0.0)
    activity.start_run(tmp_path, id="live", surface="factory", title="d", pid=os.getpid(), now=0.0)
    removed = activity.prune(tmp_path, retain_s=100, now=1000.0)  # old ended long ago
    ids = {r["id"] for r in activity.read_all(tmp_path)}
    assert removed == 1 and ids == {"live"}
```

- [ ] **Step 6: Run to verify the new tests fail**

Run: `cd Projects/aios && python -m pytest engine/tools/tests/test_activity.py -q`
Expected: FAIL — `is_live`, `_pid_alive`, `prune` not defined.

- [ ] **Step 7: Implement liveness, cross-platform pid check, and prune**

```python
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
```

- [ ] **Step 8: Run the full contract suite to verify green**

Run: `cd Projects/aios && python -m pytest engine/tools/tests/test_activity.py -q`
Expected: PASS (all tests).

- [ ] **Step 9: Commit**

```bash
git add engine/tools/activity.py engine/tools/tests/test_activity.py
git commit -m "feat(activity): run-record contract for live-run observability"
```

---

## Task 2: Server routes — `/api/activity` and the log tail

**Files:**
- Modify: `engine/dashboard/dashboard_server.py` (add routes in `do_GET`'s `/api/` block; add `activity` to `_events` fingerprint; import `activity`)
- Test: `engine/tools/tests/test_a63_dashboard_api.py`

**Interfaces:**
- Consumes: `activity.read_all(env_root)`, `activity.LOGS_DIR`, `activity.ACTIVITY_DIR` (Task 1); server's `_send_json`, `_deny`, `SAFE_ID`, `env_root`.
- Produces:
  - `GET /api/activity` → `{"runs": [<record with live+age_s>, ...], "_now": <epoch>}`
  - `GET /api/activity/<id>/log?tail=N` → `{"id": id, "lines": [...], "eof": bool, "available": bool}` (last `N` lines, default 200, cap 1000).

- [ ] **Step 1: Write failing route tests**

```python
# append to test_a63_dashboard_api.py
import os
import activity  # engine/tools already on sys.path via the file's parents[2] insert? add if needed

def _get(server, path):
    port = server.server_address[1]
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 headers={"Host": f"127.0.0.1:{port}"})
    with urllib.request.urlopen(req) as r:
        return r.status, json.loads(r.read().decode())


def test_api_activity_lists_runs_with_liveness(server, env_root):
    import sys as _s
    _s.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import activity
    activity.start_run(env_root, id="factory-A1-1", surface="factory",
                       title="Draining A1", item_ids=["A1"], pid=os.getpid())
    status, body = _get(server, "/api/activity")
    assert status == 200
    ids = {r["id"] for r in body["runs"]}
    assert "factory-A1-1" in ids
    run = next(r for r in body["runs"] if r["id"] == "factory-A1-1")
    assert run["live"] is True and run["surface"] == "factory"


def test_api_activity_log_tail_returns_last_lines(server, env_root):
    import sys as _s
    _s.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import activity
    logs = activity.LOGS_DIR(env_root)
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "factory-A1-1.log").write_text("l1\nl2\nl3\n", encoding="utf-8")
    activity.start_run(env_root, id="factory-A1-1", surface="factory", title="d",
                       log_path=str(logs / "factory-A1-1.log"))
    status, body = _get(server, "/api/activity/factory-A1-1/log?tail=2")
    assert status == 200 and body["available"] is True
    assert body["lines"] == ["l2", "l3"]


def test_api_activity_log_rejects_traversal_and_unknown(server, env_root):
    port = server.server_address[1]
    # unknown id -> available False, not a file read
    _, body = _get(server, "/api/activity/does-not-exist/log")
    assert body["available"] is False
    # traversal attempt -> 400/403, never a file outside logs/
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/activity/..%2f..%2fsecret/log",
        headers={"Host": f"127.0.0.1:{port}"})
    try:
        code = urllib.request.urlopen(req).status
    except urllib.error.HTTPError as e:
        code = e.code
    assert code in (400, 403, 404)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd Projects/aios && python -m pytest engine/tools/tests/test_a63_dashboard_api.py -k activity -q`
Expected: FAIL — routes 404 / keys missing.

- [ ] **Step 3: Add the import and routes to the server**

At the top of `dashboard_server.py`, alongside the other tool imports, add the tools dir to `sys.path` (mirror the existing `parents` insert pattern already used for dashboard imports) and `import activity`.

In `do_GET`, inside the existing `if route.startswith("/api/"):` region (before the generic fall-through), add:

```python
        if route == "/api/activity":
            runs = activity.read_all(self.server.env_root)
            return self._send_json({"runs": runs, "_now": time.time()})
        if route.startswith("/api/activity/") and route.endswith("/log"):
            rid = route[len("/api/activity/"):-len("/log")]
            return self._activity_log(rid)
```

Add the handler method on `Handler`:

```python
    def _activity_log(self, rid):
        # id must be a known record; never read an arbitrary path.
        if not SAFE_ID.match(rid or ""):
            return self._deny(400, "bad run id")
        env = self.server.env_root
        rec = None
        for r in activity.read_all(env):
            if r["id"] == rid:
                rec = r
                break
        if not rec or not rec.get("log_path"):
            return self._send_json({"id": rid, "lines": [], "eof": True, "available": False})
        lp = Path(rec["log_path"]).resolve()
        logs = activity.LOGS_DIR(env).resolve()
        # allow logs/ tee files; allow a validated transcript path only if it exists as a file.
        under_logs = str(lp).startswith(str(logs) + os.sep)
        if not (under_logs or lp.is_file()):
            return self._send_json({"id": rid, "lines": [], "eof": True, "available": False})
        try:
            tail = min(1000, max(1, int(urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query).get("tail", ["200"])[0])))
        except ValueError:
            tail = 200
        try:
            with open(lp, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            return self._send_json({"id": rid, "lines": [], "eof": True, "available": False})
        return self._send_json({"id": rid, "lines": lines[-tail:], "eof": True,
                                "available": True})
```

(Traversal like `..%2f..%2fsecret` fails `SAFE_ID.match` — `/` and `%` are not in the class — so it returns 400 before any file access.)

- [ ] **Step 4: Run route tests to verify pass**

Run: `cd Projects/aios && python -m pytest engine/tools/tests/test_a63_dashboard_api.py -k activity -q`
Expected: PASS.

- [ ] **Step 5: Add `activity` to the SSE fingerprint**

In `_events`'s `fingerprint()` (and the identical block used by `/api/mtimes` if present), add a cheap signal that changes whenever any record changes — the newest record mtime plus the count:

```python
            act_dir = env / "state" / "activity"
            act_files = sorted(act_dir.glob("*.json")) if act_dir.exists() else []
            fp["activity"] = (len(act_files),
                              max((_mtime(p) for p in act_files), default=None))
```

- [ ] **Step 6: Add a failing test that the SSE change list includes `activity`**

```python
def test_events_fingerprint_reacts_to_activity(server, env_root):
    # open the SSE stream, then write a record, expect a 'change' mentioning activity
    import sys as _s
    _s.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import activity
    port = server.server_address[1]
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/events",
                                 headers={"Host": f"127.0.0.1:{port}"})
    stream = urllib.request.urlopen(req, timeout=5)
    stream.readline()  # 'event: hello'
    stream.readline()  # 'data: {}'
    stream.readline()  # blank
    activity.start_run(env_root, id="factory-A9-9", surface="factory", title="d", pid=os.getpid())
    seen = ""
    for _ in range(40):  # ~ up to SSE_POLL_S * a few
        line = stream.readline().decode()
        seen += line
        if "activity" in seen:
            break
    stream.close()
    assert "activity" in seen
```

- [ ] **Step 7: Run and confirm green (route + SSE)**

Run: `cd Projects/aios && python -m pytest engine/tools/tests/test_a63_dashboard_api.py -k activity -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add engine/dashboard/dashboard_server.py engine/tools/tests/test_a63_dashboard_api.py
git commit -m "feat(dashboard): /api/activity + log tail route, activity SSE signal"
```

---

## Task 3: The Activity view (UI)

**Files:**
- Create: `engine/dashboard/ui/views/activity.js`
- Modify: `engine/dashboard/ui/app.js` (nav entry + route + SSE subscription)
- Modify: `engine/dashboard/ui/tokens.css` (status chip colors, only if missing)
- Test: extend the dashboard UI smoke test that already loads the page (follow the existing `test_a63_dashboard_server.py` pattern).

**Interfaces:**
- Consumes: `GET /api/activity`, `GET /api/activity/<id>/log?tail=N`, the app's existing SSE `change` subscription and view-registration mechanism (mirror `views/board.js`).
- Produces: an `Activity` view module exporting the same shape `board.js` exports (a Preact component + a nav registration), reusing the A109 card-anatomy detail-home pattern.

- [ ] **Step 1: Read the existing view + nav pattern**

Read `engine/dashboard/ui/views/board.js` and the nav/routing + SSE-subscription section of `engine/dashboard/ui/app.js`. Match their import style (vendored `preact`/`htm`), their fetch helper, and how a view subscribes to the SSE `change` event. Do not introduce a new state or fetch mechanism.

- [ ] **Step 2: Write `views/activity.js` — the list**

Render, from `GET /api/activity`:
- a live strip: `N running: X factory · Y session · Z pipeline …` (count `runs.filter(r => r.live)` by surface);
- one card per run (live first, then recently-finished), each showing surface icon, `title`, `item_ids`, live-ticking elapsed derived from `started` vs `_now` + a client clock, a status chip (`running`/`stale`/`shipped`/`parked`/`failed` — `stale` = `status=="running" && !live`), `tokens`/`cost_usd`, and a health dot (`live` truthy).

Refetch `/api/activity` on the app's SSE `change` event when `changed` includes `"activity"` (and on an interval fallback of 5s for the elapsed clock).

- [ ] **Step 3: Write the detail pane — live log tail**

On card click, open the detail home (reuse the A109 card component's right-pane/modal/accordion host). Poll `GET /api/activity/<id>/log?tail=400` on the same SSE `activity` change signal; render lines in an auto-scrolling monospace pane; when `available` is false, render "log unavailable". Show the run's stats + drill-down links (worktree path, backlog item via the existing ref renderer, governing doc). A `stale` run shows "last seen <heartbeat>" + a dismiss button that simply hides it client-side (no server delete — `prune` owns deletion).

- [ ] **Step 4: Register the view in `app.js`**

Add an `Activity` nav entry (live indicator: show the running count as a badge) and route to the view, mirroring how `Board`/`Inbox` are registered. Subscribe the view to the existing SSE `change` handler.

- [ ] **Step 5: Smoke-test the page serves and mounts**

Extend the existing server-smoke test to assert `GET /` includes the activity view asset and `GET /api/activity` returns `{"runs": [...]}`. (No headless-browser dependency; follow the current smoke pattern which checks served HTML + API, per A63.)

Run: `cd Projects/aios && python -m pytest engine/tools/tests/test_a63_dashboard_server.py -q`
Expected: PASS.

- [ ] **Step 6: Manual visual check**

Start the server against the real env (`python engine/dashboard/dashboard_server.py --port 0 --open` or the project's launch skill), write a synthetic record with `python -c "import activity; activity.start_run('.', id='demo-1', surface='pipeline', title='demo', pid=<this>)"`, and confirm the Activity view shows it live and the log pane behaves. Capture nothing to the repo.

- [ ] **Step 7: Commit**

```bash
git add engine/dashboard/ui/views/activity.js engine/dashboard/ui/app.js engine/dashboard/ui/tokens.css engine/tools/tests/test_a63_dashboard_server.py
git commit -m "feat(dashboard): Activity view — live run cards + log tail"
```

---

## Task 4: Session adapter (hooks)

**Files:**
- Modify: the SessionStart / SessionEnd / Stop hook scripts that already write `state/factory/*.session-lock` (locate via `grep -rn "session-lock" ~/.claude Scripts` — likely `Scripts/env-auto-sync` or a `~/.claude` hook). Confirm the exact writer in Step 1.
- Test: `engine/tools/tests/test_activity.py` (adapter-shape unit test — the hook shells out to a tiny helper we can call directly).

**Interfaces:**
- Consumes: `activity.start_run/heartbeat/finish_run`.
- Produces: a session record `id = f"session-{slug}-{pid}"`, `surface="session"`, `log_path` = the session transcript jsonl path when derivable, `title` = cwd-derived label.

- [ ] **Step 1: Find the session-lock writer**

Run: `grep -rn "session-lock" ~/.claude Scripts/ | grep -iv test`
Identify the SessionStart writer (creates `<slug>.session-lock` with the pid) and the SessionEnd remover. That is where the adapter attaches.

- [ ] **Step 2: Add a helper + failing test**

Add `session_run_id(slug, pid) -> str` to `activity.py` returning `f"session-{slug}-{pid}"` (slug sanitized to `SAFE_ID_CHARS`), and test it:

```python
def test_session_run_id_is_safe(tmp_path):
    rid = activity.session_run_id("open place/dev", 12648)
    assert activity._safe_id(rid) and rid.endswith("-12648")
```

Run: `cd Projects/aios && python -m pytest engine/tools/tests/test_activity.py -k session_run_id -q` → FAIL, then implement, then PASS.

- [ ] **Step 3: Wire the hooks (best-effort)**

In the SessionStart writer, after writing the `.session-lock`, call (guarded so a failure never blocks the session):

```python
try:
    import activity
    rid = activity.session_run_id(slug, pid)
    activity.start_run(env_root, id=rid, surface="session",
                       title=f"Session ({cwd_label})", pid=pid, repo=slug,
                       log_path=transcript_path_or_none)
except Exception:
    pass
```

In the throttled Stop hook: `activity.heartbeat(env_root, rid)`. In SessionEnd: `activity.finish_run(env_root, rid, "ended")`. All three wrapped in `try/except Exception: pass`.

- [ ] **Step 4: Verify a live session appears**

Run the dashboard, open a second interactive session, confirm a `session-*` card appears live and clears on that session's end. (Manual — hooks fire outside pytest.)

- [ ] **Step 5: Commit**

```bash
git add <hook files> engine/tools/activity.py engine/tools/tests/test_activity.py
git commit -m "feat(activity): session adapter — live session records via existing hooks"
```

---

## Task 5: Pipeline adapter

**Files:**
- Modify: the nightly pipeline entrypoint (confirm in Step 1 — the `aios:pipeline` skill runner; likely `engine/tools/pipeline_run.py` or the stage-orchestrator invoked by the scheduled task).
- Test: `engine/tools/tests/test_activity.py` (a stage-wrap unit test around a fake stage sequence).

**Interfaces:**
- Consumes: `activity.start_run/heartbeat/finish_run`.
- Produces: `id = f"pipeline-{date}"`, `surface="pipeline"`, `detail` = current stage; `finish_run(..., "ended")` on completion.

- [ ] **Step 1: Locate the pipeline entrypoint**

Run: `grep -rn "capture\|sort\|ingest\|gate" engine/tools/*.py | grep -i "def main\|stages\|pipeline"` and read the `aios:pipeline` SKILL to find the ordered stage runner.

- [ ] **Step 2: Add a failing wrap test**

```python
def test_run_with_activity_records_stages(tmp_path):
    seen = []
    def stage(name):
        seen.append(name)
    ok = activity.run_with_activity(
        tmp_path, id="pipeline-2026-08-01", title="Nightly pipeline",
        stages=[("capture", lambda: stage("capture")),
                ("sort", lambda: stage("sort"))], now=0.0)
    r = activity.read_all(tmp_path)[0]
    assert ok and seen == ["capture", "sort"]
    assert r["status"] == "ended" and r["surface"] == "pipeline"
```

- [ ] **Step 3: Implement `run_with_activity` in `activity.py`**

```python
def run_with_activity(env_root, *, id, title, stages, now=None):
    """Run an ordered [(name, callable)] sequence as a 'pipeline' run, heartbeating the
    current stage. Best-effort recording; the stage callables' own exceptions propagate
    (the pipeline owns its error handling), but a finish_run(failed) is written first."""
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
```

- [ ] **Step 4: Run the test → PASS**

Run: `cd Projects/aios && python -m pytest engine/tools/tests/test_activity.py -k run_with_activity -q`

- [ ] **Step 5: Call it from the pipeline entrypoint**

Wrap the existing stage sequence in `run_with_activity` (guarded import; if `activity` import fails the pipeline still runs unchanged — best-effort).

- [ ] **Step 6: Commit**

```bash
git add engine/tools/activity.py engine/tools/tests/test_activity.py <pipeline entrypoint>
git commit -m "feat(activity): pipeline adapter — per-stage live run record"
```

---

## Task 6: Factory adapter — Popen tee (GUARD-LAYER; human-gated)

> **STOP:** `Scripts/factory-gate/factory_gate.py` is in `guard_freeze.GUARD_PATHS` (H91). This task is **not autonomously drainable**. It requires Seth's guard-bless of the change and a review-gate PASS before merge. Do not merge on green tests alone.

**Files:**
- Modify: `Scripts/factory-gate/factory_gate.py` (`_launch_drain`; the `_refresh_lock_periodically` watchdog gains a heartbeat)
- Test: `Scripts/factory-gate/tests/test_factory_worktree.py`

**Interfaces:**
- Consumes: aios `activity` (import via the same `sys.path` insert the factory already uses for `cg`); the existing `_parse_output_tokens`, `_drain_result_health`, `_merge_back`.
- Produces: a `factory-<slug>` run record + a tee'd `state/activity/logs/factory-<slug>.log`, streaming while the drain runs; token/merge behavior byte-identical to today.

- [ ] **Step 1: Add the import (guarded)**

Near the top, after the `cg` import, add a guarded `activity` import so the factory never fails to launch if aios is absent:

```python
try:
    import activity  # aios engine/tools already on sys.path for cg
except Exception:
    activity = None
```

- [ ] **Step 2: Write a failing test that a drain writes a record + streamed log**

In `test_factory_worktree.py`, stub the drain subprocess with a fake `claude` that emits a few stdout lines slowly, then run `_launch_drain` and assert: (a) a `factory-*` record exists mid-run with `status=="running"` and its `.log` grows; (b) after completion the record is terminal and `tokens` matches `_parse_output_tokens` of the same stdout. Follow the file's existing subprocess-stub pattern (it already fakes git + drain).

```python
def test_launch_drain_streams_log_and_records(tmp_factory_repo, monkeypatch, tmp_path):
    # arrange env_root = tmp_path; point activity at it; stub _drain_command to a python
    # one-liner that prints 3 lines with flushes; run _launch_drain in a thread; poll the
    # record + log; join; assert terminal record + tokens parsed from the captured buffer.
    ...
```

(Write the concrete stub mirroring the existing `_drain_command`/`subprocess` monkeypatch in this test file — reuse its fixtures rather than inventing new ones.)

- [ ] **Step 3: Run → FAIL (no record written yet)**

Run: `cd Scripts/factory-gate && python -m pytest tests/test_factory_worktree.py -k stream -q`

- [ ] **Step 4: Replace the blocking run with Popen + tee**

In `_launch_drain`, replace the `subprocess.run(_drain_command(goal), cwd=path, capture_output=True, text=True, errors="replace", **_NOWIN)` call with a `Popen` that tees each stdout line to the log file AND a buffer, then reconstruct `r.stdout`/`r.returncode` for the unchanged downstream logic:

```python
        env_root = _env_root_for(repo)  # existing helper that locates state/ (or derive as cg does)
        rid = f"factory-{slug}"
        log_path = None
        if activity:
            log_path = str(activity.LOGS_DIR(env_root) / f"{rid}.log")
            activity.start_run(env_root, id=rid, surface="factory",
                               title=f"Draining {', '.join(it['id'] or '?' for it in items)}",
                               item_ids=[it["id"] for it in items if it["id"]],
                               repo=os.path.basename(repo), pid=None, log_path=log_path,
                               worktree=path)
        buf = []
        proc = subprocess.Popen(_drain_command(goal), cwd=path, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors="replace", **_NOWIN)
        if activity:
            activity.heartbeat(env_root, rid, pid=proc.pid)
        logfh = None
        try:
            if log_path:
                os.makedirs(os.path.dirname(log_path), exist_ok=True)
                logfh = open(log_path, "w", encoding="utf-8")
            for line in proc.stdout:            # blocks per line; drains the pipe (no deadlock)
                buf.append(line)
                if logfh:
                    logfh.write(line); logfh.flush()
                if activity and len(buf) % 20 == 0:
                    activity.heartbeat(env_root, rid, detail=line.rstrip()[:200])
        finally:
            if logfh:
                logfh.close()
        rc = proc.wait()
        stdout = "".join(buf)
        # ---- downstream is UNCHANGED, now fed from `stdout`/`rc` instead of `r` ----
        if rc != 0:
            _append_log(log_file, f"  ⚠ drain {slug} exited {rc} — see {log_path or 'log'}")
        tok, cost = _parse_output_tokens(stdout, nominal_tokens)
        healthy, hdetail = _drain_result_health(stdout)
        ...
```

Then, where the outcome is known, before `return`, add (guarded):

```python
        if activity:
            activity.finish_run(env_root, rid,
                                {"shipped": "shipped", "no-op": "no-op"}.get(outcome, "parked"
                                if outcome == "parked" else "failed"),
                                tokens=tok, cost_usd=cost, detail=detail)
```

Keep every existing `_append_log`, `_merge_back`, MERGE_LOCK, worktree cleanup, and return path exactly as before. `stderr` is folded into stdout via `STDOUT` so the health/stderr tail logic still sees it; if the current code inspects `r.stderr` separately, preserve that by capturing stderr to its own pipe instead and teeing both — confirm against the actual lines and keep behavior identical.

- [ ] **Step 5: Run the factory suite → PASS**

Run: `cd Scripts/factory-gate && python -m pytest tests/ -q`
Expected: PASS — new streaming test green AND all pre-existing tests still green (token accounting + merge-back unchanged).

- [ ] **Step 6: Also confirm the aios suite is unaffected**

Run: `cd Projects/aios && python -m pytest engine/tools/tests -q`
Expected: PASS.

- [ ] **Step 7: STOP for the human gate**

Present the diff for Seth's guard-bless (`guard_freeze`) and run the `review-gate` Workflow on the branch diff. Do **not** merge until both clear.

- [ ] **Step 8: Commit (after bless + review-gate PASS)**

```bash
git add Scripts/factory-gate/factory_gate.py Scripts/factory-gate/tests/test_factory_worktree.py
git commit -m "feat(factory): stream drain output live via Popen tee + activity record"
```

---

## Task 7: Wiring, gitignore, prune-on-start, ship

**Files:**
- Modify: `.gitignore` (env root + aios repo) — add `state/activity/`.
- Modify: `engine/dashboard/dashboard_server.py` — call `activity.prune(env_root)` once at server start (guarded).
- Modify: `Projects/aios` plugin version + reinstall.

- [ ] **Step 1: Gitignore `state/activity/`**

Add `state/activity/` to the env-root `.gitignore` (and confirm the aios repo doesn't track it). Verify: `git status --porcelain state/activity` prints nothing after a run.

- [ ] **Step 2: Prune on server start**

In `make_server`/`run_server` startup (guarded): `try: activity.prune(env_root)\nexcept Exception: pass`. Add a test that a terminal record older than retention is gone after `make_server` is constructed (extend `test_a63_dashboard_api.py`).

- [ ] **Step 3: Full suites green**

Run: `cd Projects/aios && python -m pytest engine/tools/tests -q` and `cd Scripts/factory-gate && python -m pytest tests -q`. Both PASS.

- [ ] **Step 4: Version bump + reinstall**

Bump the aios plugin version (the repo's version file — same place A109/A116 bumped), then `/plugin update aios` at both scopes. Confirm the Activity view is live against the real env.

- [ ] **Step 5: Commit**

```bash
git add .gitignore engine/dashboard/dashboard_server.py <version file> engine/tools/tests/test_a63_dashboard_api.py
git commit -m "chore(activity): gitignore state/activity, prune on start, version bump"
```

---

## Self-Review

**Spec coverage:**
- Run-record contract → Task 1. ✓
- Liveness computed / stale-loud → Task 1 (`is_live`) + Task 3 (stale chip). ✓
- Best-effort emit → Task 1 (`_atomic_write` swallows) + guarded calls in Tasks 4/5/6. ✓
- Factory Popen/tee, token+merge unchanged, guard-layer gate → Task 6. ✓
- Pipeline adapter → Task 5. Session adapter → Task 4. /goal+Workflow coarse (session-level) → Task 4 (records the session; Workflow journal attach noted as `log_path` when derivable — deferred fine-grain per spec). ✓
- `/api/activity` + log tail + traversal rejection → Task 2. ✓
- SSE activity signal → Task 2. ✓
- Activity view (strip, cards, detail log tail, drill-down) → Task 3. ✓
- Retention/prune → Task 1 (`prune`) + Task 7 (prune-on-start). ✓
- `state/activity/` gitignored → Task 7. ✓
- Security unchanged → Task 2 (SAFE_ID + under-logs check). ✓
- Tests across contract/factory/server/UI → Tasks 1/2/3/6. ✓
- Version bump + both-scope reinstall → Task 7. ✓

**Placeholder scan:** Task 6 Step 2's test body and Task 3's JS are described rather than fully coded — intentional, because both must mirror existing in-repo patterns (the factory test's subprocess-stub fixtures; `views/board.js`) that the implementer reads first; the interfaces, assertions, and behavior are fully specified. All Python contract/server code is concrete.

**Type consistency:** `start_run`/`heartbeat`/`finish_run`/`prune`/`read_all`/`is_live`/`_pid_alive`/`run_with_activity`/`session_run_id` names + signatures are consistent across Tasks 1–7. Record keys (`id, surface, title, item_ids, repo, pid, started, heartbeat, ended, status, tokens, cost_usd, log_path, detail, worktree`, plus computed `live`/`age_s`) are used identically in the server and view.

**Open items deferred to execution (from the spec's open questions):** session heartbeat cadence (Task 4 uses the existing throttled Stop hook — confirm freshness vs `LIVE_WINDOW_S`); transcript-tail render depth (Task 3 renders raw lines first); the exact pipeline entrypoint + session-hook files (located in Tasks 5/4 Step 1).
