#!/usr/bin/env python3
# sanitize:allow-file — fixtures use synthetic/out-of-range ids by design (A79)
"""G14b — dynamic end-to-end pipeline test (ALL-IN-CODE, hermetic).

Drives the full chain inbox-capture -> sort -> ingest -> gate -> brief -> garden on an ISOLATED temp
install (never a real install's live queue), asserting EVERY handoff + the final state the backlog names:
  queue stages, vault files, revert pointers, context-log honesty, reconcile-clean, serialization.

This is the *plumbing* half of G14b: the mechanical handoffs, driven through the real helpers
(`queue_tx`, `rewind`, `garden_sweep`, `lane_policy`) with deterministic CANNED *independent-review
verdicts* so the run is repeatable + CI-able. The *judgment* half (live sort/draft/review by agents)
is `E2E-PROCEDURE.md` + `run_agent_pass.py`.

The gate's deterministic decisions (lane->action, lane<->ballot, confirm-TTL) are NOT re-implemented
here — the test calls `lane_policy`, the same helper the gate uses, so a regression in the policy
fails this test (the C1/C2 fix from the 2026-06-21 review: the policy is tested code, not a tautology).

Run:  python tools/tests/test_e2e_pipeline.py     (exit 0 = all green)
"""
import json, os, sys, time, glob, shutil, tempfile, subprocess

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../engine/tools
sys.path.insert(0, HARNESS)
import queue_tx, rewind, garden_sweep, lane_policy

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ok  " if cond else " FAIL ") + name)

def _utc(s="2026-06-21T00:00:00Z"): return s
def _now(): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
# a fixed "now" one hour after drafting -> a fresh confirm item is within its TTL (deterministic).
NOW_EPOCH = lane_policy._epoch("2026-06-21T01:00:00Z")
# lane_policy's DEFAULT_AUTO_SHIP_KBS is EMPTY (safety default — Global Constraint: auto-ship is
# opt-in per domain at setup, read from profile/connectors.yaml `gate.auto_ship_kbs`). This hermetic
# fixture models a profile that has opted dev+personal into auto-ship — an explicit CALLER choice,
# never the engine default — so it passes this set wherever it exercises the "cleared" ship path.
PROFILE_AUTO_SHIP_KBS = frozenset({"dev", "personal"})


# ── FIXTURE: 8 synthetic raws spanning every lane + a conflict-key collision pair + a reject ──────
# (id, source, kb, conflict_key, lane, recommended, rec_reason)  — the canned sort+ingest judgment.
FIXTURE = [
    ("e2e-dev-anthropic",      "bookmark", "dev",         "dev/wiki/companies/anthropic-e2e.md",        "auto-ship", "approve", "dev entity, reversible"),
    ("e2e-personal-projector", "x",        "personal",    "personal/wiki/sources/projector-e2e.md",    "auto-ship", "approve", "personal learning, reversible"),
    ("e2e-fo-bayview-refi",      "email",    "familyoffice","familyoffice/wiki/companies/bayview-e2e.md",   "review",    "hold",    "Paper-Governs: economic term, executed-doc gate"),
    ("e2e-personal-dentist",   "email",    "personal",    "personal/wiki/sources/dentist-appt-e2e.md", "confirm",   "approve", "personal schedule, soft gate"),
    ("e2e-dev-supabase-sec",   "email",    "dev",         "dev/wiki/companies/supabase-e2e.md",         "review",    "hold",    "security notice -> human eyes"),
    ("e2e-dev-replit-a",       "email",    "dev",         "dev/wiki/companies/replit-e2e.md",           "auto-ship", "approve", "dev entity (collision A)"),
    ("e2e-dev-replit-b",       "email",    "dev",         "dev/wiki/companies/replit-e2e.md",           "auto-ship", "approve", "dev entity (collision B - same key, must serialize)"),
    ("e2e-dev-badfork",        "bookmark", "dev",         "dev/wiki/companies/badfork-e2e.md",          "auto-ship", "approve", "dev note that will FAIL independent review"),
]
AUTOSHIP_IDS = {i[0] for i in FIXTURE if i[4] == "auto-ship"}
REVIEW_IDS   = {i[0] for i in FIXTURE if i[4] == "review"}
CONFIRM_IDS  = {i[0] for i in FIXTURE if i[4] == "confirm"}
# canned INDEPENDENT-REVIEW verdict (the agent's job in the live pass): badfork is BLOCKed.
REVIEW_PASS  = {i[0]: (i[0] != "e2e-dev-badfork") for i in FIXTURE}


def staging_path(vault, kb, ck):
    return os.path.join(vault, kb, "wiki", "staging", os.path.splitext(os.path.basename(ck))[0] + ".md")
def ship_path(vault, ck):
    return os.path.join(vault, ck)
def vault_files(vault):
    return [p for p in glob.glob(os.path.join(vault, "**", "*.md"), recursive=True)
            if os.sep + "staging" + os.sep not in p]
def log_line(state, **kw):
    kw.setdefault("ts", _now())
    with open(os.path.join(state, "context-log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(kw) + "\n")
def stage_counts(live):
    q = queue_tx.load(live)["queue"]
    c = {}
    for it in q:
        c[it["stage"]] = c.get(it["stage"], 0) + 1
    return c, {it["id"]: it for it in q}
def do_ship(it, vault, revert_dir, approved_by):
    """The mechanical ship: promote staging draft -> vault, write a revert pointer. (gate is a
    SKILL, not code; this is the only hand-rolled part, and it's the same in the live driver.)"""
    dst = ship_path(vault, it["conflict_key"]); os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(staging_path(vault, it["kb"], it["conflict_key"]), dst)
    json.dump({"id": it["id"], "shipped_path": dst, "ts": _now(), "approved_by": approved_by},
              open(os.path.join(revert_dir, it["id"] + ".json"), "w", encoding="utf-8"), indent=2)


d = tempfile.mkdtemp(prefix="e2e_pipeline_")
try:
    install = os.path.join(d, "install")
    state, vault = os.path.join(install, "state"), os.path.join(install, "vault")
    revert_dir, raw_dir = os.path.join(state, "revert"), os.path.join(install, "raw", "inbox")
    for p in (state, vault, revert_dir, raw_dir):
        os.makedirs(p, exist_ok=True)
    live = os.path.join(state, "queue.json")
    items_file = live + ".items"

    for fid, src, kb, ck, lane, rec, why in FIXTURE:
        with open(os.path.join(raw_dir, fid + ".md"), "w", encoding="utf-8") as f:
            f.write(f"# {fid}\n\nsynthetic raw for the {kb} / {lane} lane.\n")

    # ════════ lane_policy — the gate's deterministic decisions, TESTED AS CODE (C1/C2 fix) ════════
    print("\n== lane_policy (tested decision code) ==")
    autoship_item = {"lane": "auto-ship", "kb": "dev"}
    review_item   = {"lane": "review", "kb": "dev"}
    confirm_fresh = {"lane": "confirm", "kb": "dev", "first_drafted_utc": "2026-06-21T00:00:00Z"}
    confirm_old   = {"lane": "confirm", "kb": "dev", "first_drafted_utc": "2026-06-01T00:00:00Z"}
    check("lane_policy: SAFETY DEFAULT - no explicit auto_ship_kbs holds a dev auto-ship item too",
          lane_policy.ship_action(autoship_item, True) == "hold")
    check("lane_policy: auto-ship + PASS -> ship (profile opts dev/personal into auto-ship)",
          lane_policy.ship_action(autoship_item, True, auto_ship_kbs=PROFILE_AUTO_SHIP_KBS) == "ship")
    check("lane_policy: auto-ship + BLOCK -> reject", lane_policy.ship_action(autoship_item, False) == "reject")
    check("lane_policy: review + PASS -> hold (never auto-ship)", lane_policy.ship_action(review_item, True) == "hold")
    check("lane_policy: review + BLOCK -> reject", lane_policy.ship_action(review_item, False) == "reject")
    check("lane_policy: confirm within TTL -> hold",
          lane_policy.ship_action(confirm_fresh, True, now_epoch=NOW_EPOCH) == "hold")
    check("lane_policy: confirm past TTL -> ship (profile opts dev into auto-ship)",
          lane_policy.ship_action(confirm_old, True, now_epoch=NOW_EPOCH,
                                  auto_ship_kbs=PROFILE_AUTO_SHIP_KBS) == "ship")
    check("lane_policy: ballot rule rejects hold-on-auto-ship (the known bug)",
          lane_policy.ballot_consistent("auto-ship", "approve") and not lane_policy.ballot_consistent("auto-ship", "hold"))
    check("lane_policy: ballot rule wants hold on review lane",
          lane_policy.ballot_consistent("review", "hold") and not lane_policy.ballot_consistent("review", "approve"))
    # kb-scope backstop (2026-06-22 SHIP widen): a KB outside auto_ship_kbs is ALWAYS human-gated,
    # even on auto-ship / past-TTL confirm. familyoffice is excluded by default (Paper-Governs).
    fo_autoship   = {"lane": "auto-ship", "kb": "familyoffice"}
    fo_confirm_old= {"lane": "confirm", "kb": "familyoffice", "first_drafted_utc": "2026-06-01T00:00:00Z"}
    pers_autoship = {"lane": "auto-ship", "kb": "personal"}
    unknown_kb    = {"lane": "auto-ship", "kb": "general"}
    check("lane_policy(kb): familyoffice auto-ship + PASS -> HOLD (Paper-Governs never auto-ships)",
          lane_policy.ship_action(fo_autoship, True) == "hold")
    check("lane_policy(kb): familyoffice confirm past TTL -> HOLD (kb backstop overrides the clock)",
          lane_policy.ship_action(fo_confirm_old, True, now_epoch=NOW_EPOCH) == "hold")
    check("lane_policy(kb): familyoffice auto-ship + BLOCK -> reject (verdict still wins)",
          lane_policy.ship_action(fo_autoship, False) == "reject")
    check("lane_policy(kb): personal auto-ship + PASS -> ship (profile clears personal)",
          lane_policy.ship_action(pers_autoship, True, auto_ship_kbs=PROFILE_AUTO_SHIP_KBS) == "ship")
    check("lane_policy(kb): a kb outside the cleared set holds (safe-by-default, even with the widen)",
          lane_policy.ship_action(unknown_kb, True, auto_ship_kbs=PROFILE_AUTO_SHIP_KBS) == "hold")
    check("lane_policy(kb): explicit widen lets familyoffice auto-ship (caller may override)",
          lane_policy.ship_action(fo_autoship, True, auto_ship_kbs=frozenset({"dev","personal","familyoffice"})) == "ship")

    # ════════════════ STAGE 1 — INBOX-CAPTURE (add new items, stage=captured) ════════════════
    print("\n== STAGE 1: inbox-capture ==")
    cap_items = [{"id": fid, "source": src, "stage": "captured",
                  "payload_path": os.path.join(raw_dir, fid + ".md"),
                  "captured_utc": _utc(), "history": [{"ts": _utc(), "stage": "captured"}]}
                 for fid, src, kb, ck, lane, rec, why in FIXTURE]
    json.dump(cap_items, open(items_file, "w", encoding="utf-8"), indent=2)
    queue_tx._apply_items(live, queue_tx._read_items(items_file), "add")
    c, by = stage_counts(live)
    n = len(FIXTURE)
    check(f"capture: {n} items enqueued at 'captured'", c.get("captured") == n)
    check("capture: validate OK", queue_tx.validate(queue_tx.load(live)) is None)
    check("capture: single-file store on disk (no shard dir)",
          os.path.isfile(live) and not os.path.isdir(live + ".d"))
    check("capture: every item's payload_path is a present, readable raw",
          all(os.path.exists(by[i[0]]["payload_path"]) and os.path.getsize(by[i[0]]["payload_path"]) > 0 for i in FIXTURE))
    log_line(state, stage="inbox-capture", run_id="e2e", items_in=n, items_out=n, repairs=[], anomalies=[])
    # capture is append-only: re-adding an existing id is refused (dedupe fence)
    json.dump([cap_items[0]], open(items_file, "w", encoding="utf-8"), indent=2)
    rdup = subprocess.run([sys.executable, os.path.join(HARNESS, "queue_tx.py"), "add", live, items_file],
                          capture_output=True, text=True)
    check("capture: re-add of same id refused (dedupe fence)", rdup.returncode != 0 and "dedupe" in (rdup.stdout + rdup.stderr))

    # ════════════════ STAGE 2 — SORT (captured -> sorted; assign kb/conflict_key/lane) ════════════════
    print("\n== STAGE 2: sort ==")
    sort_items = []
    for fid, src, kb, ck, lane, rec, why in FIXTURE:
        it = dict(by[fid]); it.update(stage="sorted", kb=kb, conflict_key=ck, lane=lane)
        it["history"] = it.get("history", []) + [{"ts": _utc(), "stage": "sorted", "kb": kb}]
        sort_items.append(it)
    json.dump(sort_items, open(items_file, "w", encoding="utf-8"), indent=2)
    queue_tx._apply_items(live, queue_tx._read_items(items_file), "update")
    c, by = stage_counts(live)
    check(f"sort: all {n} advanced to 'sorted'", c.get("sorted") == n and "captured" not in c)
    check("sort: every sorted item has a conflict_key", all(by[i[0]].get("conflict_key") for i in FIXTURE))
    check("sort: lanes assigned across the 3 lanes", {by[i[0]]["lane"] for i in FIXTURE} == {"auto-ship", "review", "confirm"})
    check("sort: the replit pair shares one conflict_key (serialization target)",
          by["e2e-dev-replit-a"]["conflict_key"] == by["e2e-dev-replit-b"]["conflict_key"])
    log_line(state, stage="sort", run_id="e2e", sorted=n, repairs=[], anomalies=[])

    # ════════════════ STAGE 3 — INGEST / Phase A (sorted -> awaiting; write staging draft + ballot) ════
    print("\n== STAGE 3: ingest (Phase A draft) ==")
    ing_items = []
    for fid, src, kb, ck, lane, rec, why in FIXTURE:
        sp = staging_path(vault, kb, ck); os.makedirs(os.path.dirname(sp), exist_ok=True)
        with open(sp, "w", encoding="utf-8") as f:
            f.write(f"# {os.path.splitext(os.path.basename(ck))[0]}\n\nDraft distilled from {fid}.\n")
        it = dict(by[fid]); it.update(stage="awaiting", first_drafted_utc=_utc(), recommended=rec, rec_reason=why,
                                      draft_path=os.path.relpath(sp, vault).replace(os.sep, "/"))
        it["history"] = it.get("history", []) + [{"ts": _utc(), "stage": "awaiting"}]
        ing_items.append(it)
    json.dump(ing_items, open(items_file, "w", encoding="utf-8"), indent=2)
    queue_tx._apply_items(live, queue_tx._read_items(items_file), "update")
    c, by = stage_counts(live)
    n_drafts = len(glob.glob(os.path.join(vault, "*", "wiki", "staging", "*.md")))
    n_keys = len({i[3] for i in FIXTURE})   # the replit pair shares one conflict_key -> one shared draft
    check(f"ingest: all {n} advanced to 'awaiting'", c.get("awaiting") == n and "sorted" not in c)
    check("ingest: one staging draft per distinct conflict_key (replit pair shares its target)",
          n_drafts == n_keys == n - 1)
    check("ingest: every awaiting item has the clock + ballot",
          all(by[i[0]].get("first_drafted_utc") and by[i[0]].get("recommended") for i in FIXTURE))
    check("ingest: every item's (lane, ballot) is consistent (no hold-on-auto-ship)",
          all(lane_policy.ballot_consistent(by[i[0]]["lane"], by[i[0]]["recommended"]) for i in FIXTURE))
    sm, shm = rewind.reconcile(live, vault, apply=False)
    check("ingest: reconcile clean (no awaiting-without-draft)", sm == [] and shm == [])
    log_line(state, stage="ingest", run_id="e2e", drafted=n_drafts, repairs=[], anomalies=[])

    # ════════════════ STAGE 4 — GATE ════════════════
    print("\n== STAGE 4: gate ==")
    # 4a. claim the awaiting set — claim() SERIALIZES on conflict_key (replit pair).
    claimed = queue_tx.claim(live, [i[0] for i in FIXTURE], "e2e-worker")
    check("gate: conflict-key serialized (only ONE of the replit pair claimed)",
          len({"e2e-dev-replit-a", "e2e-dev-replit-b"} & set(claimed)) == 1)
    serialized_out = ({"e2e-dev-replit-a", "e2e-dev-replit-b"} - set(claimed)).pop()

    # 4b. decide each claimed item via lane_policy (the SAME helper the gate uses) + the canned verdict.
    c, by = stage_counts(live)
    shipped_now, held, rejected_now, ship_updates = [], [], [], []
    for fid in claimed:
        it = dict(by[fid])
        action = lane_policy.ship_action(it, review_passed=REVIEW_PASS[fid], now_epoch=NOW_EPOCH,
                                         auto_ship_kbs=PROFILE_AUTO_SHIP_KBS)
        if action == "ship":
            do_ship(it, vault, revert_dir, "auto-ship")
            it.update(stage="shipped", approved_by="auto-ship", claimed_by=None, claimed_at=None)
            shipped_now.append(fid)
        elif action == "reject":
            it.update(stage="rejected", claimed_by=None, claimed_at=None)
            rejected_now.append(fid)
        else:   # hold
            it.update(claimed_by=None, claimed_at=None); held.append(fid)
        ship_updates.append(it)
    json.dump(ship_updates, open(items_file, "w", encoding="utf-8"), indent=2)
    queue_tx._apply_items(live, queue_tx._read_items(items_file), "update")
    c, by = stage_counts(live)

    check("gate: exactly the (un-serialized) auto-ship-PASS items shipped",
          set(shipped_now) == (AUTOSHIP_IDS - {serialized_out, "e2e-dev-badfork"}))
    check("gate: the BLOCKed item is 'rejected' (terminal)", by["e2e-dev-badfork"]["stage"] == "rejected")
    check("gate: NO vault file for the rejected item (gate didn't leak it)",
          not os.path.exists(ship_path(vault, by["e2e-dev-badfork"]["conflict_key"])))
    check("gate: every review-lane item HELD (still awaiting)",
          all(by[i].get("stage") == "awaiting" for i in REVIEW_IDS))
    check("gate: the confirm item HELD within TTL (lane_policy)",
          all(by[i].get("stage") == "awaiting" for i in CONFIRM_IDS))
    check("gate: the serialized replit still awaiting (waits for the gate)",
          by[serialized_out]["stage"] == "awaiting")
    check("gate: a vault file + revert pointer exists for every shipped item",
          all(os.path.exists(ship_path(vault, by[i]["conflict_key"])) and
              os.path.exists(os.path.join(revert_dir, i + ".json")) for i in shipped_now))
    check("gate: NO vault file for any held item (gate didn't leak)",
          not os.path.exists(ship_path(vault, by["e2e-fo-bayview-refi"]["conflict_key"])))
    n_vault_now = len(vault_files(vault))
    check("gate: vault file count == shipped count (honest, emergent after serialize+reject)",
          n_vault_now == len(shipped_now) == 3)
    sm, shm = rewind.reconcile(live, vault, apply=False)
    check("gate: reconcile clean after ship", sm == [] and shm == [])
    log_line(state, stage="gate", run_id="e2e", items_in=len(claimed),
             shipped=n_vault_now, held=len(held), rejected=len(rejected_now),
             serialized=[serialized_out], repairs=[], anomalies=[])

    # 4b'. RECONCILE one-pass heal (M2): a shipped item whose vault file vanishes but whose staging
    #      draft is still present settles at 'awaiting' in ONE pass. (Drafts not yet swept here.)
    print("\n== STAGE 4b': reconcile one-pass heal ==")
    victim1 = "e2e-personal-projector"
    os.remove(ship_path(vault, by[victim1]["conflict_key"]))
    sm1, shm1 = rewind.reconcile(live, vault, apply=False)
    check("reconcile(1-pass): detects shipped-without-file", victim1 in shm1)
    rewind.reconcile(live, vault, apply=True)
    c, by = stage_counts(live)
    check("reconcile(1-pass): draft still present -> settles at 'awaiting' (one pass)", by[victim1]["stage"] == "awaiting")
    # restore: re-ship projector so downstream counts hold
    do_ship(by[victim1], vault, revert_dir, "auto-ship")
    it = dict(by[victim1]); it.update(stage="shipped", approved_by="auto-ship")
    json.dump([it], open(items_file, "w", encoding="utf-8"), indent=2)
    queue_tx._apply_items(live, queue_tx._read_items(items_file), "update")

    # 4c. CONFIRM-TIMEOUT (lane_policy decides): backdate the clock -> ship_action returns 'ship'.
    print("\n== STAGE 4c: confirm-timeout ships ==")
    conf_id = next(iter(CONFIRM_IDS))
    c, by = stage_counts(live)
    it = dict(by[conf_id]); it["first_drafted_utc"] = "2026-06-01T00:00:00Z"   # >3d ago
    check("confirm-timeout: lane_policy now returns 'ship' for the aged confirm item",
          lane_policy.ship_action(it, review_passed=True, now_epoch=NOW_EPOCH,
                                  auto_ship_kbs=PROFILE_AUTO_SHIP_KBS) == "ship")
    json.dump([it], open(items_file, "w", encoding="utf-8"), indent=2)
    queue_tx._apply_items(live, queue_tx._read_items(items_file), "update")
    c, by = stage_counts(live)
    it = dict(by[conf_id]); do_ship(it, vault, revert_dir, "confirm-timeout")
    it.update(stage="shipped", approved_by="confirm-timeout")
    json.dump([it], open(items_file, "w", encoding="utf-8"), indent=2)
    queue_tx._apply_items(live, queue_tx._read_items(items_file), "update")
    c, by = stage_counts(live)
    check("confirm-timeout: the confirm item ships after its TTL", by[conf_id]["stage"] == "shipped")
    check("confirm-timeout: its vault file exists", os.path.exists(ship_path(vault, by[conf_id]["conflict_key"])))

    # ════════════════ STAGE 5 — GARDEN (mechanical sweep + propose-through-gate) ════════════════
    print("\n== STAGE 5: garden ==")
    old_tmp = os.path.join(state, "stale.tmp"); open(old_tmp, "w").write("x")
    os.utime(old_tmp, (time.time() - 10 * 86400, time.time() - 10 * 86400))   # 10 days old
    orphan = staging_path(vault, "dev", "dev/wiki/companies/anthropic-e2e.md")   # item shipped -> orphan
    rejected_orphan = staging_path(vault, "dev", "dev/wiki/companies/badfork-e2e.md")  # item rejected -> orphan
    live_draft = staging_path(vault, "familyoffice", "familyoffice/wiki/companies/bayview-e2e.md")  # awaiting -> keep
    check("garden(pre): orphan drafts present (shipped + rejected items)",
          os.path.exists(orphan) and os.path.exists(rejected_orphan))
    backups, orphans, evidence = garden_sweep.sweep(install, ttl_days=7, apply=True)
    check("garden: stale .tmp residue swept", not os.path.exists(old_tmp))
    check("garden: orphan staging draft (shipped item) swept", not os.path.exists(orphan))
    check("garden: orphan staging draft (rejected item) swept", not os.path.exists(rejected_orphan))
    check("garden: live staging draft (awaiting item) KEPT", os.path.exists(live_draft))
    fresh_residue = os.path.join(state, "queue.json.last-good")   # legacy residue, freshly touched
    open(fresh_residue, "w", encoding="utf-8").write("{}")
    check("garden: fresh legacy residue (.last-good) NOT swept (under TTL)",
          os.path.exists(fresh_residue))

    # propose-through-gate — a connect-hub proposal enters as awaiting/review/source:self + a draft.
    hub_ck = "personal/wiki/mocs/immersive-e2e.md"; hub_draft = staging_path(vault, "personal", hub_ck)
    os.makedirs(os.path.dirname(hub_draft), exist_ok=True)
    open(hub_draft, "w", encoding="utf-8").write("# immersive-e2e\n\nproposed connect hub.\n")
    prop = {"id": "e2e-garden-hub", "source": "self", "stage": "awaiting", "kb": "personal",
            "conflict_key": hub_ck, "lane": "review", "recommended": "hold",
            "rec_reason": "garden connect proposal — human approves", "first_drafted_utc": _now(),
            "draft_path": os.path.relpath(hub_draft, vault).replace(os.sep, "/"),
            "history": [{"ts": _now(), "stage": "awaiting"}]}
    json.dump([prop], open(items_file, "w", encoding="utf-8"), indent=2)
    queue_tx._apply_items(live, queue_tx._read_items(items_file), "add")
    c, by = stage_counts(live)
    check("garden: connect proposal enqueued as awaiting/review", by["e2e-garden-hub"]["lane"] == "review")
    check("garden: the proposal HOLDS (never silently shipped)", by["e2e-garden-hub"]["stage"] == "awaiting")
    log_line(state, stage="garden", run_id="e2e", swept=len(backups) + len(orphans), proposed=1, repairs=[], anomalies=[])

    # ════════════════ BRIEF (read side) — the review panel surfaces held items ════════════════
    print("\n== BRIEF: review panel data ==")
    q = queue_tx.load(live)["queue"]
    panel = [it for it in q if it["stage"] == "awaiting" and it.get("lane") in ("review", "confirm")]
    panel_ids = {it["id"] for it in panel}
    expected_panel = REVIEW_IDS | {"e2e-garden-hub"}   # confirm shipped on timeout; serialized replit is auto-ship lane
    check("brief: review panel surfaces exactly the held review-lane items + garden proposal", panel_ids == expected_panel)
    check("brief: no shipped/rejected item leaks into the review panel",
          not any(it["stage"] in ("shipped", "rejected") for it in panel))

    # ════════════════ FINAL STATE — the whole-chain assertions ════════════════
    print("\n== FINAL STATE ==")
    c, by = stage_counts(live)
    check("final: 4 shipped (2 auto-ship + 1 of replit pair + 1 confirm-timeout)", c.get("shipped") == 4)
    check("final: 4 awaiting (2 review + 1 serialized replit + 1 garden proposal)", c.get("awaiting") == 4)
    check("final: 1 rejected (the BLOCKed item)", c.get("rejected") == 1)
    check("final: 0 captured/sorted left (chain fully advanced)", "captured" not in c and "sorted" not in c)
    check("final: total item count conserved (8 fixture + 1 garden = 9)", len(by) == 9)
    check("final: validate OK on the single-file store", queue_tx.validate(queue_tx.load(live)) is None)
    check("final: vault file count == shipped count", len(vault_files(vault)) == c.get("shipped"))
    sm, shm = rewind.reconcile(live, vault, apply=False)
    check("final: reconcile clean end-to-end", sm == [] and shm == [])

    # reconcile loop-until-stable (G7): a shipped item whose vault file AND draft are both gone
    # (anthropic's draft was swept as an orphan) settles shipped->awaiting->sorted in one --apply call.
    print("\n== reconcile: loop-until-stable (phantom) ==")
    victim2 = "e2e-dev-anthropic"
    os.remove(ship_path(vault, by[victim2]["conflict_key"]))
    sm2, shm2 = rewind.reconcile(live, vault, apply=False)
    check("reconcile(phantom): detects shipped-without-file", victim2 in shm2)
    rewind.reconcile(live, vault, apply=True)
    c, by = stage_counts(live)
    check("reconcile(phantom): no draft to return to -> settles at 'sorted'", by[victim2]["stage"] == "sorted")
    sm3, shm3 = rewind.reconcile(live, vault, apply=False)
    check("reconcile(phantom): clean again after heal", sm3 == [] and shm3 == [])

    # ── CONTEXT-LOG HONESTY — logged counts derived from / matched to on-disk reality ──
    print("\n== CONTEXT-LOG HONESTY ==")
    logs = [json.loads(l) for l in open(os.path.join(state, "context-log.jsonl"), encoding="utf-8") if l.strip()]
    bs = {l["stage"]: l for l in logs}
    check("ctxlog: a line per stage", {"inbox-capture", "sort", "ingest", "gate", "garden"} <= set(bs))
    check("ctxlog: inbox-capture items_out == items actually enqueued", bs["inbox-capture"]["items_out"] == n)
    check("ctxlog: ingest drafted == staging files actually written (distinct keys)", bs["ingest"]["drafted"] == n - 1)
    check("ctxlog: gate shipped == vault files present right after that run (emergent ==3)",
          bs["gate"]["shipped"] == 3)
    check("ctxlog: gate recorded the rejected + serialized it produced",
          bs["gate"]["rejected"] == 1 and len(bs["gate"]["serialized"]) == 1)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:", FAIL)
    sys.exit(1 if FAIL else 0)
finally:
    shutil.rmtree(d, ignore_errors=True)
