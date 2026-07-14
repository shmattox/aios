#!/usr/bin/env python3
"""queue_tx test harness — single-file substrate (A4). Exercises the store in an isolated temp
dir: CLI ops, invariants (dedupe fence, traversal fences, live-untouched-on-reject), claim
lease, the advisory write lock, and the legacy shard-dir fence (fail-loud only — the one-time
`migrate` collapse verb was removed as dead code in A51). Scratch; safe to delete."""
import json, os, sys, tempfile, shutil, glob, subprocess, time

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../engine/tools
sys.path.insert(0, HARNESS)
import queue_tx

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ok  " if cond else " FAIL ") + name)

def mk_item(i, stage="captured", ck=None, lane=None):
    it = {"id": i, "stage": stage, "history": [{"ts": "2026-07-05T00:00:00Z", "stage": "captured"}]}
    if ck: it["conflict_key"] = ck
    if lane is not None: it["lane"] = lane
    return it

d = tempfile.mkdtemp(prefix="qtx_")
try:
    live = os.path.join(d, "queue.json")
    prop = live + ".items"

    # 1. fresh/empty store: first add creates the single file (no shard dir, no residue)
    json.dump([mk_item("alpha-2026")], open(prop, "w", encoding="utf-8"), indent=2)
    r = subprocess.run([sys.executable, os.path.join(HARNESS, "queue_tx.py"), "add", live, prop],
                       capture_output=True, text=True)
    check("empty store: first add succeeds", r.returncode == 0)
    check("store is a single file (no shard dir)", os.path.isfile(live) and not os.path.isdir(live + ".d"))
    check("no .tmp residue after a write", not os.path.exists(live + ".tmp"))
    check("no .lock residue after a write", not os.path.exists(live + ".lock"))

    # 2. add dedupe fence: re-adding an existing id is refused, live untouched
    rdup = subprocess.run([sys.executable, os.path.join(HARNESS, "queue_tx.py"), "add", live, prop],
                          capture_output=True, text=True)
    check("add refuses a duplicate id (dedupe fence)",
          rdup.returncode != 0 and "dedupe" in (rdup.stdout + rdup.stderr))
    check("live untouched after refused add", len(queue_tx.load(live)["queue"]) == 1)

    # 3. update advances an existing item; a missing id is refused
    json.dump([mk_item("alpha-2026", "sorted", "dev/wiki/knowledge/alpha.md", "auto-ship")],
              open(prop, "w", encoding="utf-8"), indent=2)
    queue_tx._apply_items(live, queue_tx._read_items(prop), "update")
    check("update advanced alpha to sorted",
          next(i for i in queue_tx.load(live)["queue"] if i["id"] == "alpha-2026")["stage"] == "sorted")
    json.dump([mk_item("ghost-2026")], open(prop, "w", encoding="utf-8"), indent=2)
    rmiss = subprocess.run([sys.executable, os.path.join(HARNESS, "queue_tx.py"), "update", live, prop],
                           capture_output=True, text=True)
    check("update refuses a missing id", rmiss.returncode != 0)

    # 4. commit: whole-queue replace (add delta, keep alpha); invalid proposed leaves live untouched
    newq = {"queue": [
        mk_item("alpha-2026", "sorted", "dev/wiki/knowledge/alpha.md", "auto-ship"),
        mk_item("delta-2026", "captured"),
    ]}
    json.dump(newq, open(prop, "w", encoding="utf-8"), indent=2)
    queue_tx.commit(prop, live)
    ids = {i["id"] for i in queue_tx.load(live)["queue"]}
    check("commit replaced the queue (alpha + delta)", ids == {"alpha-2026", "delta-2026"})
    bad = {"queue": [mk_item("bad-2026", "sorted")]}   # sorted w/o conflict_key
    json.dump(bad, open(prop, "w", encoding="utf-8"), indent=2)
    rbad = subprocess.run([sys.executable, os.path.join(HARNESS, "queue_tx.py"), "commit", prop, live],
                          capture_output=True, text=True)
    check("bad proposed rejected (exit!=0)", rbad.returncode != 0)
    check("live untouched after rejected commit",
          {i["id"] for i in queue_tx.load(live)["queue"]} == {"alpha-2026", "delta-2026"})

    # 5. validate CLI: OK on a good store, OK on a missing file (fresh), INVALID on garbage
    r2 = subprocess.run([sys.executable, os.path.join(HARNESS, "queue_tx.py"), "validate", live],
                        capture_output=True, text=True)
    check("validate CLI returns OK", r2.returncode == 0 and "OK" in r2.stdout)
    r3 = subprocess.run([sys.executable, os.path.join(HARNESS, "queue_tx.py"), "validate",
                         os.path.join(d, "nope.json")], capture_output=True, text=True)
    check("validate CLI: missing file is a legit fresh store (OK)", r3.returncode == 0)
    garb = os.path.join(d, "garbage.json"); open(garb, "w").write("{ not json")
    r4 = subprocess.run([sys.executable, os.path.join(HARNESS, "queue_tx.py"), "validate", garb],
                        capture_output=True, text=True)
    check("validate CLI: unparseable file is INVALID (exit!=0)",
          r4.returncode != 0 and "INVALID" in r4.stdout)

    # 6. select filters a subset
    rsel = subprocess.run([sys.executable, os.path.join(HARNESS, "queue_tx.py"), "select", live,
                           "--stage", "captured"], capture_output=True, text=True)
    sel = json.loads(rsel.stdout)
    check("select --stage captured returns only captured",
          all(i["stage"] == "captured" for i in sel["queue"]))

    # 7. claim sets the lease and serializes on conflict_key
    queue_tx.claim(live, ["delta-2026"], "worker-1")
    claimed = next(i for i in queue_tx.load(live)["queue"] if i["id"] == "delta-2026")
    check("claim set claimed_by", claimed.get("claimed_by") == "worker-1")
    # two free items sharing a conflict_key: one claim call takes only the first
    json.dump([mk_item("ck-a", "sorted", "dev/wiki/knowledge/same.md", "review"),
               mk_item("ck-b", "sorted", "dev/wiki/knowledge/same.md", "review")],
              open(prop, "w", encoding="utf-8"), indent=2)
    queue_tx._apply_items(live, queue_tx._read_items(prop), "add")
    got = queue_tx.claim(live, ["ck-a", "ck-b"], "worker-2")
    check("claim serializes on conflict_key (one of two same-ck items)", got == ["ck-a"])

    # 8. traversal fences (path-traversal rejected; legitimate shapes pass)
    check("validate rejects a ../.. conflict_key",
          queue_tx.validate({"queue": [mk_item("e1", "sorted", "../../evil", "auto-ship")]}) is not None)
    check("validate rejects an embedded .. segment (passes the regex, caught by the .. fence)",
          queue_tx.validate({"queue": [mk_item("e2", "sorted", "dev/wiki/../../etc/passwd.md",
                                               "auto-ship")]}) is not None)
    evil_pp = {"queue": [mk_item("e3")]}; evil_pp["queue"][0]["payload_path"] = "raw/../../secrets.md"
    check("validate rejects a .. payload_path", queue_tx.validate(evil_pp) is not None)
    good = {"queue": [mk_item("g1", "sorted", "dev/wiki/entities/bun.md", "auto-ship"),
                      mk_item("g2", "shipped", "familyoffice/wiki/journal/2026-06-21.md", "review")]}
    good["queue"][0]["payload_path"] = "03_Dev/raw/inbox/x/bun.md"
    check("validate accepts legitimate conflict_key + relative payload_path shapes",
          queue_tx.validate(good) is None)
    abspp = {"queue": [mk_item("g3")]}; abspp["queue"][0]["payload_path"] = os.path.join(d, "abs.md")
    check("validate accepts an absolute payload_path (legacy/e2e shape) with no .. segment",
          queue_tx.validate(abspp) is None)

    # 9. advisory lock: a held lock blocks a writer (fail-loud), a stale lock is reclaimed
    lock_path = live + ".lock"
    open(lock_path, "w").write('{"pid": 0}')
    old_timeout = queue_tx.LOCK_TIMEOUT_S
    queue_tx.LOCK_TIMEOUT_S = 1
    blocked = False
    try:
        json.dump([mk_item("locked-out-2026")], open(prop, "w", encoding="utf-8"), indent=2)
        queue_tx._apply_items(live, queue_tx._read_items(prop), "add")
    except SystemExit:
        blocked = True
    finally:
        queue_tx.LOCK_TIMEOUT_S = old_timeout
    check("a held lock blocks a writer (fail-loud)", blocked)
    check("blocked writer left live untouched",
          not any(i["id"] == "locked-out-2026" for i in queue_tx.load(live)["queue"]))
    # stale lock (older than LOCK_STALE_S) is reclaimed and the write proceeds
    stale_t = time.time() - queue_tx.LOCK_STALE_S - 10
    os.utime(lock_path, (stale_t, stale_t))
    queue_tx._apply_items(live, queue_tx._read_items(prop), "add")
    check("a stale lock is reclaimed and the write proceeds",
          any(i["id"] == "locked-out-2026" for i in queue_tx.load(live)["queue"]))
    check("lock released after the write", not os.path.exists(lock_path))

    # 9a. A47 reclaim primitive — DETERMINISTIC proof of the core invariant the review-gate flagged:
    # the reclaim decides staleness on the inode it atomically OWNS, so it NEVER removes a lock it
    # can't prove crashed. A plain check-path-then-remove could steal a FRESH lock re-created in the
    # window (the residual TOCTOU). These pin both outcomes without relying on thread scheduling.
    rl = os.path.join(d, "reclaim.json")
    lk = queue_tx._Lock(rl)
    # (i) a genuinely stale lock is reclaimed (True) and removed
    open(lk.path, "w").write('{"pid": 0}')
    st = time.time() - queue_tx.LOCK_STALE_S - 10
    os.utime(lk.path, (st, st))
    reclaimed_stale = lk._reclaim_if_stale()
    check("reclaim: a stale lock is cleared (returns True, file removed)",
          reclaimed_stale and not os.path.exists(lk.path))
    check("reclaim: no .reclaim residue left behind", not glob.glob(lk.path + ".reclaim.*"))
    # (ii) a FRESH (live) lock is NEVER removed — the exact double-hold the gate found. Even if the
    # cheap pre-check misfired and reclaim ran on a live lock, it must restore it and refuse (False).
    open(lk.path, "w").write('{"pid": 99999}')          # fresh mtime = now
    reclaimed_fresh = lk._reclaim_if_stale()
    check("reclaim: a LIVE lock is preserved, never stolen (returns False, file intact)",
          reclaimed_fresh is False and os.path.exists(lk.path))
    check("reclaim: live-lock path holds a real lockfile after the refused reclaim",
          '{"pid": 99999}' in open(lk.path).read() and not glob.glob(lk.path + ".reclaim.*"))
    os.remove(lk.path)
    # (iii) an absent lock is a no-op success (progress: retry acquire)
    check("reclaim: absent lock returns True (retry acquire)", lk._reclaim_if_stale() is True)

    # 9b. A47 two-waiter smoke test: two writers that BOTH see the same stale lock must NOT both
    # acquire it. This is a scheduling-dependent smoke test (the deterministic proof is 9a); it
    # asserts strict mutual exclusion (max concurrent holders == 1) under a barrier that forces
    # both into __enter__ at once while the stale lock exists.
    import threading
    live3 = os.path.join(d, "twowaiter.json")
    json.dump({"queue": []}, open(live3, "w", encoding="utf-8"))
    open(live3 + ".lock", "w").write('{"pid": 0}')                 # a pre-existing lock ...
    st = time.time() - queue_tx.LOCK_STALE_S - 10
    os.utime(live3 + ".lock", (st, st))                            # ... that is already STALE
    old_to = queue_tx.LOCK_TIMEOUT_S
    queue_tx.LOCK_TIMEOUT_S = 10
    barrier = threading.Barrier(2)
    active = {"n": 0, "max": 0}
    guard = threading.Lock()
    lock_errors = []
    def contender():
        try:
            barrier.wait()                                         # both arrive together
            with queue_tx._Lock(live3):
                with guard:
                    active["n"] += 1
                    active["max"] = max(active["max"], active["n"])
                time.sleep(0.3)                                    # hold long enough to catch overlap
                with guard:
                    active["n"] -= 1
        except BaseException as ex:                                # noqa: BLE001 - any raise is a bug
            lock_errors.append(repr(ex))
    tt = [threading.Thread(target=contender), threading.Thread(target=contender)]
    for t in tt: t.start()
    for t in tt: t.join()
    queue_tx.LOCK_TIMEOUT_S = old_to
    check("two-waiter stale-lock: neither writer errored", not lock_errors)
    check("two-waiter stale-lock: exactly one holder at a time (no TOCTOU double-hold)",
          active["max"] == 1)
    check("two-waiter stale-lock: no reclaim residue, lock released after both ran",
          not os.path.exists(live3 + ".lock") and not glob.glob(live3 + ".lock.reclaim.*"))

    # 10. legacy shard-dir fence (A51): the G13 sharded layout was retired in A4 and the one-time
    # `migrate` collapse verb is gone (dead code removed). A lingering `<queue>.json.d/` must still
    # fence EVERY op fail-loud (defensive - no current install carries one), with a message that
    # states the A4 retirement rather than pointing at a `migrate` command this build doesn't ship.
    # The fence is read-only: it must never delete/touch the shard dir it finds.
    d2 = tempfile.mkdtemp(prefix="qtx_legacy_")
    try:
        live2 = os.path.join(d2, "queue.json")
        shard_dir = live2 + ".d"
        os.makedirs(shard_dir)
        json.dump(mk_item("fence-shard"), open(os.path.join(shard_dir, "fence-shard.json"),
                                               "w", encoding="utf-8"))
        fence_prop = os.path.join(d2, "fence.items")
        json.dump([mk_item("fence-add")], open(fence_prop, "w", encoding="utf-8"))
        rfence = subprocess.run([sys.executable, os.path.join(HARNESS, "queue_tx.py"), "add",
                                 live2, fence_prop], capture_output=True, text=True)
        outfence = rfence.stdout + rfence.stderr
        check("fence: a legacy shard dir makes an op fail loud (exit!=0)", rfence.returncode != 0)
        check("fence: message states the A4 retirement, not a runnable migrate command",
              "retired in A4" in outfence and "run `queue_tx.py migrate" not in outfence)
        check("fence: no such CLI verb remains (migrate op unknown)",
              subprocess.run([sys.executable, os.path.join(HARNESS, "queue_tx.py"),
                              "migrate", live2], capture_output=True, text=True).returncode != 0)
        check("fence: the shard dir itself is left untouched (not deleted)",
              os.path.isdir(shard_dir) and
              os.path.exists(os.path.join(shard_dir, "fence-shard.json")))
    finally:
        shutil.rmtree(d2, ignore_errors=True)

    # 11. atomicity shape: the on-disk file is always complete, parseable JSON after every op
    on_disk = json.load(open(live, encoding="utf-8"))
    check("on-disk store parses and validates after the run", queue_tx.validate(on_disk) is None)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:", FAIL)
    sys.exit(1 if FAIL else 0)
finally:
    shutil.rmtree(d, ignore_errors=True)
