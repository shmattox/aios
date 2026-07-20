#!/usr/bin/env python3
"""rewind.py - the pipeline's universal UNDO / cleanup primitive.

Every pipeline process is rewindable. Where queue_tx.py enforces safe *forward* commits, rewind.py
enforces safe *backward* moves: send a unit to an earlier stage, undo a ship, or auto-reconcile a
state/file desync - each op atomic (committed through queue_tx), logged in item history, and itself
revertible (one snapshot pointer per rewind, so you can rewind the rewind).

This is the "something went wrong / got rejected -> clean it up" capability, implemented ONCE in code
so no stage reintroduces a half-undo by hand (same discipline as queue_tx's forward writes).

Discipline (Stage Contract): writes ONLY the queue (through queue_tx.commit), the snapshot dir, and -
for undo-ship / reconcile of ships - the RESOLVED vault (the same vault the gate ships to: the real
vault when --kb-map resolves it, per A19; a kb not in the map is an error, never a fallback).
NEVER Notion / Drive / Memory. Fact-free.

Usage:
  rewind.py reset        <queue.json> <id1,id2,...> <to_stage> [reason] [--snap-dir DIR]
  rewind.py undo-ship    <queue.json> <id> <vault_root> <revert_dir> [to_stage] [--snap-dir DIR] [--kb-map JSON]
  rewind.py reconcile    <queue.json> <vault_root> [--apply] [--migrate-retired] [--snap-dir DIR] [--kb-map JSON]
  rewind.py mark-retired <queue.json> <id1,id2,...> [note] [--snap-dir DIR]
  rewind.py mark-retired <queue.json> --ck <conflict_key> [note] [--snap-dir DIR]
  rewind.py undo         <queue.json> <snap_id> [--snap-dir DIR]
  rewind.py list         [--snap-dir DIR]

mark-retired (A28): a shipped page later removed ON PURPOSE (garden distill->retire archives the
husk; an approved prune deletes the page) is a lifecycle EXIT, not corruption — but reconcile
cannot tell the difference from the filesystem alone, so the removing ship must mark the page's
prior shipped item(s) `retired: true` (the `draftless` precedent, applied to shipped). Stage stays
`shipped` (it did ship; the marker records the exit); reconcile skips marked items forever.
By ids (dies on a missing id) or --ck (marks every SHIPPED item with that conflict_key; zero
matches is a no-op — gate idempotency). Snapshotted -> undoable via `undo`.

reconcile --migrate-retired (A28): the migration for instances that retired pages BEFORE the
marker existed. A shipped-w/o-file item whose slug survives at the distill-retire archive
convention (`<kb>/raw/archive/wiki-sources-retired-*/<slug>.md`) is EVIDENCED — dry-run reports
it; with --apply it is marked retired instead of rewound. No evidence -> stays flagged (prune
deletions leave no archive trail; mark those explicitly, never guess).

--kb-map (A19): the profile's `vault.live_kb_map` as a JSON object (`{"personal":"01_Personal",...}`),
REQUIRED whenever <vault_root> is the REAL vault — its folders are mapped, not kb short names, and an
unmapped live-vault `reconcile --apply` would bounce every healthy legacy item.

Stages: captured < sorted < awaiting < shipped   (+ terminal rejected / reverted).
'reset' clears every field a *later* stage introduced and preserves earlier-stage fields, so a
rewound item is indistinguishable from one that legitimately sits at that stage.
"""
import json, os, sys, time, copy, glob, shutil
import queue_tx   # same dir; reuse the enforced validate + atomic write

STAGE_ORDER = ["captured", "sorted", "awaiting", "shipped"]
# (terminal stages rejected/reverted aren't in STAGE_ORDER: you can't rewind *to* them, and an item
#  currently sitting at one is safely over-cleared by _clear_for_reset.)
# Fields a stage INTRODUCES. Rewinding to BEFORE that stage clears them.
#   sorted   = Sort assigns routing + serialization + lane
#   awaiting = Phase-A drafting assigns the clock + the ballot (and writes the staging file)
#   shipped  = adds external artifacts (vault file + revert pointer), no queue-only fields
FIELDS_AT = {
    "sorted":   ["kb", "conflict_key", "lane"],
    "awaiting": ["first_drafted_utc", "recommended", "rec_reason"],
    "shipped":  [],
}


def _now():  return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
def _die(m): print("FAIL:", m); sys.exit(1)


def _snap_id():
    """Sub-second, lexically sortable, collision-free: two rewinds in the same second get distinct ids."""
    now = time.time()
    return "rewind-" + time.strftime("%Y%m%dT%H%M%S", time.gmtime(now)) + f"{int((now % 1) * 1e6):06d}Z"


def _win_long(path):
    """Windows long-path shim (A19): plain open()/stat() fail past ~260 chars unless the path
    carries the `\\\\?\\` prefix, so a healthy deep-vault draft read as ABSENT (the A14 finding —
    reconcile would have bounced it). No-op off Windows, on short paths, and on already-prefixed
    or UNC paths (a >250-char UNC vault is NOT supported — it would need the `\\\\?\\UNC\\` form;
    the vault is local by design)."""
    if os.name == "nt":
        ap = os.path.abspath(path)
        if len(ap) > 250 and not ap.startswith("\\\\"):
            return "\\\\?\\" + ap
    return path


def _file_present(path):
    """True only if the file exists and is non-empty — an empty draft is no draft, and
    reconcile should rewind it."""
    try:
        return os.path.getsize(_win_long(path)) > 0
    except OSError:
        return False


def _kb_folder(kb, kb_map):
    """kb short name -> vault folder. The TEST vault's folders ARE the short names (identity);
    the REAL vault maps them via the profile's `vault.live_kb_map` (A19) — passed in as kb_map,
    never read from a profile here (fact-free)."""
    return (kb_map or {}).get(kb, kb)


def _ck_path(vault_root, ck, kb_map):
    """Resolve a kb-prefixed conflict_key (`{kb}/wiki/...`) to an absolute vault path, mapping the
    kb segment through kb_map. A keyless/odd ck falls back to a plain join (legacy behavior)."""
    parts = (ck or "").split("/", 1)
    if len(parts) == 2:
        return os.path.join(vault_root, _kb_folder(parts[0], kb_map), parts[1])
    return os.path.join(vault_root, ck or "")


def _default_snap_dir(queue_path):
    """<install>/state/rewind/ - sibling of the queue's dir."""
    return os.path.join(os.path.dirname(os.path.abspath(queue_path)), "rewind")


def _stage_idx(stage):
    return STAGE_ORDER.index(stage) if stage in STAGE_ORDER else -1


def _clear_for_reset(item, to_stage):
    """Remove fields introduced strictly AFTER to_stage (terminal stages clear everything later).
    Also clears the `retired` lifecycle marker (A28): a rewind means the item is LIVE again —
    a marker left behind would make reconcile skip the resurrected item's real desyncs."""
    keep = _stage_idx(to_stage)
    for st in STAGE_ORDER:
        if _stage_idx(st) > keep:
            for f in FIELDS_AT.get(st, []):
                item.pop(f, None)
    item.pop("retired", None)


def _write_snapshot(snap_dir, snap):
    os.makedirs(snap_dir, exist_ok=True)
    p = os.path.join(snap_dir, snap["snap_id"] + ".json")
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(snap, indent=2, ensure_ascii=False))
    return p


def _commit(queue_obj, live_path):
    """Validate + commit through queue_tx (atomic single-file write). Refuse invalid queues."""
    err = queue_tx.validate(queue_obj)
    if err:
        _die("refusing to commit invalid queue (live untouched): " + err)
    proposed = live_path + ".proposed"
    with open(proposed, "w", encoding="utf-8") as f:
        f.write(json.dumps(queue_obj, indent=2, ensure_ascii=False))
    queue_tx.commit(proposed, live_path)
    try:
        os.remove(proposed)                  # transient handoff file, not a representation
    except OSError:
        pass


def reset(queue_path, ids, to_stage, reason="", snap_dir=None):
    """Rewind the given ids to an earlier stage. Snapshots the pre-image first (undoable)."""
    if to_stage not in STAGE_ORDER:
        _die(f"to_stage must be one of {STAGE_ORDER}")
    snap_dir = snap_dir or _default_snap_dir(queue_path)
    d = queue_tx.load(queue_path)
    want = set(ids)
    sid, now = _snap_id(), _now()
    snap_items, touched = [], []
    for it in d["queue"]:
        if it.get("id") in want:
            snap_items.append(copy.deepcopy(it))           # pre-image for undo
            frm = it.get("stage")
            _clear_for_reset(it, to_stage)
            it["stage"] = to_stage
            it["claimed_by"] = None
            it["claimed_at"] = None
            entry = {
                "ts": now, "stage": to_stage, "by": "rewind", "snap_id": sid,
                "note": f"rewind from {frm}" + (f": {reason}" if reason else ""),
            }
            if frm == "shipped":
                entry["undo_of"] = "shipped"   # A97: a reset FROM shipped is a real ship revert too —
            it.setdefault("history", []).append(entry)   # mark it so the honest counter isn't blind
            touched.append(it.get("id"))
    missing = want - set(touched)
    if missing:
        _die(f"ids not found (no changes committed): {sorted(missing)}")
    _write_snapshot(snap_dir, {
        "snap_id": sid, "ts": now, "op": "reset", "to_stage": to_stage, "reason": reason,
        "queue_path": os.path.abspath(queue_path), "items": snap_items,
    })
    _commit(d, queue_path)
    print(f"reset {len(touched)} item(s) -> {to_stage}: {touched}")
    print(f"snapshot {sid}  (undo: rewind.py undo {queue_path} {sid})")
    return sid


def undo(queue_path, snap_id, snap_dir=None):
    """Restore every item in a snapshot to its exact pre-rewind state (rewind the rewind).

    The undo is itself revertible: it snapshots the items it is about to overwrite before committing.
    Note: it restores QUEUE state only - a vault file removed by undo-ship is NOT recreated (re-run
    gate to redraft/ship). An item present in the snapshot but absent from the live queue is
    re-appended (intended for rewind-the-rewind; it will resurrect an item deliberately purged later).
    """
    snap_dir = snap_dir or _default_snap_dir(queue_path)
    sp = os.path.join(snap_dir, snap_id + ".json")
    if not _file_present(sp):
        _die(f"snapshot not found: {sp}")
    snap = json.load(open(sp, encoding="utf-8"))
    d = queue_tx.load(queue_path)
    pre = {x["id"]: x for x in snap["items"]}
    idx = {it.get("id"): i for i, it in enumerate(d["queue"])}
    sid, now = _snap_id(), _now()
    pre_image = [copy.deepcopy(d["queue"][idx[cid]]) for cid in pre if cid in idx]   # undoable undo
    restored = []
    for cid, item in pre.items():
        if cid in idx:
            d["queue"][idx[cid]] = item
        else:
            d["queue"].append(item)
        restored.append(cid)
    _write_snapshot(snap_dir, {
        "snap_id": sid, "ts": now, "op": "undo", "of_snap": snap_id,
        "queue_path": os.path.abspath(queue_path), "items": pre_image,
    })
    _commit(d, queue_path)
    print(f"undo {snap_id}: restored {len(restored)} item(s): {restored}")
    print(f"snapshot {sid}  (this undo is itself revertible)")


def undo_ship(queue_path, cid, vault_root, revert_dir, to_stage="awaiting", snap_dir=None, kb_map=None):
    """Undo a ship: remove the shipped vault file, return the item to an earlier stage."""
    if to_stage not in STAGE_ORDER:
        _die(f"to_stage must be one of {STAGE_ORDER}")
    snap_dir = snap_dir or _default_snap_dir(queue_path)
    rp = os.path.join(revert_dir, cid + ".json")
    shipped_path, pointer = None, {}
    if _file_present(rp):
        try:
            pointer = json.load(open(rp, encoding="utf-8"))
            shipped_path = pointer.get("shipped_path")
        except (OSError, ValueError):
            pointer = {}
    d = queue_tx.load(queue_path)
    sid, now = _snap_id(), _now()
    snap_items, found, removed = [], False, False
    for it in d["queue"]:
        if it.get("id") == cid:
            found = True
            snap_items.append(copy.deepcopy(it))
            if not shipped_path and it.get("kind") != "proposal":
                # A96: a proposal ship created a Notion task row, NOT a vault file — leave
                # shipped_path falsy so undo-ship never os.removes a colliding vault path; it just
                # reverts the queue stage. (A file-based undo would be the wrong tool anyway.)
                # same live-vault class as _reconcile_scan (A19): map the ck's kb segment,
                # tolerate a legacy extensionless ck (the note on disk always carries .md)
                shipped_path = _ck_path(vault_root, it.get("conflict_key", ""), kb_map)
                if (not _file_present(shipped_path) and not shipped_path.endswith(".md")
                        and _file_present(shipped_path + ".md")):
                    shipped_path += ".md"
            if shipped_path and _file_present(shipped_path):
                prev = pointer.get("prev_content_path")
                if pointer.get("merged") and prev and _file_present(prev):
                    # a MERGED daily-note ship: restore the pre-merge incumbent, never delete
                    # the note (the promise both gate bodies make — ship.py pointer parity)
                    try:
                        with open(_win_long(prev), encoding="utf-8") as f:
                            incumbent = f.read()
                        with open(_win_long(shipped_path), "w", encoding="utf-8") as f:
                            f.write(incumbent)
                        removed = "restored-incumbent"
                    except OSError:
                        removed = False
                else:
                    try:
                        os.remove(_win_long(shipped_path)); removed = True
                    except OSError:
                        removed = False
            # A30: restore the staging husk the ship retired, so `awaiting` is a valid, re-shippable
            # state again (reconcile expects an awaiting item to carry its draft). Backward-compatible:
            # pre-A30 pointers lack staging_archived -> nothing to restore (the husk was left in place
            # by the old leak). Never clobber an existing draft on disk.
            sa, fs = pointer.get("staging_archived"), pointer.get("from_staging")
            if sa and fs and _file_present(sa) and not _file_present(fs):
                try:
                    os.makedirs(os.path.dirname(_win_long(fs)) or ".", exist_ok=True)
                    shutil.move(_win_long(sa), _win_long(fs))
                except OSError:
                    pass
            _clear_for_reset(it, to_stage)
            it["stage"] = to_stage
            it["claimed_by"] = None
            it["claimed_at"] = None
            it.setdefault("history", []).append({
                "ts": now, "stage": to_stage, "by": "rewind", "snap_id": sid,
                "undo_of": "shipped",   # A97: durable ship→undo marker — honest revert capture reads
                "note": f"undo-ship (removed_vault_file={removed})",   # this, not the terminal stage
            })
    if not found:
        _die(f"id not found: {cid}")
    _write_snapshot(snap_dir, {
        "snap_id": sid, "ts": now, "op": "undo-ship", "id": cid, "shipped_path": shipped_path,
        "removed_vault_file": removed, "queue_path": os.path.abspath(queue_path), "items": snap_items,
    })
    if _file_present(rp):
        try: os.remove(rp)            # the revert pointer has been consumed
        except OSError: pass
    _commit(d, queue_path)
    print(f"undo-ship {cid}: vault_file_removed={removed}, item -> {to_stage}, snapshot {sid}")
    return sid


def _reconcile_scan(d, queue_path, vault_root, kb_map=None):
    """One detection pass -> (awaiting-w/o-draft ids, shipped-w/o-file ids).

    kb_map (A19): the profile's `vault.live_kb_map` (kb short name -> vault folder), REQUIRED for a
    live-vault scan — without it every legacy item whose draft sits at `01_Personal/...` etc. reads
    as missing and a `reconcile --apply` bounces healthy items. `draft_path` (already real-folder
    vault-relative per A13) is honored as-is; only the short-name fallback join and the
    conflict_key join are mapped."""
    staging_missing, ship_missing = [], []
    for it in d["queue"]:
        # A96: a proposal is a Notion-write lifecycle with NO vault file at any stage — reconcile
        # scans vault-file-vs-queue, so it must skip proposals. Otherwise a shipped proposal (no
        # file by design) reads as `ship_missing`, `reconcile --apply` resets it to `awaiting`, and
        # it re-surfaces in the panel → duplicate Notion task, defeating the dedupe promise.
        if it.get("kind") == "proposal":
            continue
        ck = it.get("conflict_key") or ""
        slug = os.path.splitext(os.path.basename(ck))[0]
        kb = it.get("kb") or (ck.split("/")[0] if "/" in ck else "")
        if it.get("stage") == "awaiting":
            if it.get("draftless") is True:
                continue   # declared action-proposal (garden de-bloat/prune) — no draft by design
            dp = it.get("draft_path")
            staging = (os.path.join(vault_root, dp) if isinstance(dp, str) and dp.strip()
                       else os.path.join(vault_root, _kb_folder(kb, kb_map), "wiki", "staging", slug + ".md"))
            if not _file_present(staging):
                staging_missing.append(it.get("id"))
        elif it.get("stage") == "shipped":
            if it.get("retired") is True:
                continue   # deliberate lifecycle exit (distill-retire / prune) — not corruption (A28)
            p = _ck_path(vault_root, ck, kb_map)
            # legacy extensionless conflict_keys (pre-schema-tightening) identify the same note —
            # tolerate the missing .md rather than flag a healthy ship (A19)
            if ck and not (_file_present(p)
                           or (not ck.endswith(".md") and _file_present(p + ".md"))):
                ship_missing.append(it.get("id"))
    return staging_missing, ship_missing


def mark_retired(queue_path, ids=None, ck=None, note="", snap_dir=None):
    """Mark shipped item(s) as a deliberate lifecycle exit (`retired: true`) so reconcile stops
    reading their missing vault file as corruption. Stage stays `shipped`. By explicit ids (a
    missing id is fatal, like reset) or by conflict_key (every SHIPPED item with that ck; zero
    matches is a no-op — the gate can call it idempotently). Already-retired items are skipped.
    Snapshotted (undoable). -> (marked_ids, snap_id or None)."""
    if not ids and not ck:
        _die("mark-retired needs ids or --ck")
    snap_dir = snap_dir or _default_snap_dir(queue_path)
    d = queue_tx.load(queue_path)
    sid, now = _snap_id(), _now()
    snap_items, marked = [], []
    want = set(ids or [])
    for it in d["queue"]:
        hit = (it.get("id") in want) if ids else (
            it.get("stage") == "shipped" and it.get("conflict_key") == ck)
        if not hit or it.get("retired") is True:
            continue
        snap_items.append(copy.deepcopy(it))
        it["retired"] = True
        it.setdefault("history", []).append({
            "ts": now, "stage": it.get("stage"), "by": "rewind", "snap_id": sid,
            "note": "retired (lifecycle exit)" + (f": {note}" if note else ""),
        })
        marked.append(it.get("id"))
    if ids:
        missing = want - set(marked)
        already = {it.get("id") for it in d["queue"]
                   if it.get("id") in missing and it.get("retired") is True}
        if missing - already:
            _die(f"ids not found (no changes committed): {sorted(missing - already)}")
    if not marked:
        print(f"mark-retired: nothing to mark ({'ids already retired' if ids else f'no shipped item with ck {ck!r}'})")
        return [], None
    _write_snapshot(snap_dir, {
        "snap_id": sid, "ts": now, "op": "mark-retired", "ck": ck, "note": note,
        "queue_path": os.path.abspath(queue_path), "items": snap_items,
    })
    _commit(d, queue_path)
    print(f"mark-retired: {len(marked)} item(s) marked: {marked}")
    print(f"snapshot {sid}  (undo: rewind.py undo {queue_path} {sid})")
    return marked, sid


def _retire_evidence(vault_root, it, kb_map):
    """The distill-retire archive convention as migration evidence: the item's slug surviving at
    `<kb>/raw/archive/wiki-sources-retired-*/<slug>.md`. -> archived path or None."""
    ck = it.get("conflict_key") or ""
    slug = os.path.splitext(os.path.basename(ck))[0]
    kb = it.get("kb") or (ck.split("/")[0] if "/" in ck else "")
    if not slug or not kb:
        return None
    pat = os.path.join(vault_root, _kb_folder(kb, kb_map), "raw", "archive",
                       "wiki-sources-retired-*", slug + ".md")
    hits = sorted(glob.glob(pat))
    return hits[0] if hits and _file_present(hits[0]) else None


def reconcile(queue_path, vault_root, apply=False, snap_dir=None, kb_map=None,
              migrate_retired=False):
    """Detect state/file desyncs and (with --apply) self-heal them by rewinding:
         awaiting item with NO staging draft  -> rewind to sorted   (the godaddy class)
         shipped item with NO vault file       -> rewind to awaiting
       File presence is tested by READING (see _file_present), never os.path.exists.

       LOOP-UNTIL-STABLE (G7 hardening): a fully-phantom `shipped` item (no vault file AND no staging
       draft) needs two rewinds — shipped->awaiting, then awaiting->sorted. With --apply we repeat the
       scan+heal until a pass finds nothing (bounded), so one call fully settles the queue."""
    d = queue_tx.load(queue_path)
    staging_missing, ship_missing = _reconcile_scan(d, queue_path, vault_root, kb_map)
    n_retired = sum(1 for it in d["queue"]
                    if it.get("stage") == "shipped" and it.get("retired") is True)
    print("RECONCILE report:")
    print(f"  awaiting w/o staging draft : {staging_missing}")
    print(f"  shipped  w/o vault file    : {ship_missing}")
    if n_retired:
        print(f"  retired (skipped by design): {n_retired}")
    evidenced = []
    if migrate_retired and ship_missing:
        by_id = {it.get("id"): it for it in d["queue"]}
        evidenced = [cid for cid in ship_missing if _retire_evidence(vault_root, by_id[cid], kb_map)]
        print(f"  migrate-retired: EVIDENCED  : {evidenced}   (archived husk found)")
        print(f"  migrate-retired: no evidence: {sorted(set(ship_missing) - set(evidenced))}")
    if not apply:
        print("  (dry-run; pass --apply to rewind these)")
        return staging_missing, ship_missing
    if evidenced:
        mark_retired(queue_path, ids=evidenced,
                     note="reconcile --migrate-retired: archived husk evidences a distill-retire",
                     snap_dir=snap_dir)
        d = queue_tx.load(queue_path)
        staging_missing, ship_missing = _reconcile_scan(d, queue_path, vault_root, kb_map)
    all_staging, all_ship = set(staging_missing), set(ship_missing)
    passes = 0
    while (staging_missing or ship_missing) and passes < 10:
        if staging_missing:
            reset(queue_path, staging_missing, "sorted",
                  "reconcile: awaiting but no staging draft on disk", snap_dir)
        if ship_missing:
            reset(queue_path, ship_missing, "awaiting",
                  "reconcile: shipped but no vault file on disk", snap_dir)
        passes += 1
        d = queue_tx.load(queue_path)
        staging_missing, ship_missing = _reconcile_scan(d, queue_path, vault_root, kb_map)
        all_staging |= set(staging_missing); all_ship |= set(ship_missing)
    if staging_missing or ship_missing:
        print(f"  WARN: not stable after {passes} passes (still: awaiting={staging_missing} shipped={ship_missing})")
    else:
        print(f"  settled after {passes} pass(es).")
    return sorted(all_staging), sorted(all_ship)


def _list(snap_dir):
    rows = sorted(glob.glob(os.path.join(snap_dir, "rewind-*.json")))
    if not rows:
        print(f"(no snapshots in {snap_dir})"); return
    for p in rows:
        s = json.load(open(p, encoding="utf-8"))
        ids = [x["id"] for x in s.get("items", [])]
        print(f'{s["snap_id"]}  {s["op"]:<10} -> {s.get("to_stage",""):<9} ids={ids}')


def _pop_flag(args, name):
    if name in args:
        i = args.index(name); val = args[i + 1]; del args[i:i + 2]; return val
    return None


from _util import utf8_stdio as _utf8_stdio


if __name__ == "__main__":
    _utf8_stdio()
    a = sys.argv[1:]
    snap_dir = _pop_flag(a, "--snap-dir")
    kb_map_raw = _pop_flag(a, "--kb-map")   # A19: the profile's vault.live_kb_map as a JSON object
    kb_map = json.loads(kb_map_raw) if kb_map_raw else None
    ck = _pop_flag(a, "--ck")               # A28: mark-retired by conflict_key
    apply = "--apply" in a
    if apply:
        a.remove("--apply")
    migrate = "--migrate-retired" in a      # A28: evidence-based retire migration
    if migrate:
        a.remove("--migrate-retired")
    op = a[0] if a else ""
    if op == "reset":
        reset(a[1], a[2].split(","), a[3], a[4] if len(a) > 4 else "", snap_dir)
    elif op == "undo-ship":
        undo_ship(a[1], a[2], a[3], a[4], a[5] if len(a) > 5 else "awaiting", snap_dir, kb_map)
    elif op == "reconcile":
        reconcile(a[1], a[2], apply, snap_dir, kb_map, migrate)
    elif op == "mark-retired":
        if ck:
            mark_retired(a[1], ck=ck, note=a[2] if len(a) > 2 else "", snap_dir=snap_dir)
        else:
            mark_retired(a[1], ids=a[2].split(","), note=a[3] if len(a) > 3 else "", snap_dir=snap_dir)
    elif op == "undo":
        undo(a[1], a[2], snap_dir)
    elif op == "list":
        _list(snap_dir or _default_snap_dir(os.getcwd()))
    else:
        print(__doc__); sys.exit(1)
