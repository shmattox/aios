#!/usr/bin/env python3
"""sort.py — the deterministic half of the SORT stage (A25).

The sort tables lived as prose in three deploy bodies, executed per-item by a model. They are
lookups; this tool owns them: conflict_key derivation from a raw's declared type (the type→path
table), the lane proposal (auto-ship-kb membership + economic-signal escalation + confirm
signal + the journal-collision check), and the finalization through lane_policy's
resolve_review_gate/gate_to_lane. The model is retained ONLY for ambiguous type classification —
`run` reports those as `needs_judgment` and the model finalizes each with `one --ck`.

Ops (fact-free — every path/map/list is an argument):
  run  process every `captured` item whose raw declares a routable type (or is a pre-keyed
       session-record): derive conflict_key + lane, flip to `sorted`, ONE atomic queue update.
       Prints JSON {sorted, needs_judgment: [{id, payload_path, reason, excerpt}], by_kb}.
  one  finalize ONE item the model classified: --ck is the judged wiki target; the tool still
       owns the lane decision and the flip.

Usage:
  python sort.py run --queue Q --vault-root V --kb-map '{"dev":"03_Dev",...}' \
                     --auto-ship-kbs '["dev","personal"]' [--review-gates '{"dev":"collapsed"}']
  python sort.py one --queue Q --vault-root V --kb-map '…' --auto-ship-kbs '…' \
                     --id ID --ck '<kb>/wiki/<type>/<slug>.md'
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import queue_tx
import lane_policy
from frontmatter import read_frontmatter as _frontmatter  # the one guarded flat-frontmatter reader

EXCERPT_CHARS = 600

# type → wiki folder. "software-like" types route to entities/ ONLY where the KB keeps that
# folder on disk; a KB that folded it away makes the item ambiguous (vendor→companies vs
# distilled→knowledge is a judgment call) → needs_judgment.
TYPE_PATHS = {
    "person": "people",
    "organization": "companies", "company": "companies", "vendor": "companies",
    "idea": "knowledge", "method": "knowledge", "concept": "knowledge", "knowledge": "knowledge",
    "article": "sources", "thread": "sources", "notice": "sources", "source": "sources",
    "bookmark": "sources", "email": "sources",
}
ENTITY_TYPES = {"software", "tool", "product", "system"}
CONFIRM_TYPES = {"appointment", "schedule", "event"}


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


def _read_text(path):
    """Read a raw payload's text for sorting. `errors="replace"` (A49): the raw may be a non-UTF-8
    stub (cp1252 mail / a WhatsApp export); a strict read would make Sort re-flag it 'raw unreadable'
    EVERY run forever. Replacing the undecodable bytes lets it sort. Content only — never queue/ledger."""
    try:
        with open(_win_long(path), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s[:80]


def _resolve_raw(vault_root, payload_path):
    """Vault-relative read with the legacy absolute-path tolerance (segment from a KB folder on)."""
    if not payload_path:
        return None
    p = str(payload_path)
    if os.path.isabs(p):
        parts = re.split(r"[\\/]", p)
        for i, seg in enumerate(parts):
            if seg == "00_Inbox" or re.match(r"^\d\d_", seg):
                p = "/".join(parts[i:])
                break
        else:
            return _read_text(p)
    return _read_text(os.path.join(vault_root, p.replace("/", os.sep)))


def propose_lane(kb, auto_ship_kbs, econ_hit, confirm_signal, collision):
    """The kb→lane proposal table, plus the escalation signals. Deterministic."""
    if econ_hit or collision:
        return "review"
    if kb not in auto_ship_kbs:
        return "review"
    if confirm_signal:
        return "confirm"
    return "auto-ship"


def _finalize_lane(kb, proposed, auto_ship_kbs, review_gates):
    gate = lane_policy.resolve_review_gate(kb, profile_gates=review_gates,
                                           auto_ship_kbs=frozenset(auto_ship_kbs))
    return lane_policy.gate_to_lane(gate, proposed)


def _journal_collision(vault_root, kb_map, ck):
    """A session-record whose daily-note target already exists non-empty → human confirms the merge."""
    if "/wiki/journal/" not in ("/" + ck):
        return False
    kb, _, rel = ck.partition("/")
    folder = kb_map.get(kb)
    if not folder:
        return False
    rel_md = rel if rel.endswith(".md") else rel + ".md"
    target = os.path.join(vault_root, folder, rel_md.replace("/", os.sep))
    try:
        return os.path.getsize(_win_long(target)) > 0
    except OSError:
        return False


def _sort_item(item, vault_root, kb_map, auto_ship_kbs, review_gates):
    """Returns (sorted_item, None) or (None, needs_judgment_record)."""
    kb = item.get("kb")
    raw = _resolve_raw(vault_root, item.get("payload_path"))
    fm = _frontmatter(raw or "")
    rtype = (fm.get("type") or "").lower()

    def needs(reason):
        return None, {"id": item.get("id"), "payload_path": item.get("payload_path"),
                      "reason": reason, "excerpt": (raw or "")[:EXCERPT_CHARS]}

    if raw is None:
        return needs("raw unreadable at payload_path")

    if rtype == "session-record" or fm.get("conflict_key"):
        ck = fm.get("conflict_key")                    # pre-keyed pass-through — use verbatim
        if not ck:
            return needs("session-record with no conflict_key in frontmatter")
        kb = ck.partition("/")[0]                      # the carried key also fixes a missing kb
    elif not kb:
        return needs("item carries no kb")
    elif rtype in TYPE_PATHS:
        slug = _slugify(fm.get("title")) or _slugify(
            os.path.splitext(os.path.basename(str(item.get("payload_path"))))[0])
        if not slug:
            return needs("no title/filename to derive a slug from")
        ck = f"{kb}/wiki/{TYPE_PATHS[rtype]}/{slug}.md"
    elif rtype in ENTITY_TYPES:
        folder = kb_map.get(kb)
        if folder and os.path.isdir(os.path.join(vault_root, folder, "wiki", "entities")):
            slug = _slugify(fm.get("title")) or _slugify(
                os.path.splitext(os.path.basename(str(item.get("payload_path"))))[0])
            ck = f"{kb}/wiki/entities/{slug}.md"
        else:
            return needs(f"type {rtype!r} in a KB without entities/ — vendor vs knowledge is a judgment call")
    else:
        return needs(f"undeclared/unknown type {rtype!r} — classify, then `sort.py one --ck`")

    econ_hit = bool(lane_policy.ECONOMIC_TRIPWIRE_RE.search(raw or ""))
    collision = _journal_collision(vault_root, kb_map, ck)
    proposed = propose_lane(kb, auto_ship_kbs, econ_hit, rtype in CONFIRM_TYPES, collision)
    lane = _finalize_lane(kb, proposed, auto_ship_kbs, review_gates)

    out = dict(item)
    out.update(kb=kb, conflict_key=ck, lane=lane, stage="sorted")
    out.setdefault("history", []).append({"ts": _now(), "stage": "sorted", "kb": kb})
    return out, None


def run(queue_path, vault_root, kb_map, auto_ship_kbs, review_gates, limit=None):
    data = queue_tx.load(queue_path)
    captured = [it for it in data["queue"] if it.get("stage") == "captured"]
    if limit:
        captured = captured[:int(limit)]
    sorted_items, needs_judgment = [], []
    for it in captured:
        done, needs = _sort_item(it, vault_root, kb_map, auto_ship_kbs, review_gates)
        (sorted_items.append(done) if done else needs_judgment.append(needs))
    if sorted_items:
        queue_tx._apply_items(queue_path, sorted_items, "update")
    by_kb = {}
    for it in sorted_items:
        by_kb[it["kb"]] = by_kb.get(it["kb"], 0) + 1
    print(json.dumps({"ok": True, "sorted": len(sorted_items), "by_kb": by_kb,
                      "review_laned": sum(1 for i in sorted_items if i["lane"] == "review"),
                      "needs_judgment": needs_judgment}, ensure_ascii=False, indent=2))


def one(queue_path, vault_root, kb_map, auto_ship_kbs, review_gates, cid, ck):
    data = queue_tx.load(queue_path)
    item = next((it for it in data["queue"] if it.get("id") == cid), None)
    if item is None:
        _die(f"id {cid!r} not found")
    if item.get("stage") != "captured":
        _die(f"id {cid!r} is at stage {item.get('stage')!r} — sort acts on 'captured' only")
    kb = ck.partition("/")[0]
    raw = _resolve_raw(vault_root, item.get("payload_path")) or ""
    econ_hit = bool(lane_policy.ECONOMIC_TRIPWIRE_RE.search(raw))
    collision = _journal_collision(vault_root, kb_map, ck)
    rtype = (_frontmatter(raw).get("type") or "").lower()
    proposed = propose_lane(kb, auto_ship_kbs, econ_hit, rtype in CONFIRM_TYPES, collision)
    lane = _finalize_lane(kb, proposed, auto_ship_kbs, review_gates)
    out = dict(item)
    out.update(kb=kb, conflict_key=ck, lane=lane, stage="sorted")
    out.setdefault("history", []).append({"ts": _now(), "stage": "sorted", "kb": kb})
    queue_tx._apply_items(queue_path, [out], "update")
    print(json.dumps({"ok": True, "id": cid, "conflict_key": ck, "lane": lane},
                     ensure_ascii=False))


def _utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def main(argv=None):
    _utf8_stdio()
    ap = argparse.ArgumentParser(prog="sort.py",
                                 description="Deterministic sort-stage tables (A25).")
    sub = ap.add_subparsers(dest="op", required=True)

    def common(p):
        p.add_argument("--queue", required=True)
        p.add_argument("--vault-root", required=True)
        p.add_argument("--kb-map", required=True)
        p.add_argument("--auto-ship-kbs", required=True,
                       help='JSON list, e.g. ["dev","personal"] (empty = everything review)')
        p.add_argument("--review-gates", default=None,
                       help='optional JSON map kb -> full|collapsed (profile review_gates)')

    pr = sub.add_parser("run"); common(pr)
    pr.add_argument("--limit", type=int, default=None)
    po = sub.add_parser("one"); common(po)
    po.add_argument("--id", required=True)
    po.add_argument("--ck", required=True)
    args = ap.parse_args(argv)

    try:
        kb_map = json.loads(args.kb_map); assert isinstance(kb_map, dict)
        auto = json.loads(args.auto_ship_kbs); assert isinstance(auto, list)
        gates = json.loads(args.review_gates) if args.review_gates else None
        assert gates is None or isinstance(gates, dict)
    except (ValueError, AssertionError):
        _die("--kb-map must be a JSON object; --auto-ship-kbs a JSON list; --review-gates a JSON object")

    if args.op == "run":
        run(args.queue, args.vault_root, kb_map, auto, gates, limit=args.limit)
    else:
        one(args.queue, args.vault_root, kb_map, auto, gates, args.id, args.ck)
    return 0


if __name__ == "__main__":
    sys.exit(main())
