#!/usr/bin/env python3
"""brief_threads.py — deterministic thread<->item reconciliation for the brief.

The daily brief re-surfaced worked items COLD because `state/threads/` (the durable record of
prior-session work) was never read at gather: the cache's `thread_id` field was populated by ad-hoc
model judgment and consumed by nothing. This tool is the deterministic populator + the single source
of the `in_motion` object the renderer reads. Pure stdlib. No LLM, no network.

Join rule per cache item, first match wins (ownership is precise — an `OI-N` thread owns ONLY its
own id, so a mere cross-reference never captures another item):
  1. the item's own `thread_id` (gather judgment) names an existing thread, OR
  2a. a thread whose `id` IS the item's OI-id (exact ownership), OR
  2b. a slug-id thread (no OI-N of its own) that references the item's OI-id, OR
  3. the item's `conflict_key` equals a thread's `conflict_key`.
On match, the item gains `in_motion = {thread_id, status, next_action, updated_utc, court}` where
`court` is "you" (open — still your move), "others" (parked — waiting on someone else), or "done"
(resolved/reverted — finished). The tool never writes the scalar `thread_id` (only the gather does).

See docs/superpowers/specs/2026-07-11-brief-thread-reconciliation-design.md
"""
import json
import os
import re
import sys

_OID = re.compile(r"OI-\d+", re.IGNORECASE)


def parse_frontmatter(text):
    """Minimal YAML-frontmatter reader (str key -> str value). Stdlib only: the frontmatter here is
    flat scalars, so a line parser is enough (no PyYAML dependency). Strips matching quotes."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out = {}
    for line in text[3:end].splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key] = val
    return out


def thread_oids(thread):
    """Set of OI-ids referenced anywhere in the thread (id, conflict_key, item, next_action),
    uppercased. Deterministic; case-insensitive match, canonical uppercase output."""
    blob = " ".join(str(thread.get(k, "") or "") for k in ("id", "conflict_key", "item", "next_action"))
    return {m.upper() for m in _OID.findall(blob)}


def court(status):
    """Whose court the ball is in. `open` -> 'you' (still your move); `resolved`/`reverted`/`closed`/
    `done` -> 'done' (finished, leaves the actionable surfaces); anything else (parked/blank) ->
    'others' (waiting on someone else). The three buckets keep a done task out of the "waiting on
    others" track (review finding #3)."""
    s = (status or "").strip().lower()
    if s == "open":
        return "you"
    if s in ("resolved", "reverted", "closed", "done"):
        return "done"
    return "others"


def _in_motion(thread):
    return {
        "thread_id": thread.get("id"),
        "status": thread.get("status", ""),
        "next_action": thread.get("next_action", ""),
        "updated_utc": thread.get("updated_utc"),
        "court": court(thread.get("status")),
    }


def _is_oi_id(s):
    return bool(_OID.fullmatch(str(s or "").upper()))


def link_item(item, threads):
    """Return the in_motion dict for `item`, or None. `threads` is a list of parsed thread dicts,
    iterated in a stable (id-sorted) order so ties resolve deterministically.

    Ownership is precise (review finding #1): an `OI-N` thread owns ONLY its own id — a mere
    cross-reference to another OI never captures that item. A slug-id thread (no OI-N of its own)
    owns every OI it references. Precedence: gather-authored thread_id -> exact OI-id owner ->
    slug-thread referenced owner -> conflict_key."""
    ordered = sorted(threads, key=lambda t: str(t.get("id") or ""))
    # 1. honor a thread_id the GATHER set (its judgment) if the thread exists
    existing = item.get("thread_id")
    if existing:
        for t in ordered:
            if t.get("id") == existing:
                return _in_motion(t)
    m = _OID.search(str(item.get("id") or ""))
    item_oid = m.group(0).upper() if m else None
    if item_oid:
        # 2a. a thread whose id IS this OI-id (exact ownership — wins over any mere mention)
        for t in ordered:
            if str(t.get("id") or "").upper() == item_oid:
                return _in_motion(t)
        # 2b. a slug-id thread that references this OI-id (an OI-N thread never links by mention)
        for t in ordered:
            if not _is_oi_id(t.get("id")) and item_oid in thread_oids(t):
                return _in_motion(t)
    # 3. conflict_key equality
    ck = item.get("conflict_key")
    if ck:
        for t in ordered:
            if t.get("conflict_key") and t["conflict_key"] == ck:
                return _in_motion(t)
    return None


def _iter_items(cache):
    for it in cache.get("needs_you") or []:
        yield it
    stations = cache.get("stations") or {}
    for node in stations.values():
        items = node if isinstance(node, list) else (node or {}).get("items", [])
        for it in items:
            yield it


def annotate_cache(cache, threads):
    """Populate `in_motion` on every needs_you + station item that links to a thread. Mutates
    `cache` in place; returns the number of items linked.

    Deliberately does NOT write the scalar `thread_id` (review finding #2): only the gather authors
    that field, and `link_item` rule 1 honors it — persisting a derived value would make a misjoin
    sticky and survive a corrected join rule. `in_motion` is the only consumed field."""
    n = 0
    for it in _iter_items(cache):
        im = link_item(it, threads)
        if im:
            it["in_motion"] = im
            n += 1
    return n


def load_threads(threads_dir):
    """Read every *.md in threads_dir into a parsed thread dict, adding updated_utc from mtime."""
    out = []
    if not os.path.isdir(threads_dir):
        return out
    import datetime
    for name in sorted(os.listdir(threads_dir)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(threads_dir, name)
        try:
            with open(path, encoding="utf-8") as f:
                fm = parse_frontmatter(f.read())
        except OSError:
            continue
        if not fm:
            continue
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path), datetime.timezone.utc)
        fm["updated_utc"] = mtime.strftime("%Y-%m-%dT%H:%M:%SZ")
        out.append(fm)
    return out


def _atomic_write(path, cache):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)
    with open(tmp, encoding="utf-8") as f:
        json.load(f)  # verify it parses before replacing
    os.replace(tmp, path)


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if len(argv) < 4 or argv[1] != "annotate":
        print("usage: brief_threads.py annotate <cache.json> <threads_dir>", file=sys.stderr)
        return 2
    cache_path, threads_dir = argv[2], argv[3]
    with open(cache_path, encoding="utf-8") as f:
        cache = json.load(f)
    threads = load_threads(threads_dir)
    n = annotate_cache(cache, threads)
    _atomic_write(cache_path, cache)
    print(json.dumps({"linked": n, "threads_read": len(threads)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
