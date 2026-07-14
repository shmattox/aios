#!/usr/bin/env python3
r"""garden_hygiene.py - the MECHANICAL hygiene finding-set (Stage 5, read-only).

The Benos os-optimizer rulebook harvest (A3; design: docs/superpowers/specs/
2026-07-01-a3-garden-rulebook-harvest-design.md) tiers garden findings: MECHANICAL fixes have a
single correct output and are reversible (the B4 auto-ship tier on cleared KBs — familyoffice is
always held by lane_policy's kb backstop); SEMANTIC fixes are judgment calls the model makes
guided by skills/garden/rulebook/, always review-laned. This tool is the mechanical tier's
oracle: a deterministic filesystem walk that REPORTS; the garden skill turns findings into
staging drafts + queue proposals. Never writes anything. Stdlib-only, fact-free.

Findings (each semantic deliberate):
  dup_h1        = first content line is `# Title` whose slug equals the filename stem (G7.3 —
                  Obsidian/Claude show the filename; the H1 is redundant). strip_dup_h1() is the
                  deterministic fix body. Structural files exempt.
  frontmatter   = page lacks a leading `---` block, or the block lacks a `type:` key — the
                  kb-schema frontmatter contract's floor (G7.2). journal/ is exempt (episodic
                  dailies by the canonical journal contract); structural files exempt.
  index_missing = content page not wikilink-reachable from wiki/index.md (F9.2 — "index.md stays
                  current", kb-schema maintenance rule; log.md is history, not the index).
                  journal/ + sources/ exempt (episodic / transient distill inbox). When index.md
                  is absent: has_index=False and the per-page list stays empty — the finding is
                  "create the index", one proposal, not N.
  repoints      = a DEAD wikilink (garden_audit semantics) whose target stem sits within
                  Levenshtein distance 2 of exactly ONE page stem, strictly closer than every
                  other page - the typo class, the only dead link with a single correct output.
                  Ambiguous or distant dead links are NOT reported: they stay with the Connect
                  pass's model judgment (repoint choice needs reading the sentence).

Usage:
  python garden_hygiene.py --vault-root <path> --kb-map '{"personal":"01_Personal",...}' [--json]

--kb-map is the profile's `vault.live_kb_map`; ONLY mapped folders are scanned (same fence as
garden_audit/garden_sweep). Exit 0 always - it is a report, not a gate.
"""
import json, os, re, sys

import garden_audit
from garden_audit import EXCLUDE_DIRS, FENCE, INLINE_CODE, LINK, STRUCTURAL, _read

H1 = re.compile(r"^#\s+(.+?)\s*$")
TYPE_KEY = re.compile(r"^type\s*:", re.M)
FM_EXEMPT_DIRS = {"journal"}                 # episodic dailies - frontmatter floor not enforced
INDEX_EXEMPT_DIRS = {"journal", "sources"}   # episodic / transient distill inbox - never indexed
REPOINT_MAX_DISTANCE = 2                     # the typo class; farther is a judgment call


def _slug(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.casefold())).strip("-")


def _lev(a, b):
    """Plain Levenshtein - stems are short, the DP is fine."""
    if abs(len(a) - len(b)) > REPOINT_MAX_DISTANCE:
        return REPOINT_MAX_DISTANCE + 1      # cheap band-out; caller only cares up to the cap
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _split_frontmatter(text):
    """-> (frontmatter block incl. fences or '', body). Only a leading `---` line opens one."""
    if text.startswith("---\n") or text.startswith("---\r\n"):
        lines = text.splitlines(keepends=True)
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "".join(lines[:i + 1]), "".join(lines[i + 1:])
    return "", text


def strip_dup_h1(text, stem):
    """The deterministic G7.3 fix: if the first content line is an H1 whose slug equals the
    filename stem, drop it (plus one following blank line). -> (text, changed)."""
    fm, body = _split_frontmatter(text)
    lines = body.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        m = H1.match(line.strip())
        if m and _slug(m.group(1)) == _slug(stem):
            del lines[i]
            if i < len(lines) and not lines[i].strip():
                del lines[i]
            return fm + "".join(lines), True
        break
    return text, False


def index_line(rel):
    """The deterministic F9.2 fix line for an unindexed page."""
    return f"- [[{rel[:-3] if rel.endswith('.md') else rel}]]"


def _pages(vault_root, folder):
    """wiki-relative -> abs path, same walk + exclusions as garden_audit."""
    wiki = os.path.join(vault_root, folder, "wiki")
    pages = {}
    if not os.path.isdir(wiki):
        return pages
    for dp, dns, fns in os.walk(wiki):
        dns[:] = [d for d in dns if d not in EXCLUDE_DIRS]
        for fn in fns:
            if fn.endswith(".md"):
                rel = os.path.relpath(os.path.join(dp, fn), wiki).replace(os.sep, "/")
                pages[rel] = os.path.join(dp, fn)
    return pages


def _top_dir(rel):
    return rel.split("/")[0].casefold() if "/" in rel else ""


def hygiene_kb(vault_root, folder):
    """One KB -> {"pages", "dup_h1", "frontmatter", "has_index", "index_missing", "repoints"}.
    All paths wiki-relative, forward slashes, sorted."""
    pages = _pages(vault_root, folder)
    content = {rel: p for rel, p in pages.items()
               if os.path.basename(rel).lower() not in STRUCTURAL}

    dup_h1, frontmatter = [], []
    for rel, path in sorted(content.items()):
        text = _read(path)
        fm, body = _split_frontmatter(text)
        for line in body.splitlines():
            if not line.strip():
                continue
            m = H1.match(line.strip())
            stem = os.path.splitext(os.path.basename(rel))[0]
            if m and _slug(m.group(1)) == _slug(stem):
                dup_h1.append({"page": rel, "h1": m.group(1)})
            break
        if _top_dir(rel) not in FM_EXEMPT_DIRS:
            if not fm:
                frontmatter.append({"page": rel, "missing": ["frontmatter"]})
            elif not TYPE_KEY.search(fm):
                frontmatter.append({"page": rel, "missing": ["type"]})

    has_index = "index.md" in pages
    index_missing = []
    if has_index:
        indexed = set()
        text = INLINE_CODE.sub("", FENCE.sub("", _read(pages["index.md"])))
        stems = {}
        for rel in pages:
            stems.setdefault(os.path.splitext(os.path.basename(rel))[0].casefold(), []).append(rel)
        for m in LINK.finditer(text):
            target = m.group(1).strip().strip("/")
            if not target:
                continue
            cand = target if target.endswith(".md") else target + ".md"
            if cand in pages:
                indexed.add(cand)
            else:                              # bare-stem link: credit every stem hit (audit rule)
                indexed.update(stems.get(
                    os.path.splitext(os.path.basename(target))[0].casefold(), []))
        index_missing = sorted(rel for rel in content
                               if rel not in indexed and _top_dir(rel) not in INDEX_EXEMPT_DIRS)

    # mechanical repoints: unique near-stem match for the audit's dead links. Candidates are
    # CONTENT pages only — a typo within distance 2 of index/log/readme (e.g. [[dog]] -> log.md)
    # must not mint a repoint to a structural page.
    repoints = []
    stem_to_rels = {}
    for rel in content:
        stem_to_rels.setdefault(
            os.path.splitext(os.path.basename(rel))[0].casefold(), []).append(rel)
    for src, target in garden_audit.audit_kb(vault_root, folder)["dead_links"]:
        tstem = os.path.splitext(os.path.basename(target))[0].casefold()
        scored = sorted((_lev(tstem, s), s) for s in stem_to_rels)
        if not scored or scored[0][0] > REPOINT_MAX_DISTANCE:
            continue                           # too far - semantic, Connect's call
        if len(scored) > 1 and scored[1][0] == scored[0][0]:
            continue                           # tied stems - ambiguous, semantic
        rels = stem_to_rels[scored[0][1]]
        if len(rels) != 1:
            continue                           # one stem, several pages - ambiguous, semantic
        repoints.append({"src": src, "target": target, "repoint_to": rels[0]})

    return {"pages": len(pages), "dup_h1": dup_h1, "frontmatter": frontmatter,
            "has_index": has_index, "index_missing": index_missing, "repoints": repoints}


def hygiene(vault_root, kb_map):
    """Every mapped KB -> {kb: hygiene_kb(...)}. Unmapped folders are never scanned."""
    return {kb: hygiene_kb(vault_root, folder)
            for kb, folder in sorted((kb_map or {}).items())}


def _utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


if __name__ == "__main__":
    _utf8_stdio()
    a = sys.argv[1:]
    if "--vault-root" not in a or "--kb-map" not in a:
        print(__doc__); sys.exit(1)
    res = hygiene(a[a.index("--vault-root") + 1], json.loads(a[a.index("--kb-map") + 1]))
    if "--json" in a:
        print(json.dumps(res, indent=1, ensure_ascii=False))
        sys.exit(0)
    print("GARDEN HYGIENE report (mechanical tier, read-only):")
    tot = 0
    for kb, r in res.items():
        n = len(r["dup_h1"]) + len(r["frontmatter"]) + len(r["index_missing"]) + len(r["repoints"])
        n += 0 if r["has_index"] else 1
        tot += n
        print(f"  {kb}: pages={r['pages']} findings={n}"
              f" (dup_h1={len(r['dup_h1'])} frontmatter={len(r['frontmatter'])}"
              f" index_missing={len(r['index_missing'])} repoints={len(r['repoints'])}"
              f"{'' if r['has_index'] else ' NO-INDEX'})")
        for f in r["dup_h1"]:
            print(f"      dup-h1:     {f['page']} (\"{f['h1']}\")")
        for f in r["frontmatter"]:
            print(f"      frontmatter: {f['page']} missing {','.join(f['missing'])}")
        for p in r["index_missing"]:
            print(f"      unindexed:  {p}")
        for f in r["repoints"]:
            print(f"      repoint:    {f['src']} [[{f['target']}]] -> {f['repoint_to']}")
    print(f"  TOTAL findings: {tot}")
