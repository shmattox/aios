#!/usr/bin/env python3
"""reflect.py — deterministic scaffolding for the daily `reflect` stage.

Discovery / dedup-context / verify for turning a day's session records into gated
KB-growth proposals. Judgment lives in skills/reflect/SKILL.md; this tool is fact-free,
stdlib-only, and NEVER writes canonical state (the skill enqueues via queue_tx after VERIFY).
Mirrors session_synth.py; frontmatter parsing is imported from it (DRY)."""
import os, sys, glob, tempfile, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from session_synth import _read, _frontmatter, _get  # reuse, don't re-implement

_LESSONS_HEAD = re.compile(r"^\s{0,3}(\*\*Lessons\*\*|#{2,6}\s+Lessons)\s*$", re.I)
_BULLET = re.compile(r"^\s{0,3}-\s+(.*\S)\s*$")
_ANY_HEAD = re.compile(r"^\s{0,3}(#{1,6}\s+|\*\*[A-Za-z].*\*\*\s*$)")
_STOP = {"the","a","an","of","to","and","for","in","on","with","how","build"}

def discover(vault, live_kb_map, day):
    """Find the target day's session records + journal notes across all live KBs.
    `day` = 'YYYY-MM-DD'. Returns {"records":[...], "journals":[...]}."""
    records, journals = [], []
    for kb, folder in live_kb_map.items():
        base = os.path.join(vault, folder)
        sess_dir = os.path.join(base, "raw", "sessions")
        for p in sorted(glob.glob(os.path.join(sess_dir, "*.md"))):
            try:
                fm = _frontmatter(_read(p))
            except Exception:
                continue
            if _get(fm, "type") != "session-record":
                continue
            if (_get(fm, "started_utc") or "")[:10] != day:
                continue
            records.append({
                "file": os.path.abspath(p),
                "kb": kb,
                "id": _get(fm, "id"),
                "conflict_key": _get(fm, "conflict_key"),
                "date": day,
                "project": _get(fm, "project"),
            })
        jp = os.path.join(base, "wiki", "journal", "%s.md" % day)
        if os.path.exists(jp):
            journals.append({"file": os.path.abspath(jp), "kb": kb, "date": day})
    return {"records": records, "journals": journals}

def lessons_anchor(claude_md_path):
    """Locate the Lessons block in a CLAUDE.md. Returns exists / insert_after_line (1-based) /
    existing_rules. A missing block => the skill holds-and-flags rather than mis-inserting."""
    try:
        lines = _read(claude_md_path).splitlines()
    except Exception:
        return {"exists": False, "insert_after_line": None, "existing_rules": []}
    head = next((i for i, ln in enumerate(lines) if _LESSONS_HEAD.match(ln)), None)
    if head is None:
        return {"exists": False, "insert_after_line": None, "existing_rules": []}
    rules, last_bullet = [], head
    for i in range(head + 1, len(lines)):
        ln = lines[i]
        m = _BULLET.match(ln)
        if m:
            rules.append(m.group(1))
            last_bullet = i
            continue
        if ln.strip() == "":
            continue
        if _ANY_HEAD.match(ln):   # next section — Lessons block ended
            break
    return {"exists": True, "insert_after_line": last_bullet + 1, "existing_rules": rules}

def _tokens(s):
    """Extract meaningful tokens (skip stopwords, short/empty tokens)."""
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t and t not in _STOP and len(t) > 2}

def dedup_context(vault, live_kb_map, kb, slug_or_terms):
    """Surface the KB's knowledge/ neighbourhood so the skill prefers UPDATE over a duplicate page."""
    folder = live_kb_map.get(kb)
    existing, candidates = [], []
    if not folder:
        return {"existing_slugs": [], "candidates": []}
    kdir = os.path.join(vault, folder, "wiki", "knowledge")
    want = _tokens(slug_or_terms)
    for p in sorted(glob.glob(os.path.join(kdir, "*.md"))):
        slug = os.path.splitext(os.path.basename(p))[0]
        existing.append(slug)
        try:
            fm = _frontmatter(_read(p))
        except Exception:
            fm = {}
        title = _get(fm, "title") or slug.replace("-", " ")
        if want & (_tokens(slug) | _tokens(title)):
            candidates.append({"slug": slug, "file": os.path.abspath(p), "title": title})
    return {"existing_slugs": existing, "candidates": candidates}

def write_atomic(path, text):
    """Write text to path atomically via tempfile + os.replace."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

def verify(draft_paths, vault, live_kb_map):
    """Verify each draft exists, is non-empty, has type: in frontmatter, and sits under a live KB.
    Returns {"ok": bool, "problems": [str]}."""
    problems = []
    folders = set(live_kb_map.values())
    for p in draft_paths:
        if not os.path.exists(p):
            problems.append("missing: %s" % p)
            continue
        text = _read(p)
        if not text.strip():
            problems.append("empty: %s" % p)
            continue
        fm = _frontmatter(text)
        if not _get(fm, "type"):
            problems.append("no type: %s" % p)
        ap = os.path.abspath(p).replace("\\", "/")
        if not any(("/%s/" % f) in ap for f in folders):
            problems.append("draft outside a live KB folder: %s" % p)
    return {"ok": not problems, "problems": problems}

def _utf8_stdio():
    """Reconfigure stdout/stderr to UTF-8 if possible."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

def main(argv=None):
    """CLI: discover, verify, lessons-anchor, and dedup-context subcommands."""
    import argparse, json
    _utf8_stdio()
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover")
    d.add_argument("--vault", required=True)
    d.add_argument("--kb-map", required=True)
    d.add_argument("--day", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--vault", required=True)
    v.add_argument("--kb-map", required=True)
    v.add_argument("paths", nargs="+")
    la = sub.add_parser("lessons-anchor")
    la.add_argument("claude_md")
    dc = sub.add_parser("dedup-context")
    dc.add_argument("--vault", required=True)
    dc.add_argument("--kb-map", required=True)
    dc.add_argument("--kb", required=True)
    dc.add_argument("terms")
    a = ap.parse_args(argv)
    if a.cmd == "discover":
        kb_map = json.loads(a.kb_map)
        print(json.dumps(discover(a.vault, kb_map, a.day), indent=2))
        return 0
    if a.cmd == "verify":
        kb_map = json.loads(a.kb_map)
        r = verify(a.paths, a.vault, kb_map)
        print(json.dumps(r, indent=2))
        return 0 if r["ok"] else 1
    if a.cmd == "lessons-anchor":
        print(json.dumps(lessons_anchor(a.claude_md), indent=2))
        return 0
    if a.cmd == "dedup-context":
        kb_map = json.loads(a.kb_map)
        print(json.dumps(dedup_context(a.vault, kb_map, a.kb, a.terms), indent=2))
        return 0
    return 2

if __name__ == "__main__":
    sys.exit(main())
