#!/usr/bin/env python3
"""queue_tx.py - the ENFORCED state-commit wrapper for the aios queue.

The Stage Contract's atomic-write + validation guarantees, implemented ONCE in code so a stage
can't reintroduce the clobber/torn-write class by doing its own raw write. Stages compute their
change-set, then COMMIT through this helper. No deps beyond the stdlib.

STATE LAYOUT (A4, 2026-07-05 - single file; the G13 sharded layout is retired, see git history):
  <state>/queue.json        CANONICAL - the whole queue, one object {"queue": [...]}. Every write
                            is atomic (tmp + os.replace), so readers always see a complete queue.
                            Durable history/rollback is git (state/ is tracked) + rewind snapshots.
  <state>/queue.json.lock   short-lived advisory write lock - serializes concurrent stage writers
                            so a read-merge-write can't lose a concurrent stage's update.

Usage:
  python queue_tx.py validate <queue.json>
  python queue_tx.py add      <queue.json> <newitems.json>   # append NEW items (dedupe-fenced)
  python queue_tx.py update   <queue.json> <items.json>      # update EXISTING items (must exist)
  python queue_tx.py select   <queue.json> [--stage S] [--lane L] [--limit N]   # print a subset
  python queue_tx.py commit   <proposed.json> <live.json>    # whole-queue replace (bulk/operator)
  python queue_tx.py claim    <queue.json> <id1,id2,...> <worker>
  python queue_tx.py dump     <queue.json>                   # print the queue (debug)
  python queue_tx.py ls       <queue.json>                   # compact id/stage/lane listing
"""
import json, os, sys, time, re

STAGES   = {"captured", "sorted", "awaiting", "shipped", "reverted", "rejected", "reference"}
LANES    = {"auto-ship", "confirm", "review", None}
REQUIRED = {"id", "stage"}
KEYED_STAGES = {"sorted", "awaiting", "shipped"}   # conflict_key required from sort onward

LOCK_TIMEOUT_S = 30      # how long a writer waits for the lock before failing loud
LOCK_STALE_S   = 300     # a lock older than this is a crashed writer -> reclaim

# conflict_key shapes. The wiki shape <kb>/wiki/<type>/<slug...> is the ORIGINAL and stays MANDATORY
# for any item carrying a draft_path (a vault-wiki draft). A96/A69 relaxed the guard to admit two
# NON-wiki, DRAFTLESS key shapes (one change-set, two consumers): a proposed operational-Notion task
# `<kb>/notion/tasks/<slug>` (A96 proposal items) and an env-target lessons rule `env/lessons/<slug>`
# (A69 reflect Lessons). The regexes alone still admit a `..` inside a tail (dots are legal in slugs),
# so a separate segment-level `..` fence catches path traversal (`dev/wiki/../../etc/passwd.md`) that
# a regex would pass.
_CONFLICT_KEY_RE = re.compile(r"^[A-Za-z0-9._-]+/wiki/[A-Za-z0-9._/-]+$")          # wiki (draft_path)
_NOTION_TASK_KEY_RE = re.compile(r"^[A-Za-z0-9._-]+/notion/tasks/[A-Za-z0-9._-]+$")  # A96 proposals
_LESSONS_KEY_RE = re.compile(r"^env/lessons/[A-Za-z0-9._-]+$")                        # A69 lessons
_CONFLICT_KEY_SHAPES = (_CONFLICT_KEY_RE, _NOTION_TASK_KEY_RE, _LESSONS_KEY_RE)


def _has_dotdot(path):
    """True if any path segment (split on / or \\) is exactly '..' - a directory-traversal escape.
    A filename that merely CONTAINS dots (e.g. `cap-table-2026.md`, `foo..bar.md`) is not a `..`
    segment and is allowed."""
    return any(seg == ".." for seg in re.split(r"[\\/]", str(path)))


def _die(msg):
    print("FAIL:", msg)
    sys.exit(1)


# ─────────────────────────── validation (unchanged contract) ───────────────────────────

def validate(data):
    """Return None if the queue is well-formed, else an error string."""
    if not isinstance(data, dict) or not isinstance(data.get("queue"), list):
        return "top-level must be an object with a 'queue' array"
    seen = set()
    for n, it in enumerate(data["queue"]):
        if not isinstance(it, dict) or not REQUIRED <= set(it):
            return f"item {n} missing required {REQUIRED - set(it if isinstance(it, dict) else {})}"
        cid = it.get("id")
        if it["stage"] not in STAGES:
            return f"item {cid!r}: bad stage {it['stage']!r}"
        if it.get("lane") not in LANES:
            return f"item {cid!r}: bad lane {it.get('lane')!r}"
        if cid in seen:
            return f"duplicate id {cid!r}"
        seen.add(cid)
        if it["stage"] in KEYED_STAGES and not it.get("conflict_key"):
            return f"item {cid!r}: conflict_key required at stage {it['stage']!r}"
        # shape-validate the write targets (path-traversal fence). conflict_key, when present, must be
        # <kb>/wiki/<type>/<slug> with no `..` segment; payload_path, when present, must have no `..`
        # segment (absolute paths are a legitimate legacy/e2e shape - only traversal is rejected).
        ck2 = it.get("conflict_key")
        if ck2:
            if _has_dotdot(ck2):
                return f"item {cid!r}: conflict_key {ck2!r} has a '..' path segment"
            if not any(r.match(str(ck2)) for r in _CONFLICT_KEY_SHAPES):
                return (f"item {cid!r}: conflict_key {ck2!r} not of an accepted shape "
                        f"(<kb>/wiki/<type>/<slug>, <kb>/notion/tasks/<slug>, or env/lessons/<slug>)")
        pp = it.get("payload_path")
        if pp and _has_dotdot(pp):
            return f"item {cid!r}: payload_path {pp!r} has a '..' path segment"
        dp = it.get("draft_path")
        if dp:
            if _has_dotdot(dp):
                return f"item {cid!r}: draft_path {dp!r} has a '..' path segment"
            # a draft_path item is a vault-wiki draft — its conflict_key MUST be the wiki shape; the
            # relaxed notion/tasks + env/lessons shapes are for DRAFTLESS proposals/lessons only.
            if ck2 and not _CONFLICT_KEY_RE.match(str(ck2)):
                return (f"item {cid!r}: draft_path items require a <kb>/wiki/<type>/<slug> "
                        f"conflict_key, got {ck2!r}")
    return None


# ─────────────────────────── store (read / atomic write / lock) ───────────────────────────

def _read_json(path):
    """Parse a JSON file, or None if missing/unparseable."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_atomic(path, obj):
    """Write `obj` as JSON via tmp + os.replace, so readers only ever see a complete file.
    The short PermissionError retry is a Windows accommodation (a concurrent reader can briefly
    hold the destination open without FILE_SHARE_DELETE), not a data-integrity mechanism."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(3):
        try:
            os.replace(tmp, path)
            _fsync_dir(path)   # make the rename itself durable, not just the file contents
            return
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(0.2)


def _fsync_dir(path):
    """Best-effort fsync of the directory holding `path`, so a crash right after `os.replace`
    can't lose the rename (the file's data is fsync'd above, but the new dentry lives in the
    parent dir's own metadata). No-op where the platform can't fsync a directory - Windows has
    no directory fd to sync, and there durability is delegated to the FS/NTFS journal."""
    d = os.path.dirname(os.path.abspath(path))
    try:
        dfd = os.open(d, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)


class _Lock:
    """Advisory write lock (O_CREAT|O_EXCL lockfile). Serializes writers; readers don't lock
    (os.replace gives them a consistent snapshot). A lock older than LOCK_STALE_S is a crashed
    writer and is reclaimed."""

    def __init__(self, queue_path):
        self.path = queue_path + ".lock"

    def __enter__(self):
        deadline = time.time() + LOCK_TIMEOUT_S
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, json.dumps(
                    {"pid": os.getpid(),
                     "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}).encode("utf-8"))
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    looks_stale = time.time() - os.path.getmtime(self.path) > LOCK_STALE_S
                except OSError:
                    continue                   # lock vanished between checks - retry acquire
                # Only a lock that LOOKS crashed (>LOCK_STALE_S old) is a reclaim candidate; a
                # live lock is never touched. The authoritative staleness verdict is made INSIDE
                # _reclaim_if_stale, on the inode it atomically takes ownership of - not on the
                # path here - so a concurrent re-acquire can't turn our "stale" verdict into the
                # theft of a fresh lock (the residual TOCTOU a check-here-then-rename would leave).
                if looks_stale and self._reclaim_if_stale():
                    continue                   # a genuinely stale lock was cleared - retry acquire
                if time.time() > deadline:
                    _die(f"queue is locked by another writer ({self.path}) - "
                         f"retry after it finishes, or remove the lock if its owner crashed")
                time.sleep(0.25)

    def _reclaim_if_stale(self):
        """Atomically take ownership of the current lockfile (rename-to-unique), then decide
        staleness on the inode WE now solely own. Returns True iff a genuinely crashed lock was
        cleared (caller retries the O_EXCL acquire); False iff the lock turned out live or could
        not be taken (caller waits).

        Deciding on the owned inode - not on the path - closes the TOCTOU that a plain
        check-path-then-remove/rename leaves: two waiters both saw the same stale path, and the
        second reclaimed the FRESH lock the first had just re-created, reopening the 2026-06-11
        double-hold. Here, the rename is the atomic hand-off (only one waiter can move a given
        inode). If the inode we took is genuinely stale we discard it; if it was actually FRESH
        (a fast waiter re-acquired a live lock between our O_EXCL miss and our rename) we put it
        back untouched and wait - we never remove a lock we can't prove crashed.

        Residual (documented, not fixed - inherent to advisory file locks without an atomic
        compare-and-swap): while we restore a mistakenly-taken live lock, self.path is briefly
        empty, so a THIRD concurrent waiter could O_EXCL a new lock into that gap. That needs two
        precise preemptions inside the >5-minute-rare crashed-writer recovery path among
        trusted-local writers; the common 2-waiter reclaim is now provably single-winner."""
        reclaimed = "%s.reclaim.%d.%d" % (self.path, os.getpid(), time.time_ns())
        try:
            os.rename(self.path, reclaimed)    # atomic take: only one waiter can move this inode
        except FileNotFoundError:
            return True                        # already gone - real progress, retry acquire
        except OSError:
            return False                       # couldn't take ownership (perm/handle) - wait
        try:
            still_stale = time.time() - os.path.getmtime(reclaimed) > LOCK_STALE_S
        except OSError:
            still_stale = True                 # vanished under us - treat as reclaimable
        if still_stale:
            try:
                os.remove(reclaimed)           # genuine crashed writer - discard, re-acquire
            except OSError:
                pass
            return True
        # We took a LIVE lock by mistake - restore it untouched so its owner is unaffected, wait.
        try:
            os.rename(reclaimed, self.path)
        except OSError:
            try:
                os.remove(reclaimed)           # a new lock already holds the path - drop our copy
            except OSError:
                pass
        return False

    def __exit__(self, *exc):
        try:
            os.remove(self.path)
        except OSError:
            pass


def load(path):
    """Load + validate the queue. A missing file is a legitimately-empty fresh store; an
    unparseable or invalid file fails loud (recover via git history / rewind snapshots).
    A lingering legacy shard dir fences EVERY op fail-loud - defensive only: the G13 sharded
    layout was retired in A4 and this build ships no collapse path for it (the one-time
    `migrate` verb was removed in A51 as dead code)."""
    if os.path.isdir(path + ".d"):
        _die(f"legacy shard dir {path + '.d'} present - the sharded queue layout was retired in "
             f"A4 (single file only); this build has no collapse path for it. To fold a legacy "
             f"shard dir, install/downgrade to a pre-A4 tag that still ships `queue_tx.py migrate`, "
             f"run it there against {path}, then upgrade back to this build (reads/writes stay "
             f"fenced here until the shard dir is gone)")
    if not os.path.exists(path):
        return {"queue": []}
    data = _read_json(path)
    if data is None:
        _die(f"{path} did not parse - recover from git history (state/ is tracked) or a rewind snapshot")
    err = validate(data)
    if err:
        _die(f"{path} is invalid: {err}")
    return data


def _save(path, data):
    """Validated atomic write of the whole queue (caller holds the lock)."""
    err = validate(data)
    if err:
        _die(f"refusing to write invalid queue (live untouched): {err}")
    data["queue"].sort(key=lambda it: str(it.get("id")))
    _write_atomic(path, {"_comment": "aios queue - edit via queue_tx.py only.",
                         "queue": data["queue"]})


# ─────────────────────────── invariants ───────────────────────────

def _guard_awaiting_transition(old_by_id, it, mode):
    """Drafted-before-awaiting invariant: an item may only ENTER 'awaiting' carrying the
    vault-relative draft_path its ingest run wrote (or an explicit `draftless: true` for garden
    de-bloat/prune action-proposals, whose whole action lives in rec_reason). Already-awaiting
    items are grandfathered - the invariant holds on the transition, so a state-flip-only bulk
    advance is refused at write time instead of surfacing as gate "no draft found" rejections
    a day later."""
    old = old_by_id.get(it.get("id"))
    if it.get("stage") == "awaiting" and (old is None or old.get("stage") != "awaiting"):
        dp = it.get("draft_path")
        if not (isinstance(dp, str) and dp.strip()) and it.get("draftless") is not True:
            _die(f"{mode} refused (live untouched): item {it.get('id')!r} enters stage 'awaiting' "
                 f"with no draft_path - write the staging draft first and record its "
                 f"vault-relative path on the item (drafted-before-awaiting invariant), or "
                 f"declare `draftless: true` for an action-proposal whose whole action lives "
                 f"in rec_reason (garden de-bloat/prune)")


# ─────────────────────────── stage primitives ───────────────────────────

def _read_items(items_path):
    """Read a stage's change-set: a JSON list of items, or a {'queue':[...]} object."""
    obj = _read_json(items_path)
    if obj is None:
        _die(f"{items_path} did not parse (live untouched)")
    if isinstance(obj, dict) and isinstance(obj.get("queue"), list):
        return obj["queue"]
    if isinstance(obj, list):
        return obj
    _die(f"{items_path}: expected a JSON list of items or a {{'queue':[...]}} object")


def _apply_items(live_path, items, mode):
    """Merge `items` into the live queue (mode = add|update), validate the FULL set, write
    atomically. The lock serializes concurrent stages so no update is lost."""
    with _Lock(live_path):
        data = load(live_path)
        by_id = {it.get("id"): it for it in data["queue"]}
        # A107: an id paged out to the archive is STILL taken — otherwise archival would free an id
        # for silent reuse. The add fence consults the archive; update stays live-only (it mutates a
        # live item, and an archived item must be `unarchive`d before it can be updated). This fence
        # fails LOUD on a corrupt archive (via _load_archive) — deliberately stricter than the two
        # degrade-silent dedupe READERS, matching load()'s fail-loud discipline on the write path.
        arch_ids = archived_ids(live_path) if mode == "add" else set()
        for it in items:
            if not isinstance(it, dict) or "id" not in it:
                _die("each change-set item must be an object with an 'id'")
            cid = it["id"]
            exists = cid in by_id
            if mode == "add" and (exists or cid in arch_ids):
                where = "queue" if exists else "archive"
                _die(f"add refused: id {cid!r} already exists in the {where} (dedupe fence - "
                     f"use update to mutate a live item, or unarchive an archived one first)")
            if mode == "update" and not exists:
                _die(f"update refused: id {cid!r} does not exist (use add to append)")
            _guard_awaiting_transition(by_id, it, mode)
            by_id[cid] = it
        merged = {"queue": list(by_id.values())}
        err = validate(merged)
        if err:
            _die(f"change-set rejected (live untouched): {err}")
        _save(live_path, merged)
        total = len(merged["queue"])
    print(f"{mode}: {len(items)} item(s) applied; queue now {total} items")


def commit(proposed_path, live_path):
    """Authoritative WHOLE-queue replace (bulk/operator path only). Honors the same
    drafted-before-awaiting invariant as add/update - a whole-queue commit that flips items to
    'awaiting' without drafts was the exact primitive of the 2026-07-02 incident."""
    data = _read_json(proposed_path)
    if data is None:
        _die(f"proposed queue {proposed_path} did not parse (live untouched)")
    err = validate(data)
    if err:
        _die(f"proposed queue rejected (live untouched): {err}")
    with _Lock(live_path):
        old_by_id = {it.get("id"): it for it in load(live_path)["queue"]}
        for it in data["queue"]:
            _guard_awaiting_transition(old_by_id, it, "commit")
        _save(live_path, data)
    print(f"committed {len(data['queue'])} items -> {live_path}")


def select(live_path, stage=None, lane=None, limit=None):
    """Print the items matching stage/lane (a small subset) - a stage reads only what it works on."""
    data = load(live_path)
    out = [it for it in data["queue"]
           if (stage is None or it.get("stage") == stage)
           and (lane is None or it.get("lane") == lane)]
    if limit is not None:
        out = out[:int(limit)]
    print(json.dumps({"count": len(out), "queue": out}, indent=2, ensure_ascii=False))


# ─────────────────────────── claim (lease) ───────────────────────────

def _epoch(s):
    """UTC ISO stamp -> epoch. timegm, NOT mktime — these stamps are written with gmtime, and
    mktime would parse them as local time, skewing every lease/TTL by the timezone offset."""
    try:
        import calendar
        return float(calendar.timegm(time.strptime((s or "")[:19], "%Y-%m-%dT%H:%M:%S")))
    except Exception:
        return 0.0


def claim(path, ids, worker, ttl_min=15):
    """Claim free items, serializing on conflict_key + respecting lease TTL. Returns claimed ids."""
    with _Lock(path):
        d = load(path)
        now = time.time()
        busy = {it.get("conflict_key") for it in d["queue"]
                if it.get("conflict_key") and it.get("claimed_by")
                and (now - _epoch(it.get("claimed_at"))) < ttl_min * 60}
        claimed_items = []
        for it in d["queue"]:
            if it.get("id") in ids and not it.get("claimed_by"):
                ck = it.get("conflict_key")
                if ck and ck in busy:               # another live lease owns this target -> serialize
                    continue
                it["claimed_by"] = worker
                it["claimed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                if ck:
                    busy.add(ck)
                claimed_items.append(it)
        if claimed_items:
            _save(path, d)
    print("claimed:", [it.get("id") for it in claimed_items])
    return [it.get("id") for it in claimed_items]


# ─────────────────────────── archival (A107) ───────────────────────────
#
# 1,231 of 1,322 items were terminal (shipped/rejected/reverted/reference) yet re-parsed on every
# read of the 1.5 MB hot queue. `archive` pages terminal items PAST A WINDOW out to a sibling
# `<name>-archive.json` (same {"queue":[...]} shape, so it validates + stays queryable), keeping the
# live queue small. The archive is written BEFORE the queue shrinks, so a crash between the two
# writes duplicates an item across both files (a re-run/validate tolerates it) but NEVER loses one.
# `unarchive` is the revert/recover path. The rejection-memory invariant is load-bearing: every
# dedupe scanner that remembers a rejection (proposal_dedupe_history, reconcile.already_proposed) and
# the add id-fence consult the archive too, so archival can NEVER reopen a door a rejection closed.

TERMINAL_STAGES = {"shipped", "rejected", "reverted", "reference"}


def archive_path_for(queue_path):
    """The sibling archive path `<name>-archive.json` (A103-ready: it moves WITH the queue when the
    path seam lands, and every dedupe scanner derives it the same way)."""
    base, ext = os.path.splitext(str(queue_path))
    return base + "-archive" + (ext or ".json")


def _terminal_ts(it):
    """The item's terminal-transition time = the `ts` of its last history entry (present on 100% of
    terminal items), or None when unparseable (such an item is kept live, never archived blind)."""
    hist = it.get("history")
    if isinstance(hist, list) and hist and isinstance(hist[-1], dict):
        return hist[-1].get("ts")
    return None


def _load_archive(archive_path):
    """Read the archive as a validated {"queue":[...]}; a missing file is an empty archive."""
    if not os.path.exists(archive_path):
        return {"queue": []}
    d = _read_json(archive_path)
    if d is None:
        _die(f"archive {archive_path} did not parse - recover from git history (state/ is tracked)")
    err = validate(d)
    if err:
        _die(f"archive {archive_path} is invalid: {err}")
    return d


def archived_ids(queue_path, archive_path=None):
    """The id set held in the sibling archive (empty if none) - so the add id-fence and any caller
    can treat an archived id as still taken."""
    return {it.get("id") for it in _load_archive(archive_path or archive_path_for(queue_path))["queue"]}


def archive(queue_path, window_days=30, now=None, archive_path=None):
    """Move terminal items whose terminal-transition ts is older than `window_days` from the live
    queue to the sibling archive. Atomic under the queue lock, revertible via `unarchive`. Returns
    the moved ids. In-window and non-terminal items stay live; an item with no parseable terminal ts
    stays live (never archived blind)."""
    archive_path = archive_path or archive_path_for(queue_path)
    cutoff = (_epoch(now) if now else time.time()) - float(window_days) * 86400.0
    with _Lock(queue_path):
        data = load(queue_path)
        arch = _load_archive(archive_path)
        arch_ids = {it.get("id") for it in arch["queue"]}
        keep, move = [], []
        for it in data["queue"]:
            ts = _terminal_ts(it)
            if it.get("stage") in TERMINAL_STAGES and ts and _epoch(ts) and _epoch(ts) < cutoff:
                move.append(it)
            else:
                keep.append(it)
        if not move:
            return []
        merged = arch["queue"] + [it for it in move if it.get("id") not in arch_ids]
        err = validate({"queue": merged})
        if err:
            _die(f"refusing to write invalid archive (live untouched): {err}")
        # archive FIRST (safe ordering: a crash before the queue write duplicates, never loses)
        merged.sort(key=lambda it: str(it.get("id")))
        _write_atomic(archive_path, {"_comment": "aios queue ARCHIVE - terminal items paged out of "
                                     "queue.json (A107); STILL consulted by dedupe. Edit via queue_tx.",
                                     "queue": merged})
        _save(queue_path, {"queue": keep})
        moved_ids = [it.get("id") for it in move]
    print(f"archived {len(move)} item(s) -> {archive_path}; live queue now {len(keep)} items")
    return moved_ids


def unarchive(queue_path, ids, archive_path=None):
    """Move items back from the archive to the live queue (the rewind/recover path). Skips an id
    already live (no duplicate). Returns the restored ids."""
    archive_path = archive_path or archive_path_for(queue_path)
    want = set(ids)
    with _Lock(queue_path):
        data = load(queue_path)
        arch = _load_archive(archive_path)
        live_ids = {it.get("id") for it in data["queue"]}
        back = [it for it in arch["queue"] if it.get("id") in want and it.get("id") not in live_ids]
        # Remove EVERY requested id from the archive, incl. one already live — so a re-run after a
        # crash between the two writes below cleans the duplicate "ghost" rather than leaving a stale
        # archived copy that a later archive() could resurrect over the live one (crash-idempotent).
        pull = want & {it.get("id") for it in arch["queue"]}
        if not back and not pull:
            return []
        remaining = [it for it in arch["queue"] if it.get("id") not in pull]
        if back:
            _save(queue_path, {"queue": data["queue"] + back})
        _write_atomic(archive_path, {"_comment": "aios queue ARCHIVE - terminal items paged out of "
                                     "queue.json (A107); STILL consulted by dedupe. Edit via queue_tx.",
                                     "queue": sorted(remaining, key=lambda it: str(it.get("id")))})
        total_live = len(data["queue"]) + len(back)
    print(f"unarchived {len(back)} item(s) <- {archive_path}; live queue now {total_live} items")
    return sorted(it.get("id") for it in back)


# ─────────────────────────── CLI ───────────────────────────

from _util import utf8_stdio as _utf8_stdio


if __name__ == "__main__":
    _utf8_stdio()
    op = sys.argv[1] if len(sys.argv) > 1 else ""
    if op == "validate":
        p = sys.argv[2]
        if not os.path.exists(p):
            print("OK")                   # a missing file is a legitimately-empty fresh store
            sys.exit(0)
        d = _read_json(p)
        e = "file did not parse" if d is None else validate(d)
        print("OK" if e is None else f"INVALID: {e}")
        sys.exit(0 if e is None else 1)
    elif op == "commit":
        commit(sys.argv[2], sys.argv[3])
    elif op == "claim":
        claim(sys.argv[2], sys.argv[3].split(","), sys.argv[4])
    elif op == "add":
        _apply_items(sys.argv[2], _read_items(sys.argv[3]), "add")
    elif op == "update":
        _apply_items(sys.argv[2], _read_items(sys.argv[3]), "update")
    elif op == "select":
        a = sys.argv[2:]
        known = {"--stage", "--lane", "--limit"}
        unknown = [t for t in a[1:] if t.startswith("--") and t not in known]
        if unknown:
            _die(f"select: unknown flag(s) {unknown} (supported: {sorted(known)}) - refusing so a "
                 f"typo'd filter can't silently over-select")
        def _opt(name):
            return a[a.index(name) + 1] if name in a else None
        select(a[0], stage=_opt("--stage"), lane=_opt("--lane"), limit=_opt("--limit"))
    elif op == "archive":
        # archive <queue.json> [--window-days N] [--now ISO]
        a = sys.argv[2:]
        wd = float(a[a.index("--window-days") + 1]) if "--window-days" in a else 30
        nw = a[a.index("--now") + 1] if "--now" in a else None
        archive(a[0], window_days=wd, now=nw)
    elif op == "unarchive":
        # unarchive <queue.json> <id1,id2,...>
        unarchive(sys.argv[2], sys.argv[3].split(","))
    elif op == "dump":
        print(json.dumps(load(sys.argv[2]), indent=2, ensure_ascii=False))
    elif op == "ls":
        for it in load(sys.argv[2])["queue"]:
            print(f'{it.get("stage",""):<9} {it.get("lane") or "-":<10} {it.get("id")}')
    else:
        print(__doc__)
        sys.exit(1)
