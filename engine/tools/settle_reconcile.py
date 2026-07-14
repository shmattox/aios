#!/usr/bin/env python3
"""settle_reconcile.py — deterministic auto-heal of brief decisions whose Notion write never landed.

A prior brief decision may record executed=True with an intended notion_write, yet the flip may
have failed to land (no changelog receipt). Those have a KNOWN target, so we replay them
(fix-then-tell). Inferred completions are NOT handled here — they wait for at-desk confirm.
"""
import argparse, json, os, subprocess, sys, time
import brief_session, context_log

_HERE = os.path.dirname(os.path.abspath(__file__))

STAGE = "settle"

def find_unlanded_writes(decisions, changelog_rows):
    """Executed decisions with a notion_write intent that has no matching (page_id, field, new) receipt."""
    landed = {(r.get("page_id"), r.get("field"), r.get("new")) for r in changelog_rows}
    out = []
    for d in decisions:
        if not d.get("executed"):
            continue
        nw = d.get("notion_write")
        if not nw:
            continue
        key = (nw.get("page_id"), nw.get("field"), nw.get("to"))
        if key not in landed:
            out.append({"item_id": d.get("item_id"), "title": d.get("title"),
                        "page_id": nw.get("page_id"), "field": nw.get("field"), "to": nw.get("to")})
    return out

def _flip(env_root, row, writable, change_log):
    cmd = [sys.executable, os.path.join(_HERE, "notion_writeback.py"),
           "flip", "--page", row["page_id"], "--field", row["field"], "--to", row["to"],
           "--change-log", change_log, "--by", "aios-settle", "--run-id", time.strftime("%Y-%m-%d")]
    for w in writable:
        cmd += ["--writable", w]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return {"rc": p.returncode, "out": p.stdout.strip(), "err": p.stderr.strip()}

def run(env_root, decisions=None, changelog_rows=None, writable=None, dry_run=False):
    state = os.path.join(env_root, "state")
    if decisions is None:
        ledger = brief_session.load(os.path.join(state, "brief-session.json")) or {}
        decisions = ledger.get("decisions", [])
    if changelog_rows is None:
        cl = os.path.join(state, "notion-changelog.jsonl")
        changelog_rows = []
        if os.path.exists(cl):
            with open(cl, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        changelog_rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue    # skip a torn/malformed line — recovering from prior failure is the job
    unlanded = find_unlanded_writes(decisions, changelog_rows)
    healed = []
    for row in unlanded:
        if dry_run:
            healed.append({**row, "receipt": None, "dry_run": True})
        else:
            res = _flip(env_root, row, writable or [], os.path.join(state, "notion-changelog.jsonl"))
            if res["rc"] == 0:
                healed.append({**row, "receipt": res["out"]})
    return {"auto_healed": healed, "unlanded_found": len(unlanded)}

def main(argv=None):
    ap = argparse.ArgumentParser(description="Deterministic settle auto-heal (unlanded brief writes).")
    ap.add_argument("--env-root", required=True)
    ap.add_argument("--writable", action="append", default=[])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-context-log", action="store_true")
    args = ap.parse_args(argv)
    res = run(args.env_root, writable=args.writable, dry_run=args.dry_run)
    if not args.no_context_log:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "stage": STAGE,
               "skill": "aios-settle", "healed": len(res["auto_healed"]),
               "unlanded_found": res["unlanded_found"]}
        try:
            context_log.emit(rec, os.path.join(args.env_root, "state", "context-log.jsonl"))
        except Exception:
            pass
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    sys.exit(main())
