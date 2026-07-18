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


_MONEY_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s?(k|m|mm|bn|b|thousand|million|billion)?", re.I)
_MONEY_MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6,
               "b": 1e9, "bn": 1e9, "billion": 1e9}
_LINK_FIELDS = ("papered_source", "entity", "entities", "owner_entity")


def _body_len(raw):
    """Char count of the body (frontmatter stripped) — the A89 length signal."""
    text = raw or ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    return len(text.strip())


def _max_dollar(raw):
    """Largest currency magnitude in the body (0.0 if none), k/m/bn suffixes honored so a `$5k`
    charge is not under-counted as $5 and wrongly floored."""
    vals = []
    for m in _MONEY_RE.finditer(raw or ""):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        vals.append(v * _MONEY_MULT.get((m.group(2) or "").lower(), 1))
    return max(vals) if vals else 0.0


def _entity_or_paper_linked(fm, raw):
    """A89 invariant signal — a capture naming a known entity / carrying a papered_source is never
    floored (Paper-Governs subordinate). Covers an inline scalar / inline `[..]` list (via the flat
    reader) AND a BLOCK-style multi-line list (`entities:\\n  - X`), which the flat reader collapses to
    `""` — so the flat check alone would leak the invariant and floor an entity-linked capture."""
    if any(isinstance(fm.get(k), str) and fm.get(k).strip() for k in _LINK_FIELDS):
        return True
    lines = (raw or "").split("\n")
    if not lines or lines[0].strip() != "---":
        return False
    for i in range(1, len(lines)):
        ln = lines[i]
        if ln.strip() == "---":
            break
        if ln[:1] in (" ", "\t") or ":" not in ln:
            continue
        if ln.split(":", 1)[0].strip() in _LINK_FIELDS:
            for nxt in lines[i + 1:]:                 # any indented non-empty item before the next key?
                if nxt.strip() == "---" or (nxt.strip() and nxt[:1] not in (" ", "\t")):
                    break
                if nxt[:1] in (" ", "\t") and nxt.lstrip().lstrip("-").strip():
                    return True
    return False


def worthiness_floor(raw, fm, econ_hit, len_floor, dollar_floor):
    """A89: zero-LLM deterministic below-bar test. Returns (floored, signals|None). Below-bar iff
    length < LEN_FLOOR AND source_tier == tertiary AND (not economic) AND (no material $) AND not
    entity/paper-linked. INVARIANT: econ_hit / $≥DOLLAR_FLOOR / entity-or-paper-linked are NEVER
    floored (Paper-Governs subordinate). Disabled (never floors) when len_floor is unset/≤0 — the safe
    default is to floor nothing until a value is set."""
    if not len_floor or len_floor <= 0:
        return False, None                                   # floor disabled (safe default)
    if econ_hit or _entity_or_paper_linked(fm, raw):
        return False, None                                   # Paper-Governs subordinate — never floor
    if (fm.get("source_tier") or "").lower() != "tertiary":
        return False, None
    blen = _body_len(raw)
    if blen >= len_floor:
        return False, None
    md = _max_dollar(raw)
    dollar_below = (md < dollar_floor) if dollar_floor else (md == 0)
    if not dollar_below:
        return False, None
    return True, {"length": blen, "source_tier": "tertiary", "max_dollar": md,
                  "failing_bar": f"length<{len_floor} & tertiary & non-economic & "
                                 f"$<{dollar_floor or 'any'} & unlinked"}


def propose_lane(kb, auto_ship_kbs, econ_hit, confirm_signal, collision,
                 paper_governs=True, fo_entity_hit=False):
    """The kb→lane proposal table, plus the escalation signals. Deterministic.

    A99: an economic signal escalates to `review` ONLY when the KB is Paper-Governs, OR (for a
    non-Paper-Governs KB, e.g. the engine-dev KB) when the raw actually names a known FamilyOffice
    entity. This kills the 2026-07-12 false positive — the token "Paper-Governs" (governance vocab in
    the engine-dev KB) tripped the currency tripwire with no real economic content. A non-PG KB whose
    econ hit is pure vocabulary de-escalates to its normal kb-backstop lane; a real FamilyOffice-entity mention
    still escalates. The kb backstop below is unchanged (a non-auto-ship KB is still review)."""
    escalate_econ = econ_hit and (paper_governs or fo_entity_hit)
    if escalate_econ or collision:
        return "review"
    if kb not in auto_ship_kbs:
        return "review"
    if confirm_signal:
        return "confirm"
    return "auto-ship"


def _pg_flag(paper_governs, kb):
    """A99: a KB is Paper-Governs (escalate on econ) unless EXPLICITLY set to boolean false. A non-bool
    value (null / 0 / "") reads as the SAFE default True, not as de-escalate — an author who writes
    `null` meaning 'unset' must not silently get the opposite (review-caught foot-gun F5)."""
    v = (paper_governs or {}).get(kb, True)
    return v if isinstance(v, bool) else True


def _fm_list_items(raw, key):
    """Values of a frontmatter list `key`, covering BOTH inline `key: [a, b]` and BLOCK
    `key:\\n  - a`. The flat reader collapses a block list to "", so relying on it alone silently
    drops block-style entries (the A89 flat-reader class)."""
    out = []
    v = _frontmatter(raw or "").get(key)
    if isinstance(v, str) and v.strip():
        out.extend(t.strip().strip("'\"") for t in re.split(r"[\[\],]", v) if t.strip())
    lines = (raw or "").split("\n")
    for i, ln in enumerate(lines):
        if i > 0 and ln.strip() == "---":
            break
        if ln[:1] not in (" ", "\t") and ":" in ln and ln.split(":", 1)[0].strip() == key:
            for nxt in lines[i + 1:]:
                if nxt.strip() == "---" or (nxt.strip() and nxt[:1] not in (" ", "\t")):
                    break
                item = nxt.lstrip().lstrip("-").strip().strip("'\"")
                if nxt[:1] in (" ", "\t") and item:
                    out.append(item)
    return out


def _fo_entity_ids(vault_root, kb_map, fo_kb):
    """A99: the set of lowercased FamilyOffice entity identifiers (slug + title + inline AND block-style
    aliases) read from `<vault>/<fo_folder>/wiki/entities/*.md` at runtime. Fact-free (the fo_kb + path
    come from args); empty when unconfigured / no such folder, so a non-PG KB never escalates on econ
    alone unless the FamilyOffice-entity guard is actually wired. Short (<3-char) ids are dropped."""
    ids = set()
    folder = kb_map.get(fo_kb) if fo_kb else None
    if not folder:
        return ids
    ent_dir = os.path.join(vault_root, folder, "wiki", "entities")
    try:
        names = os.listdir(_win_long(ent_dir))
    except OSError:
        return ids
    for name in names:
        if not name.endswith(".md"):
            continue
        ids.add(os.path.splitext(name)[0].replace("-", " ").lower())
        raw = _read_text(os.path.join(ent_dir, name)) or ""
        title = _frontmatter(raw).get("title")
        if isinstance(title, str) and title.strip():
            ids.add(title.strip().lower())
        for a in _fm_list_items(raw, "aliases"):
            ids.add(a.lower())
    return {i for i in ids if len(i) >= 3}


def _fo_entity_hit(raw, entity_ids):
    """True if the raw text names any known FO entity identifier as a whole word (case-insensitive)."""
    if not entity_ids:
        return False
    low = (raw or "").lower()
    return any(re.search(r"\b" + re.escape(i) + r"\b", low) for i in entity_ids)


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


def _sort_item(item, vault_root, kb_map, auto_ship_kbs, review_gates,
               len_floor=0, dollar_floor=0, paper_governs=None, fo_entity_ids=None):
    """Returns (sorted_item, None) or (None, needs_judgment_record). A below-bar capture (A89) is
    returned as a `sorted_item` carrying stage `reference` (terminal) instead of `sorted`."""
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
    floored, floor_signals = worthiness_floor(raw, fm, econ_hit, len_floor, dollar_floor)
    if floored:
        # A89: below-bar, non-Paper-Governs capture → terminal `reference` (stays searchable in raw/,
        # never drafted, never gated). Keep the derived conflict_key (a re-open via rewind can draft it).
        out = dict(item)
        out.update(kb=kb, conflict_key=ck, lane=None, stage="reference")
        out.setdefault("history", []).append(
            {"ts": _now(), "stage": "reference", "kb": kb, "floor": floor_signals})
        return out, None

    collision = _journal_collision(vault_root, kb_map, ck)
    pg = _pg_flag(paper_governs, kb)   # A99: default Paper-Governs (escalate on econ) unless explicit false
    fo_hit = _fo_entity_hit(raw, fo_entity_ids) if (econ_hit and not pg) else False
    proposed = propose_lane(kb, auto_ship_kbs, econ_hit, rtype in CONFIRM_TYPES, collision,
                            paper_governs=pg, fo_entity_hit=fo_hit)
    lane = _finalize_lane(kb, proposed, auto_ship_kbs, review_gates)

    out = dict(item)
    out.update(kb=kb, conflict_key=ck, lane=lane, stage="sorted")
    out.setdefault("history", []).append({"ts": _now(), "stage": "sorted", "kb": kb})
    return out, None


def _log_floored(context_log, floored):
    """A89 no-silent-caps: append one JSONL line per floored item (id + signals + the failing bar) so
    a dropped draft leaves a trace. Best-effort — a log write must never fail the sort."""
    if not context_log or not floored:
        return
    try:
        os.makedirs(os.path.dirname(_win_long(context_log)) or ".", exist_ok=True)
        with open(_win_long(context_log), "a", encoding="utf-8") as f:
            for it in floored:
                sig = next((h.get("floor") for h in reversed(it.get("history", []))
                            if h.get("floor")), {})
                # no "stage" key on purpose — pipeline_health's run scan keys on `stage`, so a floored
                # trace must not read as a pipeline run; it is counted via its `event` instead.
                f.write(json.dumps({"ts": _now(), "event": "floored",
                                    "id": it.get("id"), "kb": it.get("kb"), "floor": sig},
                                   ensure_ascii=False) + "\n")
    except OSError:
        pass


def run(queue_path, vault_root, kb_map, auto_ship_kbs, review_gates, limit=None,
        len_floor=0, dollar_floor=0, context_log=None, paper_governs=None, fo_kb=None):
    data = queue_tx.load(queue_path)
    captured = [it for it in data["queue"] if it.get("stage") == "captured"]
    if limit:
        captured = captured[:int(limit)]
    # A99: load FO entity identifiers once per run (only needed when a non-PG KB has an econ hit).
    fo_entity_ids = _fo_entity_ids(vault_root, kb_map, fo_kb) if fo_kb else set()
    sorted_items, needs_judgment = [], []
    for it in captured:
        done, needs = _sort_item(it, vault_root, kb_map, auto_ship_kbs, review_gates,
                                 len_floor=len_floor, dollar_floor=dollar_floor,
                                 paper_governs=paper_governs, fo_entity_ids=fo_entity_ids)
        (sorted_items.append(done) if done else needs_judgment.append(needs))
    if sorted_items:
        queue_tx._apply_items(queue_path, sorted_items, "update")
    floored = [i for i in sorted_items if i.get("stage") == "reference"]
    _log_floored(context_log, floored)
    routed = [i for i in sorted_items if i.get("stage") == "sorted"]
    by_kb = {}
    for it in routed:
        by_kb[it["kb"]] = by_kb.get(it["kb"], 0) + 1
    print(json.dumps({"ok": True, "sorted": len(routed), "by_kb": by_kb,
                      "review_laned": sum(1 for i in routed if i["lane"] == "review"),
                      "floored": len(floored),
                      "floored_ids": [i.get("id") for i in floored],
                      "needs_judgment": needs_judgment}, ensure_ascii=False, indent=2))


def one(queue_path, vault_root, kb_map, auto_ship_kbs, review_gates, cid, ck,
        paper_governs=None, fo_kb=None):
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
    pg = _pg_flag(paper_governs, kb)   # A99: same de-escalation as the bulk `run` path
    fo_hit = (_fo_entity_hit(raw, _fo_entity_ids(vault_root, kb_map, fo_kb))
              if (econ_hit and not pg and fo_kb) else False)
    proposed = propose_lane(kb, auto_ship_kbs, econ_hit, rtype in CONFIRM_TYPES, collision,
                            paper_governs=pg, fo_entity_hit=fo_hit)
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
        # A99 KB-aware de-escalation. --paper-governs: JSON map kb -> bool (default true = escalate on
        # econ). --familyoffice-kb: the kb whose wiki/entities/ define the FamilyOffice-entity signal that lets a
        # NON-Paper-Governs kb still escalate a real FO mention. Both absent = pre-A99 behavior.
        p.add_argument("--paper-governs", default=None,
                       help='optional JSON map kb -> bool (default true; false de-escalates vocab-only econ hits)')
        p.add_argument("--familyoffice-kb", default=None,
                       help='kb key whose wiki/entities/ define the FamilyOffice-entity escalation signal (A99)')

    pr = sub.add_parser("run"); common(pr)
    pr.add_argument("--limit", type=int, default=None)
    # A89 worthiness floor — thresholds are profile knobs (fact-free). Unset/0 = floor disabled.
    pr.add_argument("--len-floor", type=int, default=0,
                    help="A89: body chars below this (with the other signals) → terminal `reference`")
    pr.add_argument("--dollar-floor", type=float, default=0,
                    help="A89: a $ amount at/above this is never floored (0 = any $ blocks flooring)")
    pr.add_argument("--context-log", default=None,
                    help="A89: append one floored-item line here (no-silent-caps trace)")
    po = sub.add_parser("one"); common(po)
    po.add_argument("--id", required=True)
    po.add_argument("--ck", required=True)
    args = ap.parse_args(argv)

    try:
        kb_map = json.loads(args.kb_map); assert isinstance(kb_map, dict)
        auto = json.loads(args.auto_ship_kbs); assert isinstance(auto, list)
        gates = json.loads(args.review_gates) if args.review_gates else None
        assert gates is None or isinstance(gates, dict)
        pgov = json.loads(args.paper_governs) if args.paper_governs else None
        assert pgov is None or isinstance(pgov, dict)
    except (ValueError, AssertionError):
        _die("--kb-map/--review-gates/--paper-governs must be JSON objects; --auto-ship-kbs a JSON list")

    if args.op == "run":
        run(args.queue, args.vault_root, kb_map, auto, gates, limit=args.limit,
            len_floor=args.len_floor, dollar_floor=args.dollar_floor, context_log=args.context_log,
            paper_governs=pgov, fo_kb=args.familyoffice_kb)
    else:
        one(args.queue, args.vault_root, kb_map, auto, gates, args.id, args.ck,
            paper_governs=pgov, fo_kb=args.familyoffice_kb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
