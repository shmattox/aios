#!/usr/bin/env python3
"""context_log.py test harness — the one context-log appender (torn-tail guard + O_APPEND,
concurrency-safe: A47 removed the post-append additive readback that false-alarmed under
concurrent appends). Run: python tools/tests/test_context_log.py"""
import json, os, sys, tempfile, shutil

HARNESS_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../engine/tools
sys.path.insert(0, HARNESS_TOOLS)
import context_log

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ok  " if cond else " FAIL ") + name)

d = tempfile.mkdtemp(prefix="ctxlog_")
try:
    REC = {"ts": "2026-06-25T18:05:00Z", "stage": "brief", "run_id": "2026-06-25",
           "repairs": [], "anomalies": [], "note": "cache-write — em-dash and (parens) preserved"}

    # ── happy path: append + verify, line round-trips ──
    p = os.path.join(d, "log.jsonl")
    returned = context_log.emit(REC, p)
    last = open(p, encoding="utf-8").read().splitlines()[-1]
    check("emit returns the exact serialized line", returned == last)
    check("emitted line parses back to the record", json.loads(last) == REC)
    check("unicode preserved verbatim (ensure_ascii=False)", "—" in last)
    check("file ends with a single trailing newline", open(p, "rb").read().endswith(b"\n"))

    # ── creates a fresh file if absent ──
    p2 = os.path.join(d, "fresh.jsonl")
    context_log.emit({"stage": "capture", "n": 1}, p2)
    check("emit creates a missing log file", os.path.exists(p2))

    # ── append after an existing line keeps both, order preserved ──
    context_log.emit({"stage": "garden", "n": 2}, p2)
    lines2 = open(p2, encoding="utf-8").read().splitlines()
    check("append preserves prior lines in order",
          len(lines2) == 2 and json.loads(lines2[0])["n"] == 1 and json.loads(lines2[1])["n"] == 2)

    # ── refuses to append onto a torn tail (prior write lost its newline) ──
    p3 = os.path.join(d, "torn-tail.jsonl")
    with open(p3, "w", encoding="utf-8") as f:
        f.write('{"stage":"brief","note":"prior line with no newline"}')  # NO trailing \n
    raised = False
    try:
        context_log.emit(REC, p3)
    except context_log.ContextLogWriteError:
        raised = True
    check("refuses to append onto a missing-newline (torn) tail", raised)

    # ── A47: a concurrent contracted appender landing a line BETWEEN our read and our write must
    #    NOT false-alarm. The removed post-append readback verify raised ContextLogWriteError here
    #    on data that was safely on disk (the file grew by the OTHER line too, so
    #    readback != before + our_line). Simulate the interleave deterministically through the
    #    _append seam: another writer's real O_APPEND lands first, then ours. ──
    p4 = os.path.join(d, "interleave.jsonl")
    context_log.emit({"stage": "a", "n": 0}, p4)
    def interleaving_append(path, line):
        context_log._default_append(path, json.dumps({"stage": "other", "n": 99}))  # the racer
        context_log._default_append(path, line)                                     # then us
    raised = False
    try:
        context_log.emit({"stage": "c", "n": 1}, p4, _append=interleaving_append)
    except context_log.ContextLogWriteError:
        raised = True
    check("a concurrent append between read and write does NOT false-alarm (A47)", not raised)
    lines4 = [json.loads(l) for l in open(p4, encoding="utf-8").read().splitlines()]
    check("both the racing line and ours landed intact", len(lines4) == 3 and lines4[-1]["n"] == 1)

    # ── A47: real concurrency — 4 threads emitting to ONE log. emit()'s two guarantees under
    #    concurrency: it NEVER raises (no false-alarm) and NEVER writes a torn/partial line. NOTE
    #    we do NOT assert all lines land: on Windows the CRT's append (lseek-to-EOF-then-write) is
    #    not atomic across handles, so a few concurrent appends can overwrite each other — a
    #    pre-existing OS limitation, out of A47's scope (the false-alarm was the bug), and caught
    #    downstream by the A21 `check` stage-presence backstop. Every line that DOES land must be
    #    intact — that is the invariant emit() owns. ──
    import threading
    p5 = os.path.join(d, "threads.jsonl")
    errors = []
    def worker(wid):
        for i in range(25):
            try:
                context_log.emit({"stage": "t", "w": wid, "i": i}, p5)
            except Exception as ex:                # noqa: BLE001 - any raise here is the bug
                errors.append(repr(ex))
    threads = [threading.Thread(target=worker, args=(w,)) for w in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    raw5 = [ln for ln in open(p5, encoding="utf-8").read().splitlines() if ln.strip()]
    unparseable = 0
    for ln in raw5:
        try:
            json.loads(ln)
        except json.JSONDecodeError:
            unparseable += 1
    check("4 threads × 25 concurrent emits: zero errors raised (A47 false-alarm fix)", not errors)
    check("every landed concurrent line is intact (no torn/partial line)", unparseable == 0 and raw5)

    # ── CRLF-terminated prior content is tolerated (not flagged as corruption) ──
    p8 = os.path.join(d, "crlf.jsonl")
    with open(p8, "wb") as f:
        f.write(b'{"stage":"x","n":0}\r\n')   # a clean line with a Windows CRLF terminator
    ok = False
    try:
        returned8 = context_log.emit({"stage": "y", "n": 1}, p8)
        ok = open(p8, "rb").read().endswith((returned8 + "\n").encode("utf-8"))
    except context_log.ContextLogWriteError:
        ok = False
    check("CRLF-terminated prior tail is tolerated, append still verifies", ok)

    # ── a clean injected appender still passes (verify isn't over-strict) ──
    def clean_append(path, line):
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush(); os.fsync(f.fileno())
    p6 = os.path.join(d, "clean-inject.jsonl")
    ok = False
    try:
        context_log.emit(REC, p6, _append=clean_append)
        ok = json.loads(open(p6, encoding="utf-8").read().splitlines()[-1]) == REC
    except context_log.ContextLogWriteError:
        ok = False
    check("a faithful injected appender passes verification", ok)

    # ─────────────────────────── CLI (what the stage prompts call) ───────────────────────────
    import io

    # --record: emit a JSON string, exit 0, line landed
    pc1 = os.path.join(d, "cli-record.jsonl")
    rc = context_log.main(["emit", "--path", pc1, "--record", json.dumps(REC)])
    landed = os.path.exists(pc1) and json.loads(open(pc1, encoding="utf-8").read().splitlines()[-1]) == REC
    check("CLI emit --record returns 0 and the line lands", rc == 0 and landed)

    # --record-file: the robust path for a model (write temp via its file tool, then emit)
    pc2 = os.path.join(d, "cli-file.jsonl")
    rf = os.path.join(d, "rec.json")
    open(rf, "w", encoding="utf-8").write(json.dumps(REC))
    rc = context_log.main(["emit", "--path", pc2, "--record-file", rf])
    check("CLI emit --record-file returns 0 and the line lands",
          rc == 0 and json.loads(open(pc2, encoding="utf-8").read().splitlines()[-1]) == REC)

    # stdin: emit reads the record from stdin when no --record/--record-file
    pc3 = os.path.join(d, "cli-stdin.jsonl")
    _old_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO(json.dumps(REC))
        rc = context_log.main(["emit", "--path", pc3])
    finally:
        sys.stdin = _old_stdin
    check("CLI emit reads the record from stdin",
          rc == 0 and json.loads(open(pc3, encoding="utf-8").read().splitlines()[-1]) == REC)

    # invalid JSON: non-zero exit, nothing written
    pc4 = os.path.join(d, "cli-bad.jsonl")
    rc = context_log.main(["emit", "--path", pc4, "--record", "{not json"])
    check("CLI emit rejects invalid JSON (exit != 0, no file)", rc != 0 and not os.path.exists(pc4))

    # a torn-tail target makes the CLI exit non-zero (fail-loud reaches the caller)
    pc5 = os.path.join(d, "cli-torn.jsonl")
    open(pc5, "w", encoding="utf-8").write('{"stage":"x"}')  # no terminator
    rc = context_log.main(["emit", "--path", pc5, "--record", json.dumps(REC)])
    check("CLI emit onto a torn tail returns non-zero", rc != 0)

    # ─────────────────────────── check (A21 runner backstop) ───────────────────────────
    pk = os.path.join(d, "check.jsonl")
    context_log.emit({"ts": "2026-07-05T04:00:00Z", "stage": "gate-auto", "run_id": "2026-07-05"}, pk)
    context_log.emit({"ts": "2026-07-05T05:45:00Z", "stage": "session-capture", "run_id": "2026-07-05"}, pk)

    ok, msgs = context_log.check(pk, stages=["gate-auto"], since="2026-07-05T03:55:00Z")
    check("check: stage line inside the window -> clean", ok and not msgs)

    ok, msgs = context_log.check(pk, stages=["gate-auto"], since="2026-07-05T04:30:00Z")
    check("check: stage line BEFORE the window -> WARN missing",
          not ok and any("missing" in m for m in msgs))

    ok, msgs = context_log.check(pk, stages=["garden"], since="2026-07-05T00:00:00Z")
    check("check: no line for the stage at all -> WARN missing",
          not ok and any("missing" in m and "garden" in m for m in msgs))

    ok, _ = context_log.check(pk, stages=["garden", "session-capture"],
                              since="2026-07-05T00:00:00Z")
    check("check: multi-stage — any one acceptable stage satisfies", ok)

    # a record with NO ts can never satisfy a window (presence must be provable)
    context_log.emit({"stage": "sort"}, pk)
    ok, _ = context_log.check(pk, stages=["sort"], since="2026-07-05T00:00:00Z")
    check("check: ts-less record does not satisfy a windowed presence check", not ok)
    ok, _ = context_log.check(pk, stages=["sort"])
    check("check: ts-less record satisfies an unwindowed presence check", ok)

    # seeded malformed tail line -> parse WARN with the line number
    with open(pk, "ab") as f:
        f.write(b'{"ts": "2026-07-05T06:00:00Z", "stage": "brief", "torn...\n')
    ok, msgs = context_log.check(pk)
    check("check: seeded malformed tail line -> WARN unparseable",
          not ok and any("unparseable" in m for m in msgs))

    # tail window: the malformed line outside --tail-lines is not scanned...
    for i in range(5):
        context_log.emit({"ts": "2026-07-05T07:00:0%dZ" % i, "stage": "pad", "n": i}, pk)
    ok, _ = context_log.check(pk, tail_lines=3)
    check("check: malformed line outside the tail window not flagged", ok)

    # absent/empty log -> WARN, not a crash
    ok, msgs = context_log.check(os.path.join(d, "nope.jsonl"))
    check("check: absent log -> WARN", not ok and any("absent" in m for m in msgs))

    # CLI: exit 0 clean / 3 warn
    rc = context_log.main(["check", "--path", pk, "--stage", "gate-auto",
                           "--since", "2026-07-05T03:55:00Z", "--tail-lines", "3"])
    check("CLI check clean -> exit 0", rc == 0)
    rc = context_log.main(["check", "--path", pk, "--stage", "never-ran",
                           "--since", "2026-07-05T03:55:00Z", "--tail-lines", "3"])
    check("CLI check missing stage -> exit 3", rc == 3)

finally:
    shutil.rmtree(d, ignore_errors=True)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
sys.exit(1 if FAIL else 0)
