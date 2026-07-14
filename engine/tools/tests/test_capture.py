#!/usr/bin/env python3
"""capture.py test harness — exercises the deterministic enqueuer in an isolated temp vault.
Scratch; safe to delete. Run: python tools/tests/test_capture.py"""
import json, os, sys, glob, tempfile, shutil
from datetime import datetime, timezone, timedelta

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../engine/tools
sys.path.insert(0, HARNESS)
import capture

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ok  " if cond else " FAIL ") + name)

def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

def raw(url=None):
    fm = "---\nsource: x\n" + (f"url: {url}\n" if url else "") + "---\nbody\n"
    return fm

def session_record(sid, kb="dev", date="2026-06-21"):
    return (f"---\ntype: session-record\nid: {sid}\ndomain: {kb}\n"
            f"conflict_key: {kb}/wiki/journal/{date}.md\n---\nFocus: x\nOutcome: y\nWhy: z\n")

def session_record_quoted(sid, kb="dev", date="2026-06-25"):
    """The NEWER writer's frontmatter: every scalar is a quoted YAML string. The discovery
    filter must be quote-tolerant or these are silently dropped (BACKLOG ON1 data loss)."""
    return (f'---\ntype: "session-record"\nsource: "claude-code"\nid: "{sid}"\n'
            f'domain: "{kb}"\nkb: "{kb}"\nstarted: "{date}T19:00:00.000Z"\n'
            f'ended: "{date}T21:00:00.000Z"\ndate: "{date}"\n'
            f'conflict_key: "{kb}/wiki/journal/{date}.md"\n---\nFocus: x\nOutcome: y\nWhy: z\n')

# ─────────────────────────── pure: normalize_url ───────────────────────────
nu = capture.normalize_url
check("normalize strips scheme+www+trailing slash",
      nu("https://www.github.com/a/b/") == "github.com/a/b")
check("normalize drops utm_/ref/fbclid",
      nu("https://github.com/a/b?utm_source=x&ref=y&fbclid=z") == "github.com/a/b")
check("normalize keeps a meaningful query param",
      nu("https://x.com/i?id=42&utm_medium=q") == "x.com/i?id=42")
check("normalize lowercases host only, not path",
      nu("https://GitHub.com/Owner/Repo") == "github.com/Owner/Repo")
check("normalize empty -> ''", nu("") == "" and nu(None) == "")

d = tempfile.mkdtemp(prefix="capture_")
try:
    vault = os.path.join(d, "Vault")
    devin = os.path.join(vault, "03_Dev", "raw", "inbox")
    devsess = os.path.join(vault, "03_Dev", "raw", "sessions")
    queue = os.path.join(d, "queue.json")
    ledger = os.path.join(d, "captured-ids.json")
    sources = [{"kb": "dev", "inbox_dir": devin, "sessions_dir": devsess}]
    now = datetime.now(timezone.utc)

    # ── fresh inbox raws ──
    write(os.path.join(devin, "x", "2026-06-21-alpha.md"), raw("https://github.com/a/alpha"))
    write(os.path.join(devin, "github", "2026-06-21-beta.md"), raw("https://news.example.com/beta"))
    r = capture.scan(sources, ledger, vault, lookback_days=3, cap=50, now=now)
    check("scan finds both fresh raws", r["stats"]["enqueued"] == 2)
    item = next(i for i in r["new_items"] if i["id"] == "2026-06-21-alpha")
    check("item id is the file stem", item["id"] == "2026-06-21-alpha")
    check("item source is the inbox subfolder", item["source"] == "x")
    check("item kb from source mapping", item["kb"] == "dev")
    check("item stage=captured, conflict_key=null, lane=null",
          item["stage"] == "captured" and item["conflict_key"] is None and item["lane"] is None)
    check("item payload_path is vault-RELATIVE (portable), forward-slashed, resolves under vault-root",
          item["payload_path"] == "03_Dev/raw/inbox/x/2026-06-21-alpha.md"
          and not os.path.isabs(item["payload_path"])
          and os.path.exists(os.path.join(vault, item["payload_path"])))
    check("ledger id is path-relative to vault root (not the slug)",
          "03_Dev/raw/inbox/x/2026-06-21-alpha.md" in r["new_ids"])

    # ── ID fence: pre-seed ledger with alpha's stable id ──
    json.dump({"ids": ["03_Dev/raw/inbox/x/2026-06-21-alpha.md"], "urls": []},
              open(ledger, "w", encoding="utf-8"))
    r2 = capture.scan(sources, ledger, vault, lookback_days=3, cap=50, now=now)
    check("ID fence skips already-captured raw (counted as ledgered, not dupe)",
          r2["stats"]["enqueued"] == 1 and r2["stats"]["ledgered"] == 1 and r2["stats"]["dupes_skipped"] == 0)

    # ── URL fence: same normalized url via a different path/source ──
    write(os.path.join(devin, "chrome", "2026-06-21-alpha-dup.md"), raw("http://github.com/a/alpha/?utm_source=t"))
    json.dump({"ids": [], "urls": ["github.com/a/alpha"]}, open(ledger, "w", encoding="utf-8"))
    r3 = capture.scan(sources, ledger, vault, lookback_days=3, cap=50, now=now)
    enq_urls = [i["id"] for i in r3["new_items"]]
    check("URL fence drops the same page from another source",
          "2026-06-21-alpha" not in enq_urls and "2026-06-21-alpha-dup" not in enq_urls)

    # ── A26: a URL-dupe's stable id is LEDGERED at fence time (resolved, never re-consider) ──
    # Without this, an un-ledgered URL-dupe file is frontmatter-read every run forever and,
    # beyond the cap, inflates backlog_remaining. Its id must ride the ledger delta even
    # though it is NOT enqueued (ledger ids are a superset of queue ids by design).
    dup_stable = "03_Dev/raw/inbox/chrome/2026-06-21-alpha-dup.md"
    check("A26: URL-dupe stable id in the ledger delta, item NOT enqueued",
          dup_stable in r3["new_ids"]
          and all(i["id"] != "2026-06-21-alpha-dup" for i in r3["new_items"]))
    # second scan with that delta applied (all three files ledgered): the dupe is ID-fenced —
    # zero frontmatter re-reads, zero dupe-skips, nothing enqueued
    json.dump({"ids": [dup_stable,
                       "03_Dev/raw/inbox/x/2026-06-21-alpha.md",
                       "03_Dev/raw/inbox/github/2026-06-21-beta.md"],
               "urls": ["github.com/a/alpha"]},
              open(ledger, "w", encoding="utf-8"))
    r3b = capture.scan(sources, ledger, vault, lookback_days=3, cap=50, now=now)
    check("A26: second scan ID-fences the URL-dupe (ledgered, not dupe-skipped)",
          r3b["stats"]["dupes_skipped"] == 0 and r3b["stats"]["ledgered"] == 3
          and r3b["stats"]["enqueued"] == 0)

    # ── A29: queue backstop fence (id -> payload_path map) — heal the enqueued-but-unledgered wedge ──
    # An add-succeeded-then-ledger-write-failed run leaves items in the queue that the ledger
    # doesn't know; the next scan would rebuild them and queue_tx add would hard-refuse forever.
    # With queue_ids passed, the candidate is fenced AND its stable id rides the delta (heal).
    write(os.path.join(devin, "x", "2026-06-21-gamma.md"), raw("https://x.com/gamma"))
    json.dump({"ids": [], "urls": []}, open(ledger, "w", encoding="utf-8"))
    GAMMA_STABLE = "03_Dev/raw/inbox/x/2026-06-21-gamma.md"
    r_q = capture.scan(sources, ledger, vault, lookback_days=3, cap=50, now=now,
                       queue_map={"2026-06-21-gamma": GAMMA_STABLE})
    check("A29: queue-fenced candidate not re-enqueued",
          all(i["id"] != "2026-06-21-gamma" for i in r_q["new_items"]))
    check("A29: queue-fenced candidate's stable id rides the ledger delta (heal)",
          GAMMA_STABLE in r_q["new_ids"])
    check("A29: queue_fenced counted in stats", r_q["stats"].get("queue_fenced") == 1)
    check("A29: URL half of the torn delta healed too", "x.com/gamma" in r_q["new_urls"])
    # STEM COLLISION (review CRITICAL): a DIFFERENT file sharing the stem must NOT be fenced
    # or ledgered — it falls through to enqueue, where queue_tx add refuses loud (pre-A29
    # behavior for genuine collisions; never silently ledger a never-captured file)
    r_qc = capture.scan(sources, ledger, vault, lookback_days=3, cap=50, now=now,
                        queue_map={"2026-06-21-gamma": "01_Personal/raw/inbox/gmail/2026-06-21-gamma.md"})
    check("A29: stem-collision candidate (different payload_path) NOT fenced, NOT ledgered",
          any(i["id"] == "2026-06-21-gamma" for i in r_qc["new_items"])
          and r_qc["stats"].get("queue_fenced") == 0)
    # end-to-end wedge recovery: queue already holds gamma (the torn run's add landed),
    # ledger doesn't; run() must exit 0, not double-enqueue, and heal the ledger
    wedge_q = os.path.join(d, "wedge-queue.json")
    wedge_l = os.path.join(d, "wedge-ledger.json")
    gamma_item = capture.build_item("03_Dev/raw/inbox/x/2026-06-21-gamma.md", "x", "dev",
                                    "2026-06-21T00:00:00Z", "2026-06-21T00:00:00Z")
    json.dump({"queue": [gamma_item]}, open(wedge_q, "w", encoding="utf-8"))
    json.dump({"ids": [
        "03_Dev/raw/inbox/x/2026-06-21-alpha.md",
        "03_Dev/raw/inbox/github/2026-06-21-beta.md",
        "03_Dev/raw/inbox/chrome/2026-06-21-alpha-dup.md"], "urls": ["github.com/a/alpha"]},
        open(wedge_l, "w", encoding="utf-8"))
    rc = capture.run(sources, wedge_q, wedge_l, vault, lookback_days=3, cap=50)
    healed = json.load(open(wedge_l, encoding="utf-8"))
    wq = json.load(open(wedge_q, encoding="utf-8"))
    check("A29: wedge run exits 0 (no hard-fail at add)", rc == 0)
    check("A29: wedge run heals the ledger with the enqueued-but-unledgered id",
          "03_Dev/raw/inbox/x/2026-06-21-gamma.md" in healed["ids"])
    check("A29: no double-enqueue",
          sum(1 for i in wq["queue"] if i["id"] == "2026-06-21-gamma") == 1)
    os.remove(os.path.join(devin, "x", "2026-06-21-gamma.md"))

    # ── lookback excludes old files ──
    old = os.path.join(devin, "x", "2026-06-01-stale.md")
    write(old, raw("https://x.com/stale"))
    old_ts = (now - timedelta(days=10)).timestamp()
    os.utime(old, (old_ts, old_ts))
    json.dump({"ids": [], "urls": []}, open(ledger, "w", encoding="utf-8"))
    r4 = capture.scan(sources, ledger, vault, lookback_days=3, cap=50, now=now)
    check("lookback window excludes a 10-day-old raw",
          all("stale" not in i["id"] for i in r4["new_items"]))

    # ── unbounded default (H35): no mtime window — the ledger is "since last checked" ──
    # A file that missed every nightly window (e.g. task-down days, bulk import) must still be
    # captured on the next run; only the dedupe ledger decides "already seen".
    ancient = os.path.join(devin, "x", "2025-01-01-ancient.md")
    write(ancient, raw("https://x.com/ancient"))
    ancient_ts = (now - timedelta(days=400)).timestamp()
    os.utime(ancient, (ancient_ts, ancient_ts))
    json.dump({"ids": [], "urls": []}, open(ledger, "w", encoding="utf-8"))
    r4b = capture.scan(sources, ledger, vault, lookback_days=0, cap=50, now=now)
    check("lookback_days=0 (unbounded) captures a 400-day-old raw",
          any(i["id"] == "2025-01-01-ancient" for i in r4b["new_items"]))
    r4c = capture.scan(sources, ledger, vault, lookback_days=None, cap=50, now=now)
    check("lookback_days=None (unbounded) captures a 400-day-old raw",
          any(i["id"] == "2025-01-01-ancient" for i in r4c["new_items"]))
    # ledgered ancient file stays fenced on the next unbounded scan
    json.dump({"ids": ["03_Dev/raw/inbox/x/2025-01-01-ancient.md"], "urls": []},
              open(ledger, "w", encoding="utf-8"))
    r4d = capture.scan(sources, ledger, vault, lookback_days=0, cap=50, now=now)
    check("unbounded scan still ID-fences the ledgered ancient raw",
          all(i["id"] != "2025-01-01-ancient" for i in r4d["new_items"]))
    os.remove(ancient)

    # ── cap + backlog ──
    json.dump({"ids": [], "urls": []}, open(ledger, "w", encoding="utf-8"))
    r5 = capture.scan(sources, ledger, vault, lookback_days=3, cap=1, now=now)
    check("cap limits the batch", r5["stats"]["enqueued"] == 1 and r5["stats"]["capped"])
    check("backlog_remaining reported when capped", r5["stats"]["backlog_remaining"] >= 1)

    # ── freshness under cap (review finding): newest-first — a fresh capture must never
    # starve behind a large un-ledgered historical backlog draining at cap/run ──
    backlog_f = os.path.join(devin, "x", "2025-06-01-backlog-item.md")
    write(backlog_f, raw("https://x.com/backlog-item"))
    old_ts2 = (now - timedelta(days=200)).timestamp()
    os.utime(backlog_f, (old_ts2, old_ts2))
    fresh_f = os.path.join(devin, "x", "2026-07-05-fresh-today.md")
    write(fresh_f, raw("https://x.com/fresh-today"))
    json.dump({"ids": [], "urls": []}, open(ledger, "w", encoding="utf-8"))
    r5b = capture.scan(sources, ledger, vault, lookback_days=0, cap=1, now=now)
    check("newest-first: today's capture wins the cap slot over a 200-day backlog item",
          [i["id"] for i in r5b["new_items"]] == ["2026-07-05-fresh-today"])
    check("the backlog item is counted in backlog_remaining, not lost",
          r5b["stats"]["backlog_remaining"] >= 1)
    # backlog accuracy: ledgered files beyond the cap must not inflate backlog_remaining
    json.dump({"ids": ["03_Dev/raw/inbox/x/2026-06-21-alpha.md",
                       "03_Dev/raw/inbox/github/2026-06-21-beta.md",
                       "03_Dev/raw/inbox/chrome/2026-06-21-alpha-dup.md",
                       "03_Dev/raw/inbox/x/2026-06-01-stale.md"], "urls": []},
              open(ledger, "w", encoding="utf-8"))
    r5c = capture.scan(sources, ledger, vault, lookback_days=0, cap=1, now=now)
    check("ledgered files beyond the cap count as ledgered, not backlog",
          r5c["stats"]["backlog_remaining"] == 1 and r5c["stats"]["ledgered"] == 4)
    os.remove(backlog_f); os.remove(fresh_f)

    # ── session-record discovery: a record enqueues source=session; an evidence file is ignored ──
    write(os.path.join(devsess, "claude-code-2026-06-21-7805551e.md"), session_record("claude-code-2026-06-21-7805551e"))
    write(os.path.join(devsess, "activity-2026-06-21.md"), "---\ntype: activity\n---\nnot a record\n")
    json.dump({"ids": [], "urls": []}, open(ledger, "w", encoding="utf-8"))
    r6 = capture.scan(sources, ledger, vault, lookback_days=3650, cap=50, now=now)
    sess = [i for i in r6["new_items"] if i["source"] == "session"]
    check("session record discovered as source=session", len(sess) == 1)
    check("non-record evidence file is NOT enqueued",
          all("activity-" not in i["id"] for i in r6["new_items"]))

    # ── ON1 regression: a QUOTED-frontmatter record, newer than the latest ledger id, MUST enqueue ──
    # Reproduces the overnight silent data loss: newer session-capture writes `type:"session-record"`
    # (quoted), the discovery filter dropped it as not-a-record, so 06-25/06-26 records never reached
    # the queue while the ledger's newest stayed at the older unquoted record.
    write(os.path.join(devsess, "claude-code-2026-06-25-quoted01.md"), session_record_quoted("quoted01"))
    queue_on1, ledger_on1 = os.path.join(d, "queue_on1.json"), os.path.join(d, "ledger_on1.json")
    # ledger newest is the OLDER unquoted record -> the quoted 06-25 record is genuinely new
    json.dump({"ids": ["03_Dev/raw/sessions/claude-code-2026-06-21-7805551e.md"], "urls": []},
              open(ledger_on1, "w", encoding="utf-8"))
    r7 = capture.scan(sources, ledger_on1, vault, lookback_days=3650, cap=50, now=now)
    sess_ids = [i["id"] for i in r7["new_items"] if i["source"] == "session"]
    check("quoted-frontmatter session record IS discovered + enqueued (ON1)",
          "claude-code-2026-06-25-quoted01" in sess_ids)
    # end-to-end (isolated queue/ledger): after a run the newer record's stable id lands in the ledger
    rcq = capture.run(sources, queue_on1, ledger_on1, vault, lookback_days=3650, cap=50, context_log=None)
    ledq = json.load(open(ledger_on1, encoding="utf-8"))
    check("ON1 run enqueues the quoted record into the ledger",
          rcq == 0 and "03_Dev/raw/sessions/claude-code-2026-06-25-quoted01.md" in ledq["ids"])

    # ── run end-to-end: queue_tx add + ledger append + validate, then idempotent ──
    json.dump({"ids": [], "urls": []}, open(ledger, "w", encoding="utf-8"))
    rc = capture.run(sources, queue, ledger, vault, lookback_days=3650, cap=50, context_log=None)
    check("run exits 0", rc == 0)
    q_after = json.load(open(queue, encoding="utf-8"))
    check("run enqueued items via queue_tx (single-file store)", len(q_after["queue"]) >= 3)
    led = json.load(open(ledger, encoding="utf-8"))
    check("ledger appended after successful add", len(led["ids"]) >= 3)
    rc2 = capture.run(sources, queue, ledger, vault, lookback_days=3650, cap=50, context_log=None)
    q_after2 = json.load(open(queue, encoding="utf-8"))
    check("second run is a clean no-op (all dupes, no new items)",
          rc2 == 0 and len(q_after2["queue"]) == len(q_after["queue"]))

finally:
    shutil.rmtree(d, ignore_errors=True)

# A58: Windows long-path safety mirrored from capture_router. capture reads raw inbox artifacts
# via glob under {kb}/raw/inbox/**; a >260-char path made _read_text's bare open() return None
# (same latent bug as the router). _longpath (\\?\ prefix) lets the read succeed on Windows.
check("_longpath no-ops a short path", capture._longpath("x.md") == "x.md")
_lp = os.path.join(os.path.abspath(os.sep), "d" * 300, "f.md")
if os.name == "nt":
    check("_longpath prefixes a >260 absolute path on Windows", capture._longpath(_lp).startswith("\\\\?\\"))
    _dlp = tempfile.mkdtemp(prefix="capture_longpath_")
    try:
        _deep = os.path.join(_dlp, "03_Dev", "raw", "inbox", "webclipper")
        os.makedirs(_deep, exist_ok=True)
        _f = os.path.join(_deep, "Amanda Orson on X " + "z" * 210 + ".md")   # 231-char basename
        with open(capture._longpath(_f), "w", encoding="utf-8") as fh:
            fh.write("---\nsource: x\n---\nbody\n")
        check("_read_text reads a >260-char path (internal _longpath, not None)",
              capture._read_text(_f) is not None and "body" in capture._read_text(_f))
    finally:
        shutil.rmtree(capture._longpath(_dlp), ignore_errors=True)
else:
    check("_longpath is a no-op off Windows", capture._longpath(_lp) == _lp)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
sys.exit(1 if FAIL else 0)
