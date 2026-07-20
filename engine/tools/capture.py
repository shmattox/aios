#!/usr/bin/env python3
"""capture.py — deterministic enqueuer for the aios INBOX-CAPTURE stage (reuse mode).

The mechanical guts of the inbox-capture stage, moved out of the SKILL.md into a tested,
stdlib-only tool — so the scheduled task shrinks to "run this, report the summary" and the
fragile file-walking lives in one reviewable place instead of being re-derived by a
model every overnight run.

WHY a checked-in tool, not model prose: capture is an aios PIPELINE stage that runs unattended on
a NATIVE schedule (Task Scheduler / launchd / cron) — or in-session on any OS; there are no
standalone background services. Keeping the mechanical file-walking in one tested tool means the
scheduled task registered from tasks.manifest.json just invokes it and reports the summary, instead
of a model re-deriving the walk every overnight run. Far less model surface.

Contract fit:
  - Fact-free (Stage Contract #1): zero person-facts here. All paths come from args, which
    the SKILL/installer resolves from the profile (connectors.yaml -> vault).
  - Atomic write via queue_tx.py (#4): we NEVER raw-write a queue file — `run` shells out to
    `queue_tx.py add` (dedupe-fenced, validated, atomic). When items enqueue, the ledger is
    updated only AFTER the add succeeds (fail-closed); a URL-dupe-ONLY delta (A26) appends
    the ledger with no add at all — ledger ids are a superset of queue ids by design.
  - Reuses, never reimplements: session-record discovery is `session_synth.py records`.
  - stdlib only (matches queue_tx.py / session_synth.py — portable to a stranger's python).

Commands:
  python capture.py scan --queue Q --ledger L --vault-root V --kb dev=03_Dev ... \
                         [--lookback-days 0] [--cap 50]   # 0 = unbounded (ledger-fenced)
        -> read-only; prints JSON {new_items, new_ids, new_urls, stats}. No writes.
  python capture.py run  (same args) [--context-log C]
        -> scan -> queue_tx add -> append ledger -> queue_tx validate. Prints a summary
           JSON. Exit 0 = enqueued+verified (or clean no-op); non-zero = failure (queue/
           ledger left consistent: with items in play the ledger appends only after a
           successful add; a dupe-only delta appends with no add).

The queue item id is the file STEM (slug); the LEDGER id is the path RELATIVE TO the vault
root (e.g. 03_Dev/raw/inbox/x/2026-06-21-foo.md) — they are different strings for the same
item, kept straight here exactly as the SKILL specified.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

import context_log as ctxlog  # the one context-log appender
from frontmatter import read_frontmatter as _frontmatter  # the one guarded flat-frontmatter reader

HERE = os.path.dirname(os.path.abspath(__file__))
# tracking params dropped during URL normalization (intake dedupe fence)
_TRACKING = re.compile(r"^(utm_[^=]*|ref|fbclid|gclid|mc_eid|igshid|si)$", re.I)


# ─────────────────────────── io helpers ───────────────────────────
def _longpath(path):
    """Windows MAX_PATH (260) guard, mirrored from capture_router (A58): stdlib open() raises
    FileNotFoundError on an absolute path at or over the limit unless it carries the \\?\
    extended-length prefix. capture globs {kb}/raw/inbox/** and a long routed artifact would read
    as None -> 'unreadable' forever. No-op off Windows / for short or already-prefixed paths."""
    if os.name == "nt":
        ap = os.path.abspath(path)
        if len(ap) >= 260 and not ap.startswith("\\\\?\\"):
            return "\\\\?\\" + ap
    return path


def _read_json(path):
    """Parse a JSON file, or None if missing/unparseable."""
    try:
        with open(_longpath(path), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _read_text(path):
    """Read a raw CAPTURE artifact's text. `errors="replace"` (never on queue/ledger — those go
    through _read_json): a cp1252 stub (Windows mail / a WhatsApp export) is not valid UTF-8, and a
    strict read would fail the raw permanently — the item re-enqueues and Sort re-reads it as
    'unreadable' EVERY run, forever (A49). Replacing the few undecodable bytes lets it ingest."""
    try:
        with open(_longpath(path), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _write_json_atomic(path, obj):
    """Atomic JSON write (tmp + os.replace; short PermissionError retry for a concurrent
    Windows reader holding the destination open). Returns True on success."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(3):
            try:
                os.replace(tmp, path)
                return True
            except PermissionError:
                time.sleep(0.2)
        return False
    except OSError:
        return False


# ─────────────────────────── pure helpers (the testable core) ───────────────────────────
def normalize_url(u):
    """Collapse a URL to a dedupe key: lowercase host, drop scheme, `www.`, a trailing
    slash, and tracking params — so `https://github.com/a/b/?utm_source=x` and
    `github.com/a/b` map to the same key. Returns "" for a falsy/garbage url."""
    if not u or not isinstance(u, str):
        return ""
    s = u.strip()
    s = re.sub(r"^[a-z]+://", "", s, flags=re.I)        # drop scheme
    s = re.sub(r"^www\.", "", s, flags=re.I)            # drop www.
    if "#" in s:                                         # drop fragment
        s = s.split("#", 1)[0]
    base, _, query = s.partition("?")
    host_path = base.rstrip("/")
    if "/" in host_path:
        host, path = host_path.split("/", 1)
        host_path = host.lower() + "/" + path
    else:
        host_path = host_path.lower()
    kept = []
    for pair in query.split("&"):
        if not pair:
            continue
        key = pair.split("=", 1)[0]
        if not _TRACKING.match(key):
            kept.append(pair)
    # sort the surviving params so the dedupe key is query-ORDER-insensitive: `?a=1&b=2` and
    # `?b=2&a=1` are the same resource and must collapse to one key (A49).
    return host_path + ("?" + "&".join(sorted(kept)) if kept else "")


def _mtime_iso(path):
    return datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rel(path, root):
    """Path relative to the vault root, forward-slashed (the LEDGER stable id form)."""
    return os.path.relpath(os.path.abspath(path), os.path.abspath(root)).replace(os.sep, "/")


def build_item(payload_path, source, kb, now_iso, mtime_iso):
    """A `captured` queue item per QUEUE.md — conflict_key:null even for session records
    (Sort honors the key carried in the record's own frontmatter; capture stays mechanical).

    `payload_path` MUST be **vault-relative, forward-slashed** (e.g. `03_Dev/raw/inbox/gmail/x.md`)
    — the caller passes the `_rel(path, vault_root)` form. Stored verbatim so the item is portable
    across machines/mounts (desktop `C:\\...\\vault` vs cloud `/home/user/vault`); every
    reader resolves it against ITS vault-root. (2026-07-01 Slice-1 fix — was `os.path.abspath`, which
    pinned the path to the capturing machine and orphaned the backlog in the cloud.)"""
    return {
        "id": os.path.splitext(os.path.basename(payload_path))[0],   # slug (queue id)
        "source": source,
        "kb": kb,
        "stage": "captured",
        "conflict_key": None,
        "lane": None,
        "claimed_by": None,
        "claimed_at": None,
        "payload_path": payload_path,        # vault-relative, forward-slashed (portable)
        "captured_utc": mtime_iso,
        "history": [{"ts": now_iso, "stage": "captured"}],
    }


# ─────────────────────────── discovery ───────────────────────────
def discover_session_records(sessions_dir):
    """List session-records via session_synth.py (reuse, don't reimplement the type filter)."""
    if not os.path.isdir(sessions_dir):
        return []
    tool = os.path.join(HERE, "session_synth.py")
    try:
        out = subprocess.run([sys.executable, tool, "records", sessions_dir],
                             capture_output=True, text=True, timeout=120)
    except Exception:
        return []
    if out.returncode != 0 or not out.stdout.strip():
        return []
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return []


def _recent_inbox_files(inbox_dir, cutoff_epoch):
    """cutoff_epoch=None -> no mtime window (unbounded; the dedupe ledger is the fence)."""
    if not os.path.isdir(inbox_dir):
        return []
    hits = []
    for p in glob.glob(os.path.join(inbox_dir, "**", "*.md"), recursive=True):
        try:
            if cutoff_epoch is None or os.path.getmtime(p) >= cutoff_epoch:
                hits.append(p)
        except OSError:
            continue
    return hits


def _source_of(inbox_dir, path):
    """The immediate subfolder under raw/inbox/ (gmail|x|chrome|github|youtube|…)."""
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(inbox_dir))
    parts = rel.replace(os.sep, "/").split("/")
    return parts[0] if len(parts) > 1 else "inbox"


# ─────────────────────────── scan (read-only) ───────────────────────────
def scan(sources, ledger_path, vault_root, lookback_days, cap, now=None, queue_map=None):
    """Pure, read-only. `sources` = [{"kb","inbox_dir","sessions_dir"}]. Returns a dict with
    new_items (queue items, newest-first, capped), new_ids/new_urls (ledger deltas), stats."""
    now = now or datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    # lookback <= 0 / None = UNBOUNDED (H35): a window is a *forgetting* boundary — a file that
    # misses every windowed run becomes permanently invisible. The ledger fences re-capture.
    cutoff = None if not lookback_days or lookback_days <= 0 else now.timestamp() - lookback_days * 86400

    ledger = _read_json(ledger_path) or {"ids": [], "urls": []}
    seen_ids = set(ledger.get("ids", []))
    seen_urls = set(ledger.get("urls", []))

    # gather candidates as (mtime, kind, kb, path, source, stable_id) — no file reads here;
    # the frontmatter read happens in the fence loop, only for items that can still enqueue
    cands = []
    scanned = 0
    for src in sources:
        kb = src["kb"]
        inbox = src.get("inbox_dir")
        # inbox raws
        for p in _recent_inbox_files(inbox, cutoff):
            scanned += 1
            try:
                mt = os.path.getmtime(p)
            except OSError:
                continue   # vanished between glob and stat — a later run picks it up
            cands.append((mt, "raw", kb, p, _source_of(inbox, p), _rel(p, vault_root)))
        # session records
        for rec in discover_session_records(src.get("sessions_dir", "")):
            scanned += 1
            f = rec.get("file")
            if not f:
                continue
            try:
                mt = os.path.getmtime(f)
            except OSError:
                continue
            cands.append((mt, "session", kb, f, "session", _rel(f, vault_root)))

    # NEWEST-first: fresh captures must never starve behind a historical backlog drain — the
    # backlog fills whatever cap headroom is left and drains newest->oldest across runs.
    cands.sort(key=lambda c: c[0], reverse=True)

    new_items, new_ids, new_urls = [], [], []
    ledgered = dupes = backlog = queue_fenced = queue_collisions = 0
    batch_ids, batch_urls = set(), set()
    for mtime, kind, kb, path, source, stable in cands:
        qid = os.path.splitext(os.path.basename(stable))[0]   # the prospective queue id (file stem)
        # ID fence — ledgered = the expected steady-state background, counted separately so
        # dupes_skipped stays a meaningful anomaly signal in the context log
        if stable in seen_ids:
            ledgered += 1
            continue
        if stable in batch_ids:
            dupes += 1
            continue
        # A29: queue backstop fence + ledger self-heal. An add-succeeded-then-ledger-failed run
        # leaves the item in the queue but not the ledger; rebuilding it would make queue_tx add
        # hard-refuse on the existing id every run forever. Fence ONLY on an exact identity match
        # — same queue id (stem) AND same payload_path (== this stable id) — and ride the stable
        # id onto the ledger delta so the tear heals itself. A stem-only match (two different
        # files sharing a slug) deliberately falls through to enqueue, where queue_tx add refuses
        # LOUD — never silently ledger a file that was never captured.
        if queue_map is not None and queue_map.get(qid) == stable:
            queue_fenced += 1
            new_ids.append(stable)
            batch_ids.add(stable)
            # heal the URL half of the torn delta too, else the same page under a different
            # filename would later pass the URL fence and enqueue as a duplicate
            if kind == "raw":
                heal_url = normalize_url(_frontmatter(_read_text(path) or "").get("url", ""))
                if heal_url and heal_url not in seen_urls and heal_url not in batch_urls:
                    new_urls.append(heal_url)
                    batch_urls.add(heal_url)
            continue
        # A54: session queue-id collision. A session record's queue id IS its globally-unique
        # session id, so a stem match against a queue item with a DIFFERENT (or absent-from-ledger)
        # payload_path is the SAME record — its file was re-discovered at a moved path (an env
        # rename like H26 03_Dev->03_GeneralManagement) or was never ledgered. Left alone it falls
        # through to enqueue and queue_tx add HARD-REFUSES the duplicate id, aborting the whole run
        # (the recurring gather-time wedge). It reaches here only past the exact A29 fence, so the
        # payload_path genuinely differs. Dedupe: skip the re-enqueue and heal the ledger with the
        # CURRENT stable id so it fences cleanly next run. Scoped to sessions BY DESIGN — a raw slug
        # is not a unique identity, so a raw stem-clash keeps the loud never-silently-ledger path.
        if kind == "session" and queue_map is not None and qid in queue_map:
            queue_collisions += 1
            new_ids.append(stable)
            batch_ids.add(stable)
            continue
        if len(new_items) >= cap:
            backlog += 1   # un-ledgered candidate beyond cap — deferred, no read spent on it
            continue
        # URL fence — raws only; frontmatter read only for items that can still enqueue,
        # so per-run reads are bounded by ~cap regardless of backlog size
        nurl = ""
        if kind == "raw":
            nurl = normalize_url(_frontmatter(_read_text(path) or "").get("url", ""))
            if nurl and (nurl in seen_urls or nurl in batch_urls):
                dupes += 1
                # A26: ledger the rejected dupe's stable id too — the ledger means "resolved,
                # never re-consider", not "enqueued" (ids are a SUPERSET of queue ids). Without
                # this the file is frontmatter-read every run forever and, beyond the cap,
                # inflates backlog_remaining.
                new_ids.append(stable)
                batch_ids.add(stable)
                continue
        new_items.append(build_item(stable, source, kb, now_iso,
                                    datetime.fromtimestamp(mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
        new_ids.append(stable)
        batch_ids.add(stable)
        if kind == "raw" and nurl:
            new_urls.append(nurl)
            batch_urls.add(nurl)

    return {
        "new_items": new_items,
        "new_ids": new_ids,
        "new_urls": new_urls,
        "stats": {
            "scanned": scanned,
            "enqueued": len(new_items),
            "ledgered": ledgered,
            "queue_fenced": queue_fenced,
            "queue_collisions": queue_collisions,
            "dupes_skipped": dupes,
            "capped": backlog > 0,
            "backlog_remaining": backlog,
        },
    }


# ─────────────────────────── reconcile (A54 heal) ───────────────────────────
def heal_ledger(queue_path, ledger_path):
    """Back-fill the capture ledger with any live-queue item whose stable id (payload_path) is
    absent from the ledger — the reconcile heal for the A54 queue<->ledger id-scheme desync. An
    item already IN the queue is BY DEFINITION already captured; ledgering its payload_path fences
    it so a later scan can't re-enqueue it and trip queue_tx add's hard id-fence (the recurring
    gather-time wedge). Unlike the scan-time dedupe this heals the STANDING backlog in one pass and
    needs no file on disk. Idempotent + atomic (append-only; never truncates). Prints a JSON
    summary; exit 0 (mechanical, reconstructable-from-source — fix-then-tell)."""
    qd = _read_json(queue_path)
    items = qd.get("queue", []) if isinstance(qd, dict) else []
    ledger = _read_json(ledger_path) or {"ids": [], "urls": []}
    ledger.setdefault("ids", []); ledger.setdefault("urls", [])
    have = set(ledger["ids"])
    added = []
    for it in items:
        pp = it.get("payload_path") if isinstance(it, dict) else None
        if pp and pp not in have:
            ledger["ids"].append(pp); have.add(pp); added.append(pp)
    if added and not _write_json_atomic(ledger_path, ledger):
        print(json.dumps({"op": "heal-ledger", "ok": False, "error": "ledger write failed"}))
        return 1
    print(json.dumps({"op": "heal-ledger", "ok": True, "healed": len(added), "ids": added[:20]},
                     ensure_ascii=False, indent=2))
    return 0


def _queue_map(queue_path):
    """A29: id -> payload_path over the live queue (read-only; atomic-write means a reader sees
    old-or-new, never torn). None when the queue is absent/unreadable — the fence then disables
    and behavior degrades to the pre-A29 loud path."""
    qd = _read_json(queue_path)
    if isinstance(qd, dict) and isinstance(qd.get("queue"), list):
        return {it.get("id"): it.get("payload_path")
                for it in qd["queue"] if isinstance(it, dict)}
    return None


# ─────────────────────────── run (scan + commit) ───────────────────────────
def run(sources, queue_path, ledger_path, vault_root, lookback_days, cap, context_log=None):
    now = datetime.now(timezone.utc)
    result = scan(sources, ledger_path, vault_root, lookback_days, cap, now=now,
                  queue_map=_queue_map(queue_path))
    items, new_ids, new_urls = result["new_items"], result["new_ids"], result["new_urls"]
    stats = result["stats"]
    run_id = now.strftime("%Y-%m-%d")

    summary = {"stage": "inbox-capture", "run_id": run_id, "ok": True, **stats, "queue_total": None}

    if items:
        # 1) enqueue via queue_tx add (dedupe-fenced, validated, atomic) — never a raw write.
        newitems_path = queue_path + ".new-items.json"
        if not _write_json_atomic(newitems_path, items):
            summary.update(ok=False, error="could not stage new-items file")
            _emit(summary, context_log, now); return 1
        add = subprocess.run([sys.executable, os.path.join(HERE, "queue_tx.py"), "add",
                              queue_path, newitems_path], capture_output=True, text=True)
        try:
            os.remove(newitems_path)
        except OSError:
            pass
        if add.returncode != 0:
            summary.update(ok=False, error="queue_tx add rejected: " + (add.stderr or add.stdout).strip()[:300])
            _emit(summary, context_log, now); return 1
        # queue_tx prints "queue now N items" — capture it for the notification
        m = re.search(r"queue now (\d+) items", add.stdout)
        summary["queue_total"] = int(m.group(1)) if m else None

    # 2) ONLY after a successful add (when there was one): append the ledger (append-only; never
    #    truncate). Runs OUTSIDE `if items` — a run whose only yield is URL-dupe ledger deltas
    #    (A26: new_ids without new_items) must still persist them or the dupes re-read forever.
    if new_ids or new_urls:
        ledger = _read_json(ledger_path) or {"ids": [], "urls": []}
        ledger.setdefault("ids", []); ledger.setdefault("urls", [])
        ledger["ids"].extend(new_ids)
        ledger["urls"].extend(new_urls)
        if not _write_json_atomic(ledger_path, ledger):
            # Self-healing either way (A29): a dupe-only delta is simply re-detected next run;
            # enqueued-but-unledgered items are caught by the queue-ids backstop fence, which
            # skips the re-enqueue and rides their stable ids back onto the ledger delta.
            summary.update(ok=False, error=(
                "LEDGER WRITE FAILED after enqueue — the next run's queue backstop fence "
                "re-ledgers the enqueued items (self-heals)" if items else
                "LEDGER WRITE FAILED (dupe-only delta) — self-heals on the next run"))
            _emit(summary, context_log, now); return 1

    # 3) VERIFY (Stage Contract #3): queue folds + validates clean.
    val = subprocess.run([sys.executable, os.path.join(HERE, "queue_tx.py"), "validate", queue_path],
                         capture_output=True, text=True)
    if val.returncode != 0:
        summary.update(ok=False, error="post-add validate failed: " + (val.stdout or val.stderr).strip()[:200])
        _emit(summary, context_log, now); return 1

    _emit(summary, context_log, now)
    return 0


def _emit(summary, context_log, now):
    if context_log:
        line = {"ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "stage": "inbox-capture",
                "run_id": summary["run_id"], "items_in": summary.get("scanned", 0),
                "items_out": summary.get("enqueued", 0), "skipped_dupe": summary.get("dupes_skipped", 0),
                "repairs": ([f"deduped+ledger-healed {summary['queue_collisions']} session id-collision(s)"]
                            if summary.get("queue_collisions") else []),
                "anomalies": [] if summary["ok"] else ["see error"],
                "note": summary.get("error", "ok") + (f"; backlog {summary['backlog_remaining']}"
                        if summary.get("capped") else "")}
        try:
            ctxlog.emit(line, context_log)
            summary["context_log_ok"] = True
        except (ctxlog.ContextLogWriteError, OSError) as e:
            # The enqueue already succeeded and was validated; the context-log is a secondary
            # honesty record, so a failed/torn append must not fail the run — but surface it
            # loudly (was a silent `except OSError: pass`, which is how a torn line once hid).
            # Signal it in BOTH the stdout summary the scheduler parses AND stderr, so an
            # unattended run can't report a clean `ok` while the honesty line was lost.
            summary["context_log_ok"] = False
            summary["context_log_error"] = str(e)
            print("WARNING: context-log emit failed: " + str(e), file=sys.stderr)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


# ─────────────────────────── CLI ───────────────────────────
def _parse_sources(kb_args, vault_root):
    """--kb dev=03_Dev -> {kb:'dev', inbox_dir:.../03_Dev/raw/inbox, sessions_dir:.../raw/sessions}."""
    out = []
    for spec in kb_args:
        if "=" not in spec:
            sys.exit(f"--kb expects kb=folder, got {spec!r}")
        kb, folder = spec.split("=", 1)
        base = os.path.join(vault_root, folder, "raw")
        out.append({"kb": kb, "inbox_dir": os.path.join(base, "inbox"),
                    "sessions_dir": os.path.join(base, "sessions")})
    return out


def _add_common(ap):
    ap.add_argument("--queue", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--vault-root", required=True, help="resolved live vault base (e.g. ...\\vault)")
    ap.add_argument("--kb", action="append", default=[], required=True,
                    help="kb=folder, repeatable (e.g. --kb dev=03_Dev --kb personal=01_Personal)")
    ap.add_argument("--lookback-days", type=int, default=0,
                    help="mtime window in days; 0 (default) = unbounded — the dedupe ledger, "
                         "not a time window, decides what is new (H35: windows lose files)")
    ap.add_argument("--cap", type=int, default=50)


from _util import utf8_stdio as _utf8_stdio


def main(argv=None):
    _utf8_stdio()
    ap = argparse.ArgumentParser(description="aios inbox-capture-stage deterministic enqueuer.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("scan", help="read-only; print new items + ledger delta")
    _add_common(sp)
    rp = sub.add_parser("run", help="scan + queue_tx add + ledger append + validate")
    _add_common(rp)
    rp.add_argument("--context-log")
    hp = sub.add_parser("heal-ledger",
                        help="A54 reconcile: ledger every in-queue payload_path (clears the "
                             "session id-collision backlog without a capture run)")
    hp.add_argument("--queue", required=True)
    hp.add_argument("--ledger", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "heal-ledger":
        return heal_ledger(args.queue, args.ledger)
    sources = _parse_sources(args.kb, args.vault_root)
    if args.cmd == "scan":
        # same queue_map as run(), so the read-only preview matches what run would do (A29)
        print(json.dumps(scan(sources, args.ledger, args.vault_root,
                              args.lookback_days, args.cap,
                              queue_map=_queue_map(args.queue)),
                         ensure_ascii=False, indent=2))
        return 0
    return run(sources, args.queue, args.ledger, args.vault_root,
               args.lookback_days, args.cap, context_log=args.context_log)


if __name__ == "__main__":
    sys.exit(main())
# cache-revalidation touch — safe to remove
