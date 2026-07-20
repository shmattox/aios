#!/usr/bin/env python3
"""paper_evidence.py — A75 Paper-Governs evidence verifier (read-only ingest-enrich leg).

**Advisory ONLY.** Attaching a `paper_evidence` packet NEVER changes an item's lane / stage /
recommended; a `matches` verdict never auto-approves. The FO backstop (a familyoffice item never
auto-ships) is untouched and independent of any verdict — this tool cannot become an auto-ship signal.

The deterministic engine owns the mechanical half: resolve an entity page's `papered_source` to its
LOCAL `raw/` projection, read it (cached per source so holds sharing one paper read it once — A76), and
attach a validated `{doc, section, quote, verdict, checked_utc}` packet to the queue item. The
matches|conflicts judgment is the cheap-tier extract-and-compare — the ingest skill reads the projection
this tool surfaces (via `resolve`) alongside the drafted claim and supplies verdict/section/quote to
`attach`. An absent / unreadable projection is `no-paper-found` (the honest residual; no Drive fallback —
fact-free, headless, zero interactive-auth).

Ops (fact-free — every path is an argument):
  resolve        --entity-page P --vault-root V  → JSON facts for ONE hold (papered_source,
                 projection_path, readable, projection_text?) — the model decides the verdict from this.
  resolve-batch  --entity-pages '["p1",...]' --vault-root V  → the same per page, sharing ONE cached
                 read per distinct projection (A76): the 2nd hold on a paper reports cache_hit=true.
  attach         --queue Q --id ID --verdict V [--doc D --section S --quote Q] → write the packet
                 (lane/stage/recommended left UNTOUCHED — the advisory invariant, enforced in code).
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import queue_tx
from frontmatter import read_frontmatter as _frontmatter

VERDICTS = ("matches", "conflicts", "no-paper-found")
PROJECTION_CHARS = 8000
_SOURCE_KEYS = ("papered_source", "formation_papered_source")


def _die(msg):
    print("FAIL:", msg)
    sys.exit(1)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _win_long(path):
    if os.name == "nt":
        ap = os.path.abspath(path)
        if len(ap) > 250 and not ap.startswith("\\\\"):
            return "\\\\?\\" + ap
    return path


def _read(path):
    try:
        with open(_win_long(path), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _resolve_projection(entity_page, vault_root, source):
    """Resolve a `papered_source` value to an absolute path CONFINED to vault_root. A relative value
    (the exemplar `../../raw/inbox/…`) resolves against the ENTITY PAGE's directory first, else against
    vault_root; an existing candidate wins. Any candidate that escapes vault_root (an absolute path, or
    `..` beyond the root) is dropped — a crafted frontmatter path can never surface an out-of-vault file
    (the LLM-read corpus stays inside the vault trust boundary). Returns None for an empty/escaping
    source (→ no-paper-found)."""
    if not source or not str(source).strip():
        return None
    s = str(source).strip().replace("/", os.sep)
    root = os.path.abspath(vault_root)
    base = os.path.dirname(os.path.abspath(entity_page))
    raw_cands = ([os.path.normpath(s)] if os.path.isabs(s)
                 else [os.path.normpath(os.path.join(base, s)),
                       os.path.normpath(os.path.join(root, s))])
    confined = []
    for c in raw_cands:
        try:
            if os.path.commonpath([root, c]) == root:
                confined.append(c)
        except ValueError:
            continue   # different drive (Windows) — not under the vault root
    for c in confined:                       # prefer an existing file
        if os.path.isfile(_win_long(c)):
            return c
    return confined[0] if confined else None  # a confined-but-missing path → readable False downstream


def _facts(entity_page, vault_root, cache=None):
    """Mechanical facts for one hold's paper: the entity's first present papered_source key, its
    resolved projection path, readability, and (on a hit) the projection text. `cache` maps a resolved
    projection path → text so holds sharing a paper read it once (A76)."""
    fm = _frontmatter(_read(entity_page) or "")
    source, source_key = None, None
    for k in _SOURCE_KEYS:
        v = fm.get(k)
        if isinstance(v, str) and v.strip():
            source, source_key = v.strip(), k
            break
    proj = _resolve_projection(entity_page, vault_root, source)
    out = {"entity_page": entity_page, "papered_source": source, "source_key": source_key,
           "projection_path": proj, "readable": False, "cache_hit": False}
    if not proj:
        return out
    if cache is not None and proj in cache:
        text, out["cache_hit"] = cache[proj], True
    else:
        text = _read(proj)
        if cache is not None and text is not None:
            cache[proj] = text
    if text is not None:
        out["readable"] = True
        out["projection_text"] = text[:PROJECTION_CHARS]
    return out


def resolve(entity_page, vault_root):
    print(json.dumps(_facts(entity_page, vault_root), ensure_ascii=False, indent=2))


def resolve_batch(entity_pages, vault_root):
    cache = {}
    print(json.dumps([_facts(p, vault_root, cache=cache) for p in entity_pages],
                     ensure_ascii=False, indent=2))


def attach(queue_path, cid, verdict, doc=None, section=None, quote=None):
    if verdict not in VERDICTS:
        _die(f"verdict must be one of {VERDICTS}, got {verdict!r}")
    data = queue_tx.load(queue_path)
    item = next((it for it in data["queue"] if it.get("id") == cid), None)
    if item is None:
        _die(f"id {cid!r} not found in {queue_path}")
    # ADVISORY INVARIANT: only the paper_evidence field is written. lane / stage / recommended are
    # read here purely to assert they are unchanged — the packet can never move an item toward ship.
    before = {k: item.get(k) for k in ("lane", "stage", "recommended")}
    item["paper_evidence"] = {"doc": doc, "section": section, "quote": quote,
                              "verdict": verdict, "checked_utc": _now()}
    after = {k: item.get(k) for k in ("lane", "stage", "recommended")}
    assert before == after, "paper_evidence attach must never change lane/stage/recommended"
    queue_tx._apply_items(queue_path, [item], "update")
    print(json.dumps({"ok": True, "id": cid, "verdict": verdict,
                      "paper_evidence": item["paper_evidence"]}, ensure_ascii=False))


from _util import utf8_stdio as _utf8_stdio


def main(argv=None):
    _utf8_stdio()
    ap = argparse.ArgumentParser(prog="paper_evidence.py",
                                 description="A75 Paper-Governs evidence verifier (advisory-only).")
    sub = ap.add_subparsers(dest="op", required=True)
    pr = sub.add_parser("resolve")
    pr.add_argument("--entity-page", required=True)
    pr.add_argument("--vault-root", required=True)
    pb = sub.add_parser("resolve-batch")
    pb.add_argument("--entity-pages", required=True, help='JSON list of entity page paths')
    pb.add_argument("--vault-root", required=True)
    pa = sub.add_parser("attach")
    pa.add_argument("--queue", required=True)
    pa.add_argument("--id", required=True)
    pa.add_argument("--verdict", required=True, choices=VERDICTS)
    pa.add_argument("--doc", default=None)
    pa.add_argument("--section", default=None)
    pa.add_argument("--quote", default=None)
    args = ap.parse_args(argv)

    if args.op == "resolve":
        resolve(args.entity_page, args.vault_root)
    elif args.op == "resolve-batch":
        try:
            pages = json.loads(args.entity_pages)
            assert isinstance(pages, list)
        except (ValueError, AssertionError):
            _die("--entity-pages must be a JSON list")
        resolve_batch(pages, args.vault_root)
    else:
        attach(args.queue, args.id, args.verdict, doc=args.doc, section=args.section, quote=args.quote)
    return 0


if __name__ == "__main__":
    sys.exit(main())
