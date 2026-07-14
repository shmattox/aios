#!/usr/bin/env python3
"""A54 — gather-time capture must not ABORT on a session-id fence collision.

The recurring pipeline wedge (2026-07-09/07-10 context-log `capture ABORTED non-zero`):
a session record is already in the queue (queue id = the session's globally-unique id), its
file is re-discovered at a DIFFERENT payload_path (an env rename like H26 03_Dev->03_GeneralManagement
moved it) or was never ledgered, so neither the capture ledger fence (keyed on payload_path) nor
the A29 exact-identity fence catches it. It falls through to enqueue and `queue_tx.py add`
hard-refuses the duplicate id -> the WHOLE capture run aborts non-zero, wedging the pipeline
every run.

A session stem IS a unique session id, so a stem collision is the SAME record, not a coincidental
slug clash (the deliberate raw behaviour). Fix: dedupe the session collision (skip re-enqueue, heal
the ledger with the current path) so capture completes; a `heal-ledger` reconcile back-fills the
standing unfenced-session backlog; raws keep the loud never-silently-ledger contract.

Standalone; run: python tools/tests/test_a54_capture_id_collision.py"""
import json, os, sys, tempfile, shutil
from datetime import datetime, timezone

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

def session_record(sid, kb="dev", date="2026-07-06"):
    return (f"---\ntype: session-record\nid: {sid}\ndomain: {kb}\n"
            f"conflict_key: {kb}/wiki/journal/{date}.md\n---\nFocus: x\nOutcome: y\nWhy: z\n")

SID = "claude-code-2026-07-06-c322828c"
NEW_STABLE = "03_Dev/raw/sessions/" + SID + ".md"          # where the file lives now (discovered)
OLD_PAYLOAD = "03_GeneralManagement/raw/sessions/" + SID + ".md"  # the in-queue item's stale path

d = tempfile.mkdtemp(prefix="a54_")
try:
    vault = os.path.join(d, "Vault")
    devsess = os.path.join(vault, "03_Dev", "raw", "sessions")
    devin = os.path.join(vault, "03_Dev", "raw", "inbox")
    sources = [{"kb": "dev", "inbox_dir": devin, "sessions_dir": devsess}]
    now = datetime.now(timezone.utc)

    write(os.path.join(devsess, SID + ".md"), session_record(SID))

    # ── scan: a session stem collision (same unique id, different queued payload_path) is DEDUPED,
    #    not fallen-through-to-enqueue (which would trip queue_tx add's hard fence and abort) ──
    ledger = os.path.join(d, "ledger.json")
    json.dump({"ids": [], "urls": []}, open(ledger, "w", encoding="utf-8"))
    r = capture.scan(sources, ledger, vault, lookback_days=0, cap=50, now=now,
                     queue_map={SID: OLD_PAYLOAD})
    check("A54: session id-collision NOT re-enqueued (deduped, no abort at add)",
          all(i["id"] != SID for i in r["new_items"]))
    check("A54: deduped session's CURRENT stable id rides the ledger delta (heal)",
          NEW_STABLE in r["new_ids"])
    check("A54: session id-collision counted in stats (visible, not silent)",
          r["stats"].get("queue_collisions", 0) == 1)

    # ── run() end-to-end: the exact wedge — queue already holds the session id, ledger empty.
    #    Current code aborts (queue_tx add refuses the dup id); the fix exits 0 and heals. ──
    wq, wl = os.path.join(d, "wedge-q.json"), os.path.join(d, "wedge-l.json")
    queued = capture.build_item(OLD_PAYLOAD, "session", "dev", "2026-07-06T00:00:00Z", "2026-07-06T00:00:00Z")
    check("precondition: the queued item's id is the session id (stem)", queued["id"] == SID)
    json.dump({"queue": [queued]}, open(wq, "w", encoding="utf-8"))
    json.dump({"ids": [], "urls": []}, open(wl, "w", encoding="utf-8"))
    rc = capture.run(sources, wq, wl, vault, lookback_days=0, cap=50)
    check("A54: wedge run exits 0 (no hard-fail at queue_tx add)", rc == 0)
    healed = json.load(open(wl, encoding="utf-8"))
    check("A54: wedge run heals the ledger with the re-discovered session path",
          NEW_STABLE in healed["ids"])
    wq_after = json.load(open(wq, encoding="utf-8"))
    check("A54: no double-enqueue (queue still holds exactly one item for the session id)",
          sum(1 for i in wq_after["queue"] if i["id"] == SID) == 1)
    # the heal is VISIBLE in the context-log (a repair, not silent) — re-run the wedge with a log
    clog = os.path.join(d, "ctx.jsonl")
    json.dump({"queue": [queued]}, open(wq, "w", encoding="utf-8"))
    json.dump({"ids": [], "urls": []}, open(wl, "w", encoding="utf-8"))
    capture.run(sources, wq, wl, vault, lookback_days=0, cap=50, context_log=clog)
    logged = [json.loads(ln) for ln in open(clog, encoding="utf-8") if ln.strip()]
    check("A54: the session id-collision heal is logged as a repair (visible, not silent)",
          any("session id-collision" in r for rec in logged for r in rec.get("repairs", [])))

    # next run is a clean no-op (the heal now fences it)
    rc2 = capture.run(sources, wq, wl, vault, lookback_days=0, cap=50)
    check("A54: next run is a clean no-op (healed ledger fences the session)", rc2 == 0)

    # ── reconcile: heal-ledger back-fills any in-queue payload_path missing from the ledger
    #    (heals the standing unfenced-session backlog without running a capture) ──
    hq, hl = os.path.join(d, "heal-q.json"), os.path.join(d, "heal-l.json")
    a = capture.build_item("03_GeneralManagement/raw/sessions/s-aaa.md", "session", "gm", "t", "t")
    b = capture.build_item("03_GeneralManagement/raw/inbox/x/b.md", "x", "gm", "t", "t")
    json.dump({"queue": [a, b]}, open(hq, "w", encoding="utf-8"))
    json.dump({"ids": ["03_GeneralManagement/raw/inbox/x/b.md"], "urls": []},  # only b is ledgered
              open(hl, "w", encoding="utf-8"))
    hrc = capture.heal_ledger(hq, hl)
    healed2 = json.load(open(hl, encoding="utf-8"))
    check("A54: heal-ledger exits 0", hrc == 0)
    check("A54: heal-ledger back-fills the unledgered in-queue item",
          "03_GeneralManagement/raw/sessions/s-aaa.md" in healed2["ids"])
    check("A54: heal-ledger does not duplicate an already-ledgered id",
          healed2["ids"].count("03_GeneralManagement/raw/inbox/x/b.md") == 1)
    # idempotent
    capture.heal_ledger(hq, hl)
    healed3 = json.load(open(hl, encoding="utf-8"))
    check("A54: heal-ledger is idempotent (a second run adds nothing)",
          len(healed3["ids"]) == len(healed2["ids"]))

    # ── REGRESSION: a RAW stem-collision with a DIFFERENT file MUST NOT be silently deduped —
    #    a raw slug is not a unique identity; it stays loud (never silently ledger a never-captured
    #    file). Preserves the A29 CRITICAL-reviewed contract. ──
    write(os.path.join(devin, "x", "2026-06-21-gamma.md"), "---\nsource: x\nurl: https://x.com/gamma\n---\nbody\n")
    json.dump({"ids": [], "urls": []}, open(ledger, "w", encoding="utf-8"))
    r_raw = capture.scan(sources, ledger, vault, lookback_days=0, cap=50, now=now,
                         queue_map={"2026-06-21-gamma": "01_Personal/raw/inbox/gmail/2026-06-21-gamma.md"})
    check("A54: raw stem-collision (different file) still NOT deduped/ledgered (loud, unchanged)",
          any(i["id"] == "2026-06-21-gamma" for i in r_raw["new_items"])
          and r_raw["stats"].get("queue_collisions", 0) == 0)

finally:
    shutil.rmtree(d, ignore_errors=True)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
sys.exit(1 if FAIL else 0)
