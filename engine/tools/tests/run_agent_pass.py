#!/usr/bin/env python3
"""G14b Half-2 driver — the LIVE agent-pass harness.

Sets up an isolated fixture install, then applies the two fresh-context agents' JSON decisions through
the SAME real helpers the plumbing test uses (queue_tx / rewind), and asserts the final state.

The agents (spawned separately, fresh context each) supply the JUDGMENT; this driver supplies the
mechanics. Never touches a real install's live queue — the fixture is a throwaway install under a scratch dir.

  python run_agent_pass.py setup        <fixture_dir>                 # build install + 3 raws; print them
  python run_agent_pass.py apply-draft  <fixture_dir> <draft.json>    # capture->sort->ingest from agent-1
  python run_agent_pass.py apply-review <fixture_dir> <review.json>   # ship/hold/reject from agent-2 + assert
"""
import json, os, sys, time, shutil, glob

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS)
import queue_tx, rewind, lane_policy

def _now(): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

# The fixture's explicit auto-ship policy. lane_policy's engine default is EMPTY (nothing auto-ships),
# so the driver MUST pass an explicit set — and its independent oracle uses the SAME set — or the two
# diverge from the empty default. This fixture opts `dev` in (the only auto-ship-lane raw is dev).
_FIXTURE_AUTO_SHIP = frozenset({"dev"})

# 3 synthetic raws — one per ship outcome (auto-ship/PASS, FO Paper-Governs/hold, dev security/hold).
RAWS = {
    "e2e-bun-runtime": ("bookmark", "dev", """# Bun 1.2 released
Bun 1.2 ships a built-in S3 client, ~90% Node.js compatibility, and a faster package installer.
A JavaScript runtime / toolkit. Reversible dev-tooling note."""),
    "e2e-bayview-bridge-term": ("email", "familyoffice", """# Bayview bridge loan — verbal lender offer
The lender VERBALLY indicated 8% interest-only for 18 months on a $1.75M bridge, pending a term sheet.
No executed document yet. Economic term, not papered."""),
    "e2e-supabase-advisory": ("email", "dev", """# Supabase security advisory
Supabase emailed a security advisory for the project: rotate your service_role key; a dependency CVE
was patched. Action item, security-sensitive."""),
}

def paths(fixture):
    install = os.path.join(fixture, "install")
    return {
        "install": install,
        "state": os.path.join(install, "state"),
        "vault": os.path.join(install, "vault"),
        "revert": os.path.join(install, "state", "revert"),
        "raw": os.path.join(install, "raw", "inbox"),
        "live": os.path.join(install, "state", "queue.json"),
    }

def staging_path(vault, kb, ck):
    return os.path.join(vault, kb, "wiki", "staging", os.path.splitext(os.path.basename(ck))[0] + ".md")

def log_line(state, **kw):
    kw.setdefault("ts", _now())
    with open(os.path.join(state, "context-log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(kw) + "\n")


def _assert_scratch(fixture):
    """Guard: only ever rmtree a path that is clearly a throwaway fixture (never a real install)."""
    low = os.path.abspath(fixture).replace("\\", "/").lower()
    if not any(m in low for m in ("e2e", "agentpass", "tmp", "temp", "fixture", "scratch")):
        sys.exit(f"refusing to wipe {fixture!r}: not a recognizable scratch/fixture path")

def setup(fixture):
    p = paths(fixture)
    if os.path.exists(fixture):
        _assert_scratch(fixture)
        shutil.rmtree(fixture, ignore_errors=True)
    for k in ("state", "vault", "revert", "raw"):
        os.makedirs(p[k], exist_ok=True)
    # inbox-capture: enqueue the 3 raws at stage=captured (additive, via queue_tx add)
    items = []
    for rid, (src, kb_hint, body) in RAWS.items():
        rp = os.path.join(p["raw"], rid + ".md")
        open(rp, "w", encoding="utf-8").write(body)
        items.append({"id": rid, "source": src, "stage": "captured", "payload_path": rp,
                      "captured_utc": _now(), "history": [{"ts": _now(), "stage": "captured"}]})
    itf = p["live"] + ".items"
    json.dump(items, open(itf, "w", encoding="utf-8"), indent=2)
    queue_tx._apply_items(p["live"], queue_tx._read_items(itf), "add")
    log_line(p["state"], stage="inbox-capture", run_id="agentpass", items_in=3, items_out=3, repairs=[], anomalies=[])
    print(json.dumps({"fixture": fixture, "captured": len(items),
                      "raws": {rid: {"source": s, "kb_hint": kb, "payload_path": os.path.join(p["raw"], rid + ".md"),
                                     "body": b} for rid, (s, kb, b) in RAWS.items()}}, indent=2))


def apply_draft(fixture, draft_json):
    """agent-1 output: [{id, kb, conflict_key, lane, recommended, rec_reason, draft_markdown}]"""
    p = paths(fixture)
    decisions = json.load(open(draft_json, encoding="utf-8"))
    by = {it["id"]: it for it in queue_tx.load(p["live"])["queue"]}
    sort_items, ing_items = [], []
    for dcn in decisions:
        cid = dcn["id"]; it = dict(by[cid])
        it.update(stage="sorted", kb=dcn["kb"], conflict_key=dcn["conflict_key"], lane=dcn["lane"])
        sort_items.append(json.loads(json.dumps(it)))
        # write the staging draft the agent authored
        sp = staging_path(p["vault"], dcn["kb"], dcn["conflict_key"])
        os.makedirs(os.path.dirname(sp), exist_ok=True)
        open(sp, "w", encoding="utf-8").write(dcn["draft_markdown"])
        it2 = dict(it)
        it2.update(stage="awaiting", first_drafted_utc=_now(),
                   recommended=dcn["recommended"], rec_reason=dcn.get("rec_reason", ""),
                   draft_path=os.path.relpath(sp, p["vault"]).replace(os.sep, "/"))
        ing_items.append(it2)
    itf = p["live"] + ".items"
    json.dump(sort_items, open(itf, "w", encoding="utf-8"), indent=2)
    queue_tx._apply_items(p["live"], queue_tx._read_items(itf), "update")
    json.dump(ing_items, open(itf, "w", encoding="utf-8"), indent=2)
    queue_tx._apply_items(p["live"], queue_tx._read_items(itf), "update")
    log_line(p["state"], stage="sort", run_id="agentpass", sorted=len(decisions), repairs=[], anomalies=[])
    log_line(p["state"], stage="ingest", run_id="agentpass", drafted=len(decisions), repairs=[], anomalies=[])
    sm, shm = rewind.reconcile(p["live"], p["vault"], apply=False)
    print(json.dumps({"sorted_and_drafted": len(decisions),
                      "reconcile_clean": (sm == [] and shm == []),
                      "drafts": {d["id"]: staging_path(p["vault"], d["kb"], d["conflict_key"]) for d in decisions}}, indent=2))


def apply_review(fixture, review_json):
    """agent-2 output: [{id, verdict: PASS|BLOCK, reason}]. Driver applies the ship policy + asserts."""
    p = paths(fixture)
    verdicts = {v["id"]: v for v in json.load(open(review_json, encoding="utf-8"))}
    by = {it["id"]: it for it in queue_tx.load(p["live"])["queue"]}
    updates, shipped, held, rejected = [], [], [], []
    for cid, it in by.items():
        v = verdicts.get(cid, {"verdict": "BLOCK", "reason": "no verdict returned"})
        it = dict(it)
        # the SAME deterministic policy the gate uses: agent supplies PASS/BLOCK, lane_policy maps it.
        action = lane_policy.ship_action(it, review_passed=(v["verdict"] == "PASS"), auto_ship_kbs=_FIXTURE_AUTO_SHIP)
        if action == "ship":
            dst = os.path.join(p["vault"], it["conflict_key"])
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(staging_path(p["vault"], it["kb"], it["conflict_key"]), dst)
            json.dump({"id": cid, "shipped_path": dst, "ts": _now(), "approved_by": "auto-ship"},
                      open(os.path.join(p["revert"], cid + ".json"), "w", encoding="utf-8"), indent=2)
            it.update(stage="shipped", approved_by="auto-ship"); shipped.append(cid)
        elif action == "reject":
            it.update(stage="rejected", reject_reason=v.get("reason", "")); rejected.append(cid)
        else:                              # hold (review lane, or confirm within TTL)
            held.append(cid)
        updates.append(it)
    itf = p["live"] + ".items"
    json.dump(updates, open(itf, "w", encoding="utf-8"), indent=2)
    queue_tx._apply_items(p["live"], queue_tx._read_items(itf), "update")
    log_line(p["state"], stage="gate", run_id="agentpass",
             items_in=len(by), shipped=len(shipped), held=len(held), rejected=len(rejected),
             repairs=[], anomalies=[])

    # ── assertions ──
    by = {it["id"]: it for it in queue_tx.load(p["live"])["queue"]}
    fails = []
    def want(name, cond):
        print(("  ok  " if cond else " FAIL ") + name)
        if not cond: fails.append(name)

    # INDEPENDENT ORACLE — re-derive the expected partition inline (NOT via lane_policy, the code that
    # applied the ship). If lane_policy regresses, its applied partition diverges from this second
    # source and the assertion fails — so the driver isn't vacuously checking its own categorization.
    def _expect(it, passed):
        if not passed: return "reject"
        if it.get("kb") not in _FIXTURE_AUTO_SHIP: return "hold"   # kb backstop — matches auto_ship_kbs
        return {"auto-ship": "ship", "review": "hold", "confirm": "hold"}.get(it.get("lane"), "hold")
    exp = {a: set() for a in ("ship", "hold", "reject")}
    for cid, it in by.items():
        v = verdicts.get(cid, {"verdict": "BLOCK"})
        exp[_expect(it, v["verdict"] == "PASS")].add(cid)
    want("partition matches an independent oracle (catches a lane_policy regression)",
         set(shipped) == exp["ship"] and set(held) == exp["hold"] and set(rejected) == exp["reject"])
    want("every shipped item has a vault file",
         all(os.path.exists(os.path.join(p["vault"], by[c]["conflict_key"])) for c in shipped))
    want("every shipped item has a revert pointer",
         all(os.path.exists(os.path.join(p["revert"], c + ".json")) for c in shipped))
    want("no review-lane item shipped (held for human)",
         all(by[c]["stage"] == "awaiting" for c in held))
    want("no held item leaked a vault file",
         all(not os.path.exists(os.path.join(p["vault"], by[c]["conflict_key"])) for c in held))
    want("every rejected item is terminal: no vault file + a reason recorded",
         all(by[c]["stage"] == "rejected" and not os.path.exists(os.path.join(p["vault"], by[c]["conflict_key"]))
             and by[c].get("reject_reason") for c in rejected))
    sm, shm = rewind.reconcile(p["live"], p["vault"], apply=False)
    want("reconcile clean end-to-end", sm == [] and shm == [])
    want("validate OK", queue_tx.validate(queue_tx.load(p["live"])) is None)
    print(json.dumps({"shipped": shipped, "held": held, "rejected": rejected,
                      "PASS": not fails, "fails": fails}, indent=2))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    op = sys.argv[1] if len(sys.argv) > 1 else ""
    if op == "setup":          setup(sys.argv[2])
    elif op == "apply-draft":  apply_draft(sys.argv[2], sys.argv[3])
    elif op == "apply-review": apply_review(sys.argv[2], sys.argv[3])
    else:
        print(__doc__); sys.exit(1)
