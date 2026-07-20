#!/usr/bin/env python3
"""context_log.py — the ONE appender for the aios context-log.jsonl.

Every pipeline stage emits its per-run record through `emit()` instead of hand-writing a JSON
line, so the append path is a single tested primitive: refuse to glue onto a torn tail, append
at EOF + fsync (atomic via O_APPEND on POSIX, non-atomic on Windows — see `emit`), verify the
tail once.

`check` (A21) is the RUNNER's deterministic post-run integrity gate: the model self-reporting
"line verified-appended" is not evidence (three live integrity failures said otherwise), so
after `claude -p` returns the runner asks this tool two questions it answers from disk alone —
did a record for this stage land inside the run window, and does the tail parse line-by-line.
Exit 0 = quiet OK; exit 3 = WARN (missing line and/or unparseable tail) with the reasons on
stdout for the task log; exit 2 = could not even read the inputs.

stdlib only; fact-free (the log path is an argument).
"""
import argparse
import json
import os
import sys


class ContextLogWriteError(Exception):
    """Raised when an append cannot be verified on disk."""


def _default_append(path, line):
    """Append one line + '\\n', flushed to disk. Binary write so the terminator is exactly
    '\\n' on every platform (Windows text mode would expand it to '\\r\\n' and desync the
    JSONL other writers append as '\\n').

    Append atomicity is PLATFORM-DEPENDENT: on POSIX, "ab" maps to O_APPEND and the kernel
    positions each write at true EOF atomically, so concurrent appenders never clobber one
    another. On Windows the CRT implements append as lseek-to-EOF-then-write, which is NOT
    atomic across handles — genuinely concurrent appenders can each seek to the same EOF and
    the later write overwrites the earlier line (~4% whole-line loss observed at 4×25). See
    emit()'s note on why that is an accepted risk for this log (A52)."""
    with open(path, "ab") as f:
        f.write((line + "\n").encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())


def _read_bytes(path):
    """Whole-file bytes, or b'' if absent."""
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return b""


def emit(record, path, *, _append=_default_append):
    """Append `record` as one JSON line to `path`.

    Refuses to append onto a torn tail — a prior line missing its terminator, i.e. a crashed
    writer's partial line. The append itself (`_default_append`) is atomic on POSIX (O_APPEND)
    but NOT on Windows (lseek-to-EOF-then-write; see `_default_append`): under genuine concurrency
    (gate fan-out, overlapping scheduled runs) Windows can silently drop a whole line — it never
    tears a line and never raises. This is an ACCEPTED risk (A52, 2026-07-09), not a guarded
    invariant: this is the observability context-log, not canonical state (queue.json stays safe
    via its own lock + tmp/replace), and a dropped stage record is caught on the next run by the
    A21 `check` stage-presence backstop. A post-append readback-equality verify was removed (A47):
    it added nothing on POSIX, and it FALSE-ALARMED whenever another appender's line legitimately
    raced in between our read and our readback — raising ContextLogWriteError on data that was
    safely on disk. Returns the serialized line on success; raises ContextLogWriteError only on a
    torn tail.
    """
    line = json.dumps(record, ensure_ascii=False)
    before = _read_bytes(path)
    if before and not before.endswith((b"\n", b"\r")):
        raise ContextLogWriteError(
            "refusing to append: context-log does not end with a terminator "
            "(torn prior write?): " + path)
    _append(path, line)
    return line


def check(path, stages=None, since=None, tail_lines=50):
    """Post-run integrity check -> (ok, messages).

    - Tail parse: the last `tail_lines` non-empty lines must each be valid JSON.
    - Stage presence (only when `stages` given): some record whose `stage` is in `stages`
      carries a `ts` >= `since` (ISO-8601, 'Z' tolerated). Records with a missing/unparseable
      `ts` never satisfy the window — presence must be provable, not assumed.
    An absent log file is a WARN (the run claims to have emitted; nothing exists), not a crash.
    """
    msgs = []
    raw = _read_bytes(path)
    if not raw:
        return False, ["WARN context-log absent or empty: " + path]
    # split on '\n' only: json.dumps(ensure_ascii=False) leaves U+2028/U+0085 unescaped inside
    # strings, and splitlines() would fragment such a record into false parse WARNs
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
    lines = [(i, ln) for i, ln in enumerate(text.split("\n"), 1) if ln.strip()]

    bad = []
    for i, ln in lines[-max(1, int(tail_lines)):]:
        try:
            json.loads(ln)
        except json.JSONDecodeError:
            bad.append(i)
    if bad:
        msgs.append("WARN %d unparseable context-log line(s) in tail: %s"
                    % (len(bad), ",".join(map(str, bad))))

    if stages:
        want = {s.strip() for s in stages if s.strip()}
        floor = (since or "").strip().replace("Z", "")
        found = False
        for _, ln in lines:
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if rec.get("stage") not in want:
                continue
            ts = str(rec.get("ts") or "").replace("Z", "")
            if not floor or (ts and ts >= floor):
                found = True
                break
        if not found:
            msgs.append("WARN missing context-log line for stage %s since %s"
                        % ("|".join(sorted(want)), since or "(any time)"))
    return not msgs, msgs


# ─────────────────────────── CLI (so a model-driven stage prompt can call it) ───────────────────────────
# A stage prompt shells out instead of importing:
#   python tools/context_log.py emit --path <log> --record-file <tmp.json>   (recommended), or
#   ... --record '<json>'   /   echo '<json>' | ... emit --path <log>   (stdin).
# Exit 0 = appended + tail-verified; non-zero = the append could not be confirmed.

def _load_raw(args):
    if args.record is not None:
        return args.record
    if args.record_file is not None:
        with open(args.record_file, encoding="utf-8") as f:
            return f.read()
    return sys.stdin.read()


from _util import utf8_stdio as _utf8_stdio


def main(argv=None):
    _utf8_stdio()
    parser = argparse.ArgumentParser(
        prog="context_log.py",
        description="Append one record to the aios context-log.jsonl (tail-verified).")
    sub = parser.add_subparsers(dest="cmd")
    em = sub.add_parser("emit", help="append one JSON record")
    em.add_argument("--path", required=True, help="context-log.jsonl path")
    src = em.add_mutually_exclusive_group()
    src.add_argument("--record", help="the record as a JSON string")
    src.add_argument("--record-file", help="path to a file holding the JSON record")
    # neither flag -> read the JSON record from stdin
    ck = sub.add_parser("check", help="post-run integrity check (A21 runner backstop)")
    ck.add_argument("--path", required=True, help="context-log.jsonl path")
    ck.add_argument("--stage", help="expected stage name(s), comma-separated; omit to skip")
    ck.add_argument("--since", help="ISO-8601 floor for the stage record's ts (run start)")
    ck.add_argument("--tail-lines", type=int, default=50, help="tail lines to parse-validate")
    args = parser.parse_args(argv)

    if args.cmd == "check":
        stages = [s for s in (args.stage or "").split(",") if s.strip()] or None
        try:
            ok, msgs = check(args.path, stages=stages, since=args.since,
                             tail_lines=args.tail_lines)
        except OSError as ex:
            print("ERROR: " + str(ex), file=sys.stderr)
            return 2
        for m in msgs:
            print(m)
        if ok:
            print("OK context-log check clean")
            return 0
        return 3

    if args.cmd != "emit":
        parser.print_help(sys.stderr)
        return 2
    try:
        record = json.loads(_load_raw(args))
    except (json.JSONDecodeError, OSError) as ex:
        print("ERROR: could not read a JSON record: " + str(ex), file=sys.stderr)
        return 2
    try:
        emit(record, args.path)
    except (ContextLogWriteError, OSError) as ex:
        print("ERROR: " + str(ex), file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
