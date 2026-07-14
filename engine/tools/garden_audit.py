#!/usr/bin/env python3
r"""garden_audit.py - full-inventory wiki audit: orphans + dead links (Stage 5, read-only).

The connect pass's frontier is the link graph ("recent + linked"), so a page with zero inlinks
is structurally unreachable from it - orphans accumulate monotonically (361 on the reference
vault before the 2026-07-05 catch-up). This tool is the catch-up mechanism made recurring: a
deterministic FILESYSTEM walk over each KB's full wiki inventory. It REPORTS; the garden model
leg decides (link / flag / accept). Never writes anything. No deps beyond the stdlib.

Semantics (each deliberate):
  orphan     = a wiki content page with zero inbound wikilinks from counted sources.
               index.md links COUNT ("the index never goes stale" - indexed = minimally
               connected; a page missing from even the index is the real defect class).
               log.md is NOT a source (append-only history; its links rot by design).
               index.md / log.md / README.md are structural, never themselves orphans.
  dead link  = a wikilink whose target resolves to no file. Resolution order: exact
               wiki-relative path -> unique-or-first stem match -> file under the KB ROOT
               (catches [[outputs/queries/x]] and raw/ citations). Bare folder-name links
               ([[people]]) are structural references, skipped. Links inside fenced code
               blocks or inline backticks are examples/templates, skipped.
  excluded   = staging/ and .templates/ (transient, not knowledge) - neither pages nor sources.
  exempt     = journal/ pages are never ORPHANS (episodic decaying narrative by the canonical
               journal contract - old dailies losing inlinks is the design, not a defect). They
               still count as link sources and their dead links still report. Override with
               --orphan-exempt.

Usage:
  python garden_audit.py --vault-root <path> --kb-map '{"personal":"01_Personal",...}' [--json]
                         [--orphan-exempt journal]   (comma-separated wiki subdirs; default "journal")

--kb-map is the profile's `vault.live_kb_map`; ONLY mapped folders are audited (an unmapped
folder is skipped, same posture as garden_sweep). Exit 0 always - it is a report, not a gate.
"""
import json, os, re, sys

LINK = re.compile(r"\[\[([^\]\|#]+)")
FENCE = re.compile(r"```.*?```", re.S)
INLINE_CODE = re.compile(r"`[^`\n]*`")
STRUCTURAL = {"index.md", "log.md", "readme.md"}   # never orphans; log.md also not a source
EXCLUDE_DIRS = {"staging", ".templates"}
ORPHAN_EXEMPT_DIRS = {"journal"}                   # episodic-by-design; see docstring


def _win_long(path):
    """Windows long-path shim (same as garden_sweep): deep vault slugs pass ~260 chars."""
    if os.name == "nt":
        ap = os.path.abspath(path)
        if len(ap) > 250 and not ap.startswith("\\\\"):
            return "\\\\?\\" + ap
    return path


def _read(p):
    try:
        with open(_win_long(p), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def walk_pages(vault_root, folder):
    """{wiki-relative page path (fwd slashes) -> abs path} for all .md content pages.
    Honours EXCLUDE_DIRS (staging/.templates). Empty dict if the wiki dir is absent."""
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


def audit_kb(vault_root, folder, orphan_exempt=None):
    """Audit one KB folder -> {"pages": int, "orphans": [rel], "dead_links": [(src_rel, target)],
    "inbound": {rel: int}, "adjacency": {rel: [rel,...]}}.
    All paths wiki-relative with forward slashes. orphan_exempt: wiki subdirs whose pages are
    never reported as orphans (default ORPHAN_EXEMPT_DIRS). inbound/adjacency are additive keys
    (A65): inbound = per-page inbound wikilink count (same counting rules as orphans);
    adjacency = per-page sorted in-KB outbound targets (no self-links; credits stem-collision hits)."""
    exempt = ORPHAN_EXEMPT_DIRS if orphan_exempt is None else set(orphan_exempt)
    pages = walk_pages(vault_root, folder)
    if not pages:
        return {"pages": 0, "orphans": [], "dead_links": [], "inbound": {}, "adjacency": {}}

    stems = {}                                     # casefolded stem -> [rel, ...]
    for rel in pages:
        stems.setdefault(os.path.splitext(os.path.basename(rel))[0].casefold(), []).append(rel)
    subdirs = {d.casefold() for rel in pages for d in [rel.split("/")[0]] if "/" in rel}

    inbound = dict.fromkeys(pages, 0)
    adjacency = {rel: set() for rel in pages}
    dead = []
    for rel, path in sorted(pages.items()):
        if os.path.basename(rel).lower() == "log.md":
            continue                               # history is not connection; its rot is by design
        text = INLINE_CODE.sub("", FENCE.sub("", _read(path)))
        for m in LINK.finditer(text):
            target = m.group(1).strip().strip("/")
            if not target:
                continue
            if "/" not in target and target.casefold() in subdirs:
                continue                           # bare folder reference, not a page link
            cand = target if target.endswith(".md") else target + ".md"
            if cand in pages:
                if cand != rel:                    # a self-link is not connection
                    inbound[cand] += 1
                    adjacency[rel].add(cand)
                continue
            hits = stems.get(os.path.splitext(os.path.basename(target))[0].casefold(), [])
            if hits:
                # Bare-stem link with colliding stems: credit EVERY hit - the page the author
                # meant is then always credited (an arbitrary first-hit pick could mask a real
                # orphan behind a link aimed at its namesake). Errs toward fewer orphans, never
                # toward a false orphan proposal.
                for h in hits:
                    if h != rel:
                        inbound[h] += 1
                        adjacency[rel].add(h)
                continue
            kb_base = os.path.realpath(os.path.join(vault_root, folder))
            kb_root_file = os.path.realpath(os.path.join(kb_base, cand.replace("/", os.sep)))
            # containment check: a traversal-shaped target ([[../../x]]) must not probe outside
            # the KB - treat it as dead rather than resolve it
            if kb_root_file.startswith(kb_base + os.sep) and os.path.isfile(_win_long(kb_root_file)):
                continue                           # outputs/ or raw/ citation - alive, just not wiki
            dead.append((rel, target))

    orphans = sorted(r for r, n in inbound.items()
                     if n == 0 and os.path.basename(r).lower() not in STRUCTURAL
                     and ("/" not in r or r.split("/")[0].casefold() not in exempt))
    return {"pages": len(pages), "orphans": orphans, "dead_links": dead,
            "inbound": inbound, "adjacency": {k: sorted(v) for k, v in adjacency.items()}}


def audit(vault_root, kb_map, orphan_exempt=None):
    """Audit every mapped KB -> {kb: audit_kb(...)}. Unmapped folders are never scanned."""
    return {kb: audit_kb(vault_root, folder, orphan_exempt)
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
    vault_root = a[a.index("--vault-root") + 1]
    kb_map = json.loads(a[a.index("--kb-map") + 1])
    exempt = ({d.strip().casefold() for d in a[a.index("--orphan-exempt") + 1].split(",") if d.strip()}
              if "--orphan-exempt" in a else None)
    res = audit(vault_root, kb_map, exempt)
    if "--json" in a:
        print(json.dumps(res, indent=1, ensure_ascii=False))
        sys.exit(0)
    tot_pages = sum(r["pages"] for r in res.values())
    tot_orph = sum(len(r["orphans"]) for r in res.values())
    tot_dead = sum(len(r["dead_links"]) for r in res.values())
    print("GARDEN AUDIT report (read-only):")
    for kb, r in res.items():
        print(f"  {kb}: pages={r['pages']} orphans={len(r['orphans'])} dead_links={len(r['dead_links'])}")
        for o in r["orphans"]:
            print(f"      orphan: {o}")
        for src, t in r["dead_links"]:
            print(f"      dead:   {src} -> [[{t}]]")
    print(f"  TOTAL: pages={tot_pages} orphans={tot_orph} dead_links={tot_dead}")
