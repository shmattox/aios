#!/usr/bin/env python3
"""ship.py — the deterministic SHIP mechanics of the Phase-B gate (A25).

The gate's judgment (independent PASS/BLOCK review, lane decision via lane_policy) stays with
the model; everything mechanical about acting on that decision lives here, tested once:
slug/target resolution from the conflict_key, draft location (draft_path + legacy staging
fallback), the daily-note MERGE guard (never clobber an incumbent journal note), the revert
pointer, and the queue flip through queue_tx.

Ops (all fact-free — every path/map is an argument):
  resolve  print JSON facts for one candidate (slug, target, draft found?, excerpt for the
           economic-tripwire enrichment, journal?) — read-only, the model decides from this.
  ship     write the canonical page (replace, or delimited MERGE for an existing journal
           note with a pre-merge copy), write the revert pointer, flip the item to `shipped`.
  reject   flip the item to `rejected` with the BLOCK reason.

Usage:
  python ship.py resolve --queue Q --vault-root V --kb-map '{"dev":"03_Dev",...}' --id ID
  python ship.py ship    --queue Q --vault-root V --kb-map '…' --id ID --approved-by WHO
                         [--revert-dir D]
  python ship.py reject  --queue Q --id ID --reason "…"

A kb missing from --kb-map is an ERROR (hold + flag), never a fallback vault.
"""
import argparse
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import queue_tx

EXCERPT_CHARS = 4000
MERGE_DELIM = "\n\n---\n\n"


def _die(msg):
    print("FAIL:", msg)
    sys.exit(1)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _win_long(path):
    """Windows long-path shim (parity with rewind.py) — deep vaults exceed MAX_PATH."""
    if os.name == "nt":
        ap = os.path.abspath(path)
        if len(ap) > 250 and not ap.startswith("\\\\"):
            return "\\\\?\\" + ap
    return path


def _read(path):
    with open(_win_long(path), encoding="utf-8") as f:
        return f.read()


def _present(path):
    try:
        return os.path.getsize(_win_long(path)) > 0
    except OSError:
        return False


def _draft_supersets(draft_text, incumbent):
    """A43: True when the draft body contains every non-blank line of the incumbent body — i.e. the
    draft is a COMPLETE re-draft (the late-capture-append pattern re-emits the whole note + a new
    section), not a delta. In that case a delimited APPEND would duplicate the entire note (two H1s),
    so the caller REPLACES instead — safe because every distinct incumbent body line is confirmed
    present in the draft (set coverage). Bias is toward APPEND: a re-draft that reflows/edits a line
    fails the check and falls through to the (non-lossy) append path, never to content loss. Compares
    frontmatter-stripped bodies by normalized (stripped) non-blank lines; an empty incumbent is never
    a superset (nothing to duplicate)."""
    inc_lines = {ln.strip() for ln in _strip_frontmatter(incumbent).splitlines() if ln.strip()}
    if not inc_lines:
        return False
    draft_lines = {ln.strip() for ln in _strip_frontmatter(draft_text).splitlines() if ln.strip()}
    return inc_lines <= draft_lines


def _strip_frontmatter(text):
    """Body without a leading ---…--- block (a merged entry must not embed frontmatter)."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


def _find_item(queue_path, cid):
    data = queue_tx.load(queue_path)
    for it in data["queue"]:
        if it.get("id") == cid:
            return it
    _die(f"id {cid!r} not found in {queue_path}")


def _resolve_facts(item, vault_root, kb_map):
    """All mechanical facts for one candidate. Fails loud on an unmapped kb."""
    ck = item.get("conflict_key") or ""
    kb, _, rel = ck.partition("/")
    if not rel:
        _die(f"item {item.get('id')!r}: conflict_key {ck!r} is not <kb>/wiki/... shaped")
    if kb not in kb_map:
        _die(f"item {item.get('id')!r}: kb {kb!r} not in --kb-map (hold + flag; never a "
             f"fallback vault)")
    base = os.path.join(vault_root, kb_map[kb])
    slug = os.path.basename(ck)
    if slug.endswith(".md"):
        slug = slug[:-3]                     # the `.md.md` staging-lookup bug class, deleted here
    rel_md = rel if rel.endswith(".md") else rel + ".md"
    target = os.path.join(base, rel_md.replace("/", os.sep))
    dp = item.get("draft_path")
    if isinstance(dp, str) and dp.strip():
        draft = os.path.join(vault_root, dp.strip().replace("/", os.sep))
    else:
        draft = os.path.join(base, "wiki", "staging", slug + ".md")   # legacy fallback
    return {
        "id": item.get("id"), "kb": kb, "kb_folder": kb_map[kb], "slug": slug,
        "target_path": target, "target_exists": _present(target),
        "draft_path": draft, "draft_found": _present(draft),
        "is_journal": "/wiki/journal/" in ("/" + rel_md),
    }


def resolve(queue_path, vault_root, kb_map, cid):
    item = _find_item(queue_path, cid)
    facts = _resolve_facts(item, vault_root, kb_map)
    if facts["draft_found"]:
        facts["draft_excerpt"] = _read(facts["draft_path"])[:EXCERPT_CHARS]
    print(json.dumps(facts, ensure_ascii=False, indent=2))


def _derive_decided_by(approved_by, human_approved):
    """A73 normalized decider stamp. approved_by is either an auto constant or the approver's
    name (gate SKILL contract) — any named approver is a human decision even on a non-review
    lane. Free-text approved_by stays for audit; history is never rewritten."""
    if human_approved:
        return "human"
    if approved_by == "auto-ship-scheduled":
        return "scheduled"
    if approved_by == "auto-ship":
        return "auto"
    return "human"


def _flip(queue_path, item, stage, history_extra):
    item["stage"] = stage
    item.setdefault("history", []).append({"ts": _now(), "stage": stage, **history_extra})
    queue_tx._apply_items(queue_path, [item], "update")


def ship(queue_path, vault_root, kb_map, cid, approved_by, revert_dir, human_approved=False):
    item = _find_item(queue_path, cid)
    if item.get("stage") != "awaiting":
        _die(f"id {cid!r} is at stage {item.get('stage')!r} — only 'awaiting' items ship")
    if item.get("lane") == "review" and not human_approved:
        # tested backstop for the lane_policy division of labor: an unattended caller can never
        # ship a review-lane item; the manual gate passes --human-approved after the human's call
        _die(f"id {cid!r} is on the 'review' lane — a review-lane ship requires explicit human "
             f"approval (--human-approved); unattended runs must leave it for the manual gate")
    facts = _resolve_facts(item, vault_root, kb_map)
    if not facts["draft_found"]:
        _die(f"id {cid!r}: no draft found at {facts['draft_path']} — use `reject`, not `ship`")
    draft_text = _read(facts["draft_path"])
    os.makedirs(revert_dir, exist_ok=True)
    target = facts["target_path"]
    merged, prev_copy = False, None
    if facts["is_journal"] and facts["target_exists"]:
        # DAILY-NOTE MERGE GUARD: preserve the incumbent verbatim, append a delimited entry.
        incumbent = _read(target)
        prev_copy = os.path.join(revert_dir, f"{cid}.prev.md")
        with open(_win_long(prev_copy), "w", encoding="utf-8") as f:
            f.write(incumbent)
        if _draft_supersets(draft_text, incumbent):
            # A43: the draft already contains the whole incumbent (a complete re-draft) — appending
            # would duplicate the note. Replace with the draft; the pre-merge copy above keeps undo
            # revertible. merged stays True (the target existed and this is the merge path).
            content = draft_text
        else:
            content = (incumbent.rstrip() + MERGE_DELIM
                       + f"<!-- merged by aios gate: {cid} @ {_now()} -->\n\n"
                       + _strip_frontmatter(draft_text).strip() + "\n")
        merged = True
    else:
        content = draft_text
    os.makedirs(os.path.dirname(_win_long(target)) or ".", exist_ok=True)
    with open(_win_long(target), "w", encoding="utf-8") as f:
        f.write(content)
    if not _present(target):
        _die(f"id {cid!r}: canonical write did not land at {target}")
    # A30: the canonical is confirmed on disk, so this ship WILL retire the staging husk (move it
    # into the revert dir so undo-ship can restore it — move, never delete). Record the intent in
    # the pointer now, but perform the MOVE last (after the queue flip) — see below.
    src_draft = facts["draft_path"]
    will_retire = os.path.abspath(src_draft) != os.path.abspath(target) and _present(src_draft)
    staging_archived = os.path.join(revert_dir, f"{cid}.staging.md") if will_retire else None
    pointer = {"id": cid, "shipped_path": target,
               "from_staging": facts["draft_path"], "merged": merged,
               "prev_content_path": prev_copy, "staging_archived": staging_archived, "ts": _now()}
    pointer_path = os.path.join(revert_dir, f"{cid}.json")
    with open(_win_long(pointer_path), "w", encoding="utf-8") as f:
        json.dump(pointer, f, indent=2, ensure_ascii=False)
    _flip(queue_path, item, "shipped",
          {"approved_by": approved_by,
           "decided_by": _derive_decided_by(approved_by, human_approved)})
    # A30: retire the husk LAST — after the flip — fenced fail-closed (the A23 liveness lesson). A
    # crash BEFORE the flip leaves a clean `awaiting` item WITH its draft (re-shippable); a crash
    # AFTER the flip leaves a `shipped` item with a benign in-place husk (reconcile ignores shipped
    # husks). Guarded so a move error can't fail an already-successful ship (husk stays = benign).
    if will_retire:
        try:
            shutil.move(_win_long(src_draft), _win_long(staging_archived))
        except OSError:
            pass
    print(json.dumps({"ok": True, "id": cid, "shipped_path": target, "merged": merged,
                      "revert_pointer": pointer_path}, ensure_ascii=False))


def reject(queue_path, cid, reason, decided_by="auto"):
    item = _find_item(queue_path, cid)
    if item.get("stage") in ("shipped", "reverted"):
        _die(f"id {cid!r} is at terminal stage {item.get('stage')!r} — rejecting it would orphan "
             f"its vault file; use `rewind.py undo-ship` first")
    _flip(queue_path, item, "rejected", {"reason": reason, "decided_by": decided_by})
    print(json.dumps({"ok": True, "id": cid, "stage": "rejected", "reason": reason},
                     ensure_ascii=False))


def _utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def main(argv=None):
    _utf8_stdio()
    ap = argparse.ArgumentParser(prog="ship.py",
                                 description="Deterministic gate ship mechanics (A25).")
    sub = ap.add_subparsers(dest="op", required=True)

    def common(p, vault=True):
        p.add_argument("--queue", required=True)
        p.add_argument("--id", required=True)
        if vault:
            p.add_argument("--vault-root", required=True)
            p.add_argument("--kb-map", required=True,
                           help='JSON object, e.g. {"dev":"03_Dev"}')

    pr = sub.add_parser("resolve"); common(pr)
    ps = sub.add_parser("ship"); common(ps)
    ps.add_argument("--approved-by", required=True)
    ps.add_argument("--revert-dir", default=None,
                    help="default: <queue dir>/revert")
    ps.add_argument("--human-approved", action="store_true",
                    help="required to ship a review-lane item (manual gate only)")
    pj = sub.add_parser("reject"); common(pj, vault=False)
    pj.add_argument("--reason", required=True)
    pj.add_argument("--decided-by", choices=("human", "auto"), default="auto",
                    help="A73: who decided this reject (manual gate passes human)")
    args = ap.parse_args(argv)

    if args.op == "reject":
        reject(args.queue, args.id, args.reason, decided_by=args.decided_by)
        return 0
    try:
        kb_map = json.loads(args.kb_map)
        assert isinstance(kb_map, dict)
    except (ValueError, AssertionError):
        _die("--kb-map must be a JSON object of kb -> vault folder")
    if args.op == "resolve":
        resolve(args.queue, args.vault_root, kb_map, args.id)
    else:
        revert_dir = args.revert_dir or os.path.join(
            os.path.dirname(os.path.abspath(args.queue)), "revert")
        ship(args.queue, args.vault_root, kb_map, args.id, args.approved_by, revert_dir,
             human_approved=args.human_approved)
    return 0


if __name__ == "__main__":
    sys.exit(main())
