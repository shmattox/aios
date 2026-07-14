# A65 — Semantic Connection Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local, offline embedding oracle (`garden_neighbors.py`) that surfaces within-KB nearest-by-meaning candidate links for orphan/weakly-linked wiki pages, feeding the garden connect pass as `lane: review` proposals.

**Architecture:** A new read-only deterministic tool sits beside `garden_audit.py`. It reuses `garden_audit`'s inventory walk + inbound/adjacency data, builds a content-hash-cached local embedding index (`fastembed`), and computes cosine nearest-neighbours. The garden LLM (SKILL.md step 1) judges the candidates and proposes links. The tool is stdlib-only at import time and fails soft (SKIP, exit 0) when the embedding dep/model is absent, so the lexical connect pass is never blocked.

**Tech Stack:** Python 3 (stdlib: `json`, `os`, `re`, `sys`, `hashlib`, `math`), `fastembed` (lazy-imported ONNX embedder), the repo's custom `check()`/`w()`/`main()` test harness (not pytest).

## Global Constraints

- **Local/offline embeddings only** — no hosted API, no network at inference. FamilyOffice is audit-grade; no vault content leaves the machine.
- **`garden_neighbors.py` must import with stdlib only** — `fastembed`/`numpy` are lazy-imported inside `load_embedder()` only. The module must import and run its pure functions with `fastembed` absent.
- **Embeddings are an input to judgment, never an auto-link.** The tool only *reports* candidates; all resulting queue items are `lane: review`.
- **Within-KB only (v1).** Neighbours are computed per-KB; cross-KB suggestions are out of scope.
- **Backward compatibility:** `garden_audit.audit_kb`'s existing return keys (`pages`, `orphans`, `dead_links`) must be unchanged; only additive keys allowed.
- **Defaults (verbatim):** embedding model `BAAI/bge-small-en-v1.5`; `k = 5`; similarity `floor = 0.55`; `weak_threshold = 2` (inbound `< 2` ⇒ target); embed-text cap `2000` chars.
- **Test idiom:** each test file is a standalone script — `HERE`/`TOOLS`/`sys.path.insert(0, TOOLS)`, `check(name, cond)` counter, `w(root, rel, text)` file writer, hermetic `tempfile.mkdtemp()`, `main()` ending in `sys.exit(1 if FAIL else 0)`. Run with `python engine/tools/tests/<file>.py`.
- **Cache location:** `<env_root>/state/garden/embeddings/<kb>.json`, gitignored (derived, regenerable).

---

### Task 1: `garden_audit` — extract `walk_pages`, expose `inbound` + `adjacency`

Reuse `garden_audit`'s file walk and link resolution so "what is a page" / "what links to what" has one home. Extract the inline `os.walk` into a public `walk_pages()`, and add two additive keys to `audit_kb`'s return: `inbound` (per-page inbound count) and `adjacency` (per-page resolved in-KB outbound link targets).

**Files:**
- Modify: `engine/tools/garden_audit.py` (the `audit_kb` function + a new `walk_pages` function)
- Test: `engine/tools/tests/test_garden_audit_reuse.py` (new — keeps the big existing `test_garden_audit.py` untouched)

**Interfaces:**
- Produces: `walk_pages(vault_root, folder) -> dict[str, str]` mapping wiki-relative page path (forward slashes) → absolute path, honouring `EXCLUDE_DIRS`.
- Produces: `audit_kb(...)` return dict additionally carries `"inbound": dict[str, int]` (rel → inbound wikilink count, same counting rules as orphans) and `"adjacency": dict[str, list[str]]` (rel → sorted list of in-KB page rels it links to; excludes self-links; credits all stem-collision hits, matching the inbound rule).

- [ ] **Step 1: Write the failing test**

Create `engine/tools/tests/test_garden_audit_reuse.py`:

```python
#!/usr/bin/env python3
r"""A65 Task 1 — garden_audit exposes walk_pages() + inbound + adjacency for reuse.
Hermetic. Run: python engine/tools/tests/test_garden_audit_reuse.py"""
import os, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)
import garden_audit

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok   {name}")
    else: FAIL += 1; print(f"  FAIL {name}")

def w(root, rel, text=""):
    p = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)

def main():
    vault = tempfile.mkdtemp(prefix="reuse-vault-")
    try:
        B = "03_Dev/wiki/"
        w(vault, B + "projects/hub.md", "# hub\nsee [[knowledge/leaf]]\n")
        w(vault, B + "knowledge/leaf.md", "# leaf\nback to [[projects/hub]]\n")
        w(vault, B + "entities/lonely.md", "# lonely\n")
        w(vault, B + "index.md", "- [[projects/hub]]\n")

        # walk_pages: returns rel->abspath for all content pages, excludes staging/.templates
        w(vault, B + "staging/draft.md", "x")
        pages = garden_audit.walk_pages(vault, "03_Dev")
        check("walk_pages finds content pages", "knowledge/leaf.md" in pages and "projects/hub.md" in pages)
        check("walk_pages returns abspaths", os.path.isabs(pages["projects/hub.md"]))
        check("walk_pages excludes staging/", "staging/draft.md" not in pages)

        a = garden_audit.audit_kb(vault, "03_Dev")
        # backward-compatible keys unchanged
        check("orphans unchanged (lonely is orphan)", a["orphans"] == ["entities/lonely.md"])
        check("dead_links unchanged (none)", a["dead_links"] == [])
        # new inbound key
        check("inbound: hub gets index+leaf = 2", a["inbound"]["projects/hub.md"] == 2)
        check("inbound: leaf gets hub = 1", a["inbound"]["knowledge/leaf.md"] == 1)
        check("inbound: lonely = 0", a["inbound"]["entities/lonely.md"] == 0)
        # new adjacency key (outbound, in-KB, no self-links)
        check("adjacency: hub -> leaf", a["adjacency"]["projects/hub.md"] == ["knowledge/leaf.md"])
        check("adjacency: lonely -> []", a["adjacency"]["entities/lonely.md"] == [])
    finally:
        shutil.rmtree(vault, ignore_errors=True)
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python engine/tools/tests/test_garden_audit_reuse.py`
Expected: FAIL — `AttributeError: module 'garden_audit' has no attribute 'walk_pages'` (or `KeyError: 'inbound'`).

- [ ] **Step 3: Implement — extract `walk_pages` and add the two keys**

In `engine/tools/garden_audit.py`, add `walk_pages` above `audit_kb`:

```python
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
```

Then edit `audit_kb`. Replace the empty-wiki early return and the inline page walk so it uses `walk_pages`, and thread an `adjacency` accumulator. The full edited body:

```python
def audit_kb(vault_root, folder, orphan_exempt=None):
    """Audit one KB folder -> {"pages": int, "orphans": [rel], "dead_links": [(src_rel, target)],
    "inbound": {rel: int}, "adjacency": {rel: [rel,...]}}.
    All paths wiki-relative with forward slashes. orphan_exempt: wiki subdirs whose pages are
    never reported as orphans (default ORPHAN_EXEMPT_DIRS)."""
    exempt = ORPHAN_EXEMPT_DIRS if orphan_exempt is None else set(orphan_exempt)
    pages = walk_pages(vault_root, folder)
    if not pages:
        return {"pages": 0, "orphans": [], "dead_links": [], "inbound": {}, "adjacency": {}}

    stems = {}
    for rel in pages:
        stems.setdefault(os.path.splitext(os.path.basename(rel))[0].casefold(), []).append(rel)
    subdirs = {d.casefold() for rel in pages for d in [rel.split("/")[0]] if "/" in rel}

    inbound = dict.fromkeys(pages, 0)
    adjacency = {rel: set() for rel in pages}
    dead = []
    for rel, path in sorted(pages.items()):
        if os.path.basename(rel).lower() == "log.md":
            continue
        text = INLINE_CODE.sub("", FENCE.sub("", _read(path)))
        for m in LINK.finditer(text):
            target = m.group(1).strip().strip("/")
            if not target:
                continue
            if "/" not in target and target.casefold() in subdirs:
                continue
            cand = target if target.endswith(".md") else target + ".md"
            if cand in pages:
                if cand != rel:
                    inbound[cand] += 1
                    adjacency[rel].add(cand)
                continue
            hits = stems.get(os.path.splitext(os.path.basename(target))[0].casefold(), [])
            if hits:
                for h in hits:
                    if h != rel:
                        inbound[h] += 1
                        adjacency[rel].add(h)
                continue
            kb_base = os.path.realpath(os.path.join(vault_root, folder))
            kb_root_file = os.path.realpath(os.path.join(kb_base, cand.replace("/", os.sep)))
            if kb_root_file.startswith(kb_base + os.sep) and os.path.isfile(_win_long(kb_root_file)):
                continue
            dead.append((rel, target))

    orphans = sorted(r for r, n in inbound.items()
                     if n == 0 and os.path.basename(r).lower() not in STRUCTURAL
                     and ("/" not in r or r.split("/")[0].casefold() not in exempt))
    return {"pages": len(pages), "orphans": orphans, "dead_links": dead,
            "inbound": inbound, "adjacency": {k: sorted(v) for k, v in adjacency.items()}}
```

- [ ] **Step 4: Run both the new test and the existing audit test (regression)**

Run: `python engine/tools/tests/test_garden_audit_reuse.py`
Expected: PASS — `N passed, 0 failed`.

Run: `python engine/tools/tests/test_garden_audit.py`
Expected: PASS — the original suite still green (proves the additive change didn't alter `pages`/`orphans`/`dead_links`).

- [ ] **Step 5: Commit**

```bash
git add engine/tools/garden_audit.py engine/tools/tests/test_garden_audit_reuse.py
git commit -m "A65 t1: garden_audit exposes walk_pages + inbound + adjacency"
```

---

### Task 2: `garden_neighbors` — embed-text extraction + hashing (pure, stdlib-only)

Build the pure functions that turn a raw markdown page into embed text and a content hash. No embedder yet — this task establishes the module imports stdlib-only.

**Files:**
- Create: `engine/tools/garden_neighbors.py`
- Test: `engine/tools/tests/test_garden_neighbors.py` (new)

**Interfaces:**
- Produces: `build_embed_text(raw: str, max_chars: int = 2000) -> str` — strips leading frontmatter, fenced/inline code, and heading markers; replaces `[[a/b-c|alias#x]]` with its visible words (`b c`); collapses whitespace; truncates to `max_chars`.
- Produces: `page_hash(text: str) -> str` — sha256 hex of the utf-8 bytes.

- [ ] **Step 1: Write the failing test**

Create `engine/tools/tests/test_garden_neighbors.py`:

```python
#!/usr/bin/env python3
r"""A65 — garden_neighbors: embed-text, hashing, cache, cosine, nearest, run() (stub embedder).
Stdlib-only + a stub embedder; needs no model. Run: python engine/tools/tests/test_garden_neighbors.py"""
import os, sys, tempfile, shutil, json

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)
import garden_neighbors as gn

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok   {name}")
    else: FAIL += 1; print(f"  FAIL {name}")

def w(root, rel, text=""):
    p = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)

def test_embed_text():
    raw = "---\nkb: dev\ntype: concept\n---\n# Ambient Computing\nBody about [[knowledge/edge-nodes|edge]].\n```\ncode [[x]]\n```\n`inline`\n"
    t = gn.build_embed_text(raw)
    check("embed strips frontmatter", "kb: dev" not in t)
    check("embed strips fenced code", "code" not in t)
    check("embed strips inline code", "inline" not in t)
    check("embed keeps title words", "Ambient Computing" in t)
    check("embed keeps wikilink visible words", "edge nodes" in t)
    check("embed truncates", len(gn.build_embed_text("x " * 5000, max_chars=100)) <= 100)

def test_hash():
    check("hash stable", gn.page_hash("abc") == gn.page_hash("abc"))
    check("hash differs on change", gn.page_hash("abc") != gn.page_hash("abd"))

def main():
    test_embed_text()
    test_hash()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python engine/tools/tests/test_garden_neighbors.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'garden_neighbors'`.

- [ ] **Step 3: Implement the module skeleton + pure functions**

Create `engine/tools/garden_neighbors.py`:

```python
#!/usr/bin/env python3
r"""garden_neighbors.py - local, offline embedding oracle for the garden connect pass (Stage 5, read-only).

Fills the connect pass's lexical blind spot: garden_audit + F8 clustering match on shared
links/tags/tokens/proper-nouns, so an orphan written in novel vocabulary never clusters. This tool
builds a LOCAL embedding index (fastembed) and reports, per orphan/weakly-linked page, its
within-KB nearest-by-meaning neighbours as candidate links. It REPORTS; the garden model decides
(propose -> gate -> lane:review). Nothing leaves the machine (FamilyOffice is audit-grade).

Stdlib-only at import: fastembed/numpy are lazy-imported inside load_embedder() only, so the pure
functions import and run without the dep, and a missing dep degrades to SKIP (exit 0) - the lexical
connect pass is never blocked.

Usage:
  python garden_neighbors.py --vault-root <path> --kb-map '{"dev":"03_Dev",...}' \
        --cache-dir <env_root>/state/garden/embeddings [--json] [--k 5] [--floor 0.55] [--weak-threshold 2]
"""
import json, os, re, sys, hashlib, math

from garden_audit import audit_kb, walk_pages, _read, STRUCTURAL, ORPHAN_EXEMPT_DIRS

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"

_FM = re.compile(r"\A---\n.*?\n---\n", re.S)
_FENCE = re.compile(r"```.*?```", re.S)
_INLINE = re.compile(r"`[^`\n]*`")
_WIKILINK = re.compile(r"\[\[([^\]\|#]+)(?:[#\|][^\]]*)?\]\]")
_HEADING = re.compile(r"^#{1,6}\s*", re.M)


def build_embed_text(raw, max_chars=2000):
    t = _FM.sub("", raw, count=1)
    t = _FENCE.sub(" ", t)
    t = _INLINE.sub(" ", t)
    t = _WIKILINK.sub(lambda m: m.group(1).split("/")[-1].replace("-", " "), t)
    t = _HEADING.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:max_chars]


def page_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python engine/tools/tests/test_garden_neighbors.py`
Expected: PASS — the embed-text + hash checks pass; module imports stdlib-only.

- [ ] **Step 5: Commit**

```bash
git add engine/tools/garden_neighbors.py engine/tools/tests/test_garden_neighbors.py
git commit -m "A65 t2: garden_neighbors embed-text + hashing (stdlib-only)"
```

---

### Task 3: `garden_neighbors` — content-hash cache + `embed_pages` (stub embedder)

Add the incremental cache: reuse a cached vector when the page's embed-text hash is unchanged, re-embed changed pages, drop deleted pages. Tested with an injected stub embedder — no model needed.

**Files:**
- Modify: `engine/tools/garden_neighbors.py`
- Modify: `engine/tools/tests/test_garden_neighbors.py`

**Interfaces:**
- Produces: `load_cache(path: str) -> dict` — `{rel: {"hash": str, "vec": [float]}}`; returns `{}` on missing/corrupt/non-dict.
- Produces: `save_cache(path: str, cache: dict) -> None` — atomic write (`.tmp` + `os.replace`), makes parent dirs.
- Produces: `embed_pages(page_texts: dict[str,str], embedder, cache: dict) -> tuple[dict[str,list[float]], dict]` — returns `(vectors, new_cache)`; `new_cache` contains only rels present in `page_texts` (deleted pages evicted); `embedder.embed(list[str]) -> list[list[float]]` is called only for changed/new pages.

- [ ] **Step 1: Write the failing test (add to `test_garden_neighbors.py`)**

Add this helper and test function, and call `test_cache()` from `main()` before the summary:

```python
class StubEmbedder:
    """Deterministic stub: vector = [len(text), count of 'a', count of 'b']. Records call args."""
    def __init__(self):
        self.calls = []
    def embed(self, texts):
        texts = list(texts)
        self.calls.append(texts)
        return [[float(len(t)), float(t.count("a")), float(t.count("b"))] for t in texts]

def test_cache():
    d = tempfile.mkdtemp(prefix="gn-cache-")
    try:
        path = os.path.join(d, "dev.json")
        check("load_cache missing -> {}", gn.load_cache(path) == {})

        emb = StubEmbedder()
        texts = {"a.md": "aaa", "b.md": "bbb"}
        vecs, cache = gn.embed_pages(texts, emb, {})
        check("embed_pages returns a vector per page", set(vecs) == {"a.md", "b.md"})
        check("embed_pages embedded both first time", sorted(emb.calls[0]) == ["aaa", "bbb"])
        gn.save_cache(path, cache)
        check("cache persisted", os.path.isfile(path))

        # reload: unchanged page must be a cache hit (not re-embedded)
        emb2 = StubEmbedder()
        cache2 = gn.load_cache(path)
        texts2 = {"a.md": "aaa", "b.md": "bbbb"}   # b changed, a unchanged
        vecs2, cache3 = gn.embed_pages(texts2, emb2, cache2)
        embedded = [t for call in emb2.calls for t in call]
        check("unchanged page reused from cache", "aaa" not in embedded)
        check("changed page re-embedded", "bbbb" in embedded)
        check("changed vector updated", vecs2["b.md"][0] == 4.0)

        # deletion: a.md dropped from input -> evicted from new_cache
        _, cache4 = gn.embed_pages({"b.md": "bbbb"}, StubEmbedder(), cache3)
        check("deleted page evicted from cache", "a.md" not in cache4)

        # corrupt cache file -> {}
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ not json")
        check("corrupt cache -> {}", gn.load_cache(path) == {})
    finally:
        shutil.rmtree(d, ignore_errors=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python engine/tools/tests/test_garden_neighbors.py`
Expected: FAIL — `AttributeError: module 'garden_neighbors' has no attribute 'load_cache'`.

- [ ] **Step 3: Implement cache + `embed_pages`**

Append to `engine/tools/garden_neighbors.py`:

```python
def load_cache(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except (OSError, ValueError):
        pass
    return {}


def save_cache(path, cache):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    os.replace(tmp, path)


def embed_pages(page_texts, embedder, cache):
    """Reuse cached vectors on hash match; embed changed/new pages; evict deleted pages."""
    new_cache = {}
    vectors = {}
    to_embed = []
    for rel, text in page_texts.items():
        h = page_hash(text)
        ent = cache.get(rel)
        if ent and ent.get("hash") == h and isinstance(ent.get("vec"), list):
            new_cache[rel] = ent
            vectors[rel] = ent["vec"]
        else:
            to_embed.append((rel, text, h))
    if to_embed:
        embs = embedder.embed([t for _, t, _ in to_embed])
        for (rel, _, h), vec in zip(to_embed, embs):
            vec = [float(x) for x in vec]
            new_cache[rel] = {"hash": h, "vec": vec}
            vectors[rel] = vec
    return vectors, new_cache
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python engine/tools/tests/test_garden_neighbors.py`
Expected: PASS — all cache checks green.

- [ ] **Step 5: Commit**

```bash
git add engine/tools/garden_neighbors.py engine/tools/tests/test_garden_neighbors.py
git commit -m "A65 t3: incremental content-hash embedding cache"
```

---

### Task 4: `garden_neighbors` — cosine, target/exclude selection, `nearest`

The pure math + selection core: cosine similarity, target selection (orphans + weakly-linked, structural/journal excluded), exclude-set construction (already-linked either direction + self), and top-k-above-floor nearest neighbours.

**Files:**
- Modify: `engine/tools/garden_neighbors.py`
- Modify: `engine/tools/tests/test_garden_neighbors.py`

**Interfaces:**
- Produces: `cosine(a: list[float], b: list[float]) -> float` — 0.0 if either norm is 0.
- Produces: `select_targets(inbound: dict[str,int], weak_threshold: int = 2) -> list[str]` — rels with inbound `< weak_threshold`, excluding structural pages (`index`/`log`/`README`) and `ORPHAN_EXEMPT_DIRS` (journal).
- Produces: `build_excludes(adjacency: dict[str,list[str]]) -> dict[str,set[str]]` — per rel, the union of its outbound targets and its inbound sources.
- Produces: `nearest(vectors: dict[str,list[float]], targets: list[str], excludes: dict[str,set[str]], k: int = 5, floor: float = 0.55) -> dict[str, list[dict]]` — `{target: [{"neighbor": rel, "score": float}, ...]}`; excludes self + already-linked; only targets with ≥1 neighbour appear.

- [ ] **Step 1: Write the failing test (add to `test_garden_neighbors.py`)**

Add and call `test_nearest()` from `main()`:

```python
def test_selection_and_nearest():
    inbound = {"orphan.md": 0, "weak.md": 1, "strong.md": 3,
               "index.md": 0, "journal/2026-01-01.md": 0}
    targets = gn.select_targets(inbound, weak_threshold=2)
    check("orphan is a target", "orphan.md" in targets)
    check("weakly-linked is a target", "weak.md" in targets)
    check("strong page is not a target", "strong.md" not in targets)
    check("structural index excluded", "index.md" not in targets)
    check("journal excluded", "journal/2026-01-01.md" not in targets)

    adjacency = {"orphan.md": [], "near.md": [], "far.md": [], "linked.md": []}
    adjacency["orphan.md"] = ["linked.md"]           # orphan already links linked.md
    excludes = gn.build_excludes(adjacency)
    check("exclude covers outbound", "linked.md" in excludes["orphan.md"])
    check("exclude covers inbound (inverse)", "orphan.md" in excludes["linked.md"])

    vectors = {
        "orphan.md": [1.0, 0.0, 0.0],
        "near.md":   [0.9, 0.1, 0.0],   # high cosine with orphan
        "far.md":    [0.0, 0.0, 1.0],   # orthogonal -> below floor
        "linked.md": [1.0, 0.0, 0.0],   # identical but already-linked -> excluded
    }
    res = gn.nearest(vectors, ["orphan.md"], excludes, k=5, floor=0.5)
    names = [n["neighbor"] for n in res.get("orphan.md", [])]
    check("near neighbour surfaced", "near.md" in names)
    check("orthogonal below floor dropped", "far.md" not in names)
    check("already-linked excluded", "linked.md" not in names)
    check("self excluded", "orphan.md" not in names)
    check("scores descending", res["orphan.md"] == sorted(res["orphan.md"], key=lambda x: -x["score"]))

    # top-k cap
    vecs2 = {"t.md": [1.0, 0.0]}
    for i in range(8):
        vecs2[f"n{i}.md"] = [1.0, 0.01 * i]
    res2 = gn.nearest(vecs2, ["t.md"], {}, k=3, floor=0.0)
    check("top-k caps at 3", len(res2["t.md"]) == 3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python engine/tools/tests/test_garden_neighbors.py`
Expected: FAIL — `AttributeError: ... has no attribute 'cosine'` (or `select_targets`).

- [ ] **Step 3: Implement the math + selection**

Append to `engine/tools/garden_neighbors.py`:

```python
def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _exempt(rel):
    if os.path.basename(rel).lower() in STRUCTURAL:
        return True
    top = rel.split("/")[0].casefold() if "/" in rel else ""
    return top in ORPHAN_EXEMPT_DIRS


def select_targets(inbound, weak_threshold=2):
    return [rel for rel, c in inbound.items() if c < weak_threshold and not _exempt(rel)]


def build_excludes(adjacency):
    ex = {rel: set(neigh) for rel, neigh in adjacency.items()}
    for src, neigh in adjacency.items():
        for tgt in neigh:
            ex.setdefault(tgt, set()).add(src)
    return ex


def nearest(vectors, targets, excludes, k=5, floor=0.55):
    out = {}
    items = list(vectors.items())
    for t in targets:
        tv = vectors.get(t)
        if tv is None:
            continue
        ex = excludes.get(t, set())
        scored = []
        for rel, v in items:
            if rel == t or rel in ex:
                continue
            s = cosine(tv, v)
            if s >= floor:
                scored.append((rel, s))
        scored.sort(key=lambda x: (-x[1], x[0]))
        if scored:
            out[t] = [{"neighbor": r, "score": round(s, 4)} for r, s in scored[:k]]
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python engine/tools/tests/test_garden_neighbors.py`
Expected: PASS — selection, exclude, and nearest checks green.

- [ ] **Step 5: Commit**

```bash
git add engine/tools/garden_neighbors.py engine/tools/tests/test_garden_neighbors.py
git commit -m "A65 t4: cosine + target/exclude selection + nearest"
```

---

### Task 5: `garden_neighbors` — `run()` orchestration, `load_embedder` (lazy), CLI + fail-soft SKIP

Tie it together: `run()` takes an embedder (so tests inject the stub), walks each KB, embeds via cache, selects targets/excludes from `audit_kb`, and returns per-KB candidates. `load_embedder()` lazy-imports `fastembed` and returns `None` on any failure. `main()` SKIPs (exit 0) when the embedder is `None`.

**Files:**
- Modify: `engine/tools/garden_neighbors.py`
- Modify: `engine/tools/tests/test_garden_neighbors.py`

**Interfaces:**
- Produces: `run(vault_root: str, kb_map: dict[str,str], embedder, cache_dir: str, k: int = 5, floor: float = 0.55, weak_threshold: int = 2) -> dict` — `{kb: {target_rel: [{"neighbor", "score"}]}}`; embeds content pages (structural pages excluded from the pool), writes `<cache_dir>/<kb>.json`.
- Produces: `load_embedder(model_name: str = DEFAULT_MODEL)` — an object with `.embed(list[str]) -> list[list[float]]`, or `None` if `fastembed`/model is unavailable.

- [ ] **Step 1: Write the failing test (add to `test_garden_neighbors.py`)**

Add and call `test_run()` from `main()`:

```python
def test_run_end_to_end():
    vault = tempfile.mkdtemp(prefix="gn-run-")
    cache = tempfile.mkdtemp(prefix="gn-runcache-")
    try:
        B = "03_Dev/wiki/"
        # a well-connected pair (topic: cats) + an orphan whose text is about cats (novel vocab, no links)
        w(vault, B + "knowledge/cats.md", "# Cats\n[[knowledge/kittens]] purr and meow.\n")
        w(vault, B + "knowledge/kittens.md", "# Kittens\n[[knowledge/cats]] small felines.\n")
        w(vault, B + "entities/feline-orphan.md", "# Feline Orphan\nmeow purr felines cats kittens\n")
        w(vault, B + "index.md", "- [[knowledge/cats]]\n- [[knowledge/kittens]]\n")

        # StubEmbedder here yields [len, a-count, b-count]; craft texts so the orphan is nearest
        # to cats/kittens is not the point — assert structure, not semantics, with the stub:
        emb = StubEmbedder()
        res = gn.run(vault, {"dev": "03_Dev"}, emb, cache, k=5, floor=0.0, weak_threshold=2)
        check("run returns the dev KB", "dev" in res)
        check("orphan is a target key", "entities/feline-orphan.md" in res["dev"])
        # cats.md has inbound=2 (index+kittens) -> not a target
        check("well-connected page not a target", "knowledge/cats.md" not in res["dev"])
        # structural index never a neighbour
        flat = [n["neighbor"] for cand in res["dev"].values() for n in cand]
        check("index.md never suggested", "index.md" not in flat)
        # cache file written for the KB
        check("per-KB cache written", os.path.isfile(os.path.join(cache, "dev.json")))
    finally:
        shutil.rmtree(vault, ignore_errors=True)
        shutil.rmtree(cache, ignore_errors=True)

def test_load_embedder_softfail():
    # Simulate fastembed absent by shadowing the import.
    import builtins
    real_import = builtins.__import__
    def fake(name, *a, **k):
        if name == "fastembed":
            raise ImportError("no fastembed")
        return real_import(name, *a, **k)
    builtins.__import__ = fake
    try:
        check("load_embedder returns None when fastembed absent", gn.load_embedder() is None)
    finally:
        builtins.__import__ = real_import
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python engine/tools/tests/test_garden_neighbors.py`
Expected: FAIL — `AttributeError: ... has no attribute 'run'`.

- [ ] **Step 3: Implement `run`, `load_embedder`, and the CLI**

Append to `engine/tools/garden_neighbors.py`:

```python
def run(vault_root, kb_map, embedder, cache_dir, k=5, floor=0.55, weak_threshold=2):
    result = {}
    for kb, folder in sorted((kb_map or {}).items()):
        a = audit_kb(vault_root, folder)
        inbound = a.get("inbound", {})
        adjacency = a.get("adjacency", {})
        page_paths = walk_pages(vault_root, folder)
        page_texts = {}
        for rel, abspath in page_paths.items():
            if os.path.basename(rel).lower() in STRUCTURAL:
                continue
            page_texts[rel] = build_embed_text(_read(abspath))
        cache_path = os.path.join(cache_dir, f"{kb}.json")
        vectors, new_cache = embed_pages(page_texts, embedder, load_cache(cache_path))
        save_cache(cache_path, new_cache)
        targets = [t for t in select_targets(inbound, weak_threshold) if t in vectors]
        neigh = nearest(vectors, targets, build_excludes(adjacency), k=k, floor=floor)
        if neigh:
            result[kb] = neigh
    return result


class _FastEmbedder:
    def __init__(self, model):
        self._model = model
    def embed(self, texts):
        return [list(map(float, v)) for v in self._model.embed(list(texts))]


def load_embedder(model_name=DEFAULT_MODEL):
    try:
        from fastembed import TextEmbedding
        return _FastEmbedder(TextEmbedding(model_name=model_name))
    except Exception:
        return None


def _utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _arg(a, flag, default=None, cast=str):
    return cast(a[a.index(flag) + 1]) if flag in a else default


if __name__ == "__main__":
    _utf8_stdio()
    a = sys.argv[1:]
    if "--vault-root" not in a or "--kb-map" not in a or "--cache-dir" not in a:
        print(__doc__); sys.exit(1)
    vault_root = _arg(a, "--vault-root")
    kb_map = json.loads(_arg(a, "--kb-map"))
    cache_dir = _arg(a, "--cache-dir")
    k = _arg(a, "--k", 5, int)
    floor = _arg(a, "--floor", 0.55, float)
    weak = _arg(a, "--weak-threshold", 2, int)
    embedder = load_embedder()
    if embedder is None:
        print("SKIP: local embedder unavailable (fastembed not installed, or model not "
              "downloadable offline). Semantic connect pass skipped; lexical connect pass unaffected.")
        sys.exit(0)
    res = run(vault_root, kb_map, embedder, cache_dir, k=k, floor=floor, weak_threshold=weak)
    if "--json" in a:
        print(json.dumps(res, indent=1, ensure_ascii=False)); sys.exit(0)
    tot_targets = sum(len(v) for v in res.values())
    tot_cands = sum(len(c) for v in res.values() for c in v.values())
    print("GARDEN NEIGHBORS report (read-only):")
    for kb, v in res.items():
        print(f"  {kb}: {len(v)} target(s) with candidates")
        for tgt, cands in v.items():
            joined = ", ".join(f"{c['neighbor']} ({c['score']})" for c in cands)
            print(f"      {tgt} -> {joined}")
    print(f"  TOTAL: {tot_targets} target(s), {tot_cands} candidate link(s)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python engine/tools/tests/test_garden_neighbors.py`
Expected: PASS — `N passed, 0 failed` (all embed/cache/nearest/run/soft-fail checks).

- [ ] **Step 5: Commit**

```bash
git add engine/tools/garden_neighbors.py engine/tools/tests/test_garden_neighbors.py
git commit -m "A65 t5: run() orchestration + lazy load_embedder + CLI + fail-soft SKIP"
```

---

### Task 6: Rulebook pass + garden wiring (SKILL.md, deploy task, README tier map)

Add the judgment rulebook for the semantic-connect pass and wire the oracle into the garden run. This is the prose/contract layer; its "test" is a live smoke run of the tool proving it produces a JSON candidate list, plus the drift/spec hooks.

**Files:**
- Create: `skills/garden/rulebook/passes-semantic-connect.md`
- Modify: `skills/garden/rulebook/README.md` (tier map — add the new pass under the SEMANTIC tier)
- Modify: `skills/garden/SKILL.md` (`# Run` step 1 — run `garden_neighbors.py` and work its candidates)
- Modify: `deploy/tasks/garden.md` (step 2 Connect — same pointer, native constants)

**Interfaces:**
- Consumes: `garden_neighbors.py --json` output `{kb: {target_rel: [{neighbor, score}]}}` (Task 5).

- [ ] **Step 1: Create the rulebook pass**

Create `skills/garden/rulebook/passes-semantic-connect.md`:

```markdown
# F-SC — Semantic Connect (pass implementation)

**Tier: SEMANTIC — every finding, every KB.** `lane: review`, `recommended: hold`.
Never auto-ships on any KB.

## Input
`garden_neighbors.py --json` reports, per KB, each orphan/weakly-linked page's within-KB
nearest-by-meaning candidates: `{kb: {target_rel: [{neighbor, score}, ...]}}`. The tool is a
mechanical ORACLE (a local embedding index) — its list is *candidates*, not decisions. If the tool
prints `SKIP: ...` (embedder unavailable), this pass does not run this cycle; the lexical connect
pass (audit orphans/dead-links + F2/F8) runs unchanged.

## How this pass works
For each `(target -> candidate, score)`, read the target page and the candidate page, then judge:
- **Real relationship?** Propose a wikilink only if the two pages are genuinely related — the score
  is a similarity prior, not proof. A high score between a person page and an unrelated concept that
  merely shares register is a false positive; drop it.
- **Direction + star-topology.** Link the way the KB's architecture wants it: domain entries link
  to their hub/index page, not sideways to each other (the star-topology rule). Usually the edit is
  on the *target* (the under-connected page), pointing at its hub or its true topical neighbour.
- **Within-KB only (v1).** Candidates are already within-KB; never propose a cross-KB link here.
- **Paper-Governs.** A link that would assert an ownership/economic relationship is held for the
  human at the gate; never let a similarity score imply a papered fact.

## Proposal
A `lane: review`, `recommended: hold` connect proposal (the F9.2 / step-6 connect shape): write the
target page's staging draft with the added wikilink(s), `rec_reason` naming the candidate + why the
relationship is real (never just "high similarity"). Verify no new dead links. Record in the run
note: candidates surfaced vs. links proposed — a persistent "many candidates, zero proposals" means
the floor is mis-tuned or this pass is being skipped.

## Cross-framework constraints
| Constraint | What F-SC does |
|---|---|
| Embeddings are an input, never an auto-link | Only `lane: review` proposals; never `auto-ship` |
| Never create dead wikilinks | Verify the proposed link resolves before enqueueing |
| Never edit CLAUDE.md / SKILL.md / `_schema/` | Out of scope; flag-only in the run note |
| FamilyOffice audit-grade | Local embeddings only (enforced by the tool); Paper-Governs holds |
```

- [ ] **Step 2: Add the pass to the tier map**

In `skills/garden/rulebook/README.md`, add a row/line placing `passes-semantic-connect.md` under the **SEMANTIC** tier (alongside the F2/F8 passes), noting: "F-SC — embedding-neighbour candidates for orphans/weakly-linked; `lane: review`; consumes `garden_neighbors.py`; SKIPs cleanly if the embedder is absent." Match the file's existing formatting for a tier entry.

- [ ] **Step 3: Wire the oracle into SKILL.md step 1**

In `skills/garden/SKILL.md`, in `# Run` step 1 (Connect), after the `garden_hygiene.py` call and before "Then work the REMAINING audit lists semantically", insert the semantic-connect oracle call and its handling:

```markdown
   Then the semantic oracle — one more read-only call (fail-soft):
   ```
   python "${CLAUDE_PLUGIN_ROOT}/engine/tools/garden_neighbors.py" --vault-root "<vault>" \
     --kb-map '<the profile vault.live_kb_map as one-line JSON>' \
     --cache-dir "<env_root>/state/garden/embeddings" --json
   ```
   It reports, per KB, each orphan/weakly-linked page's within-KB nearest-by-meaning candidates
   (a local embedding index; nothing leaves the machine). If it prints `SKIP: ...` (embedder
   unavailable), skip this leg — the lexical connect pass below is unaffected. Otherwise work the
   candidates per `rulebook/passes-semantic-connect.md`: for each `(target -> candidate)`, propose
   the wikilink only if the relationship is real (honour star-topology, within-KB, Paper-Governs);
   enqueue `lane: review`. Record candidates-surfaced vs. links-proposed in the run note.
```

- [ ] **Step 4: Wire the oracle into the deploy task**

In `deploy/tasks/garden.md`, in step 2 (Content pass → Connect), add the same `garden_neighbors.py` call using the native `<vault>` / `<env_root>` constants already defined in that file (`--cache-dir "<env_root>/state/garden/embeddings" --json`), with the same "SKIP ⇒ lexical pass unaffected, else work per `passes-semantic-connect.md`, `lane: review`" instruction.

- [ ] **Step 5: Smoke-test the wiring against a hermetic vault**

Run (proves the CLI contract the SKILL now depends on — JSON shape and the SKIP path both work):

```bash
python - <<'PY'
import json, os, subprocess, sys, tempfile
vault = tempfile.mkdtemp(); cache = tempfile.mkdtemp()
B = os.path.join(vault, "03_Dev", "wiki", "knowledge"); os.makedirs(B)
open(os.path.join(B, "a.md"), "w").write("# A\nmeow purr felines\n")
open(os.path.join(B, "b.md"), "w").write("# B\nmeow purr felines cats\n")
cmd = [sys.executable, "engine/tools/garden_neighbors.py", "--vault-root", vault,
       "--kb-map", json.dumps({"dev": "03_Dev"}), "--cache-dir", cache, "--json"]
out = subprocess.run(cmd, capture_output=True, text=True)
print("exit", out.returncode)
print(out.stdout[:400] or out.stderr[:400])
PY
```

Expected: exit `0`. Either valid JSON `{...}` (fastembed installed — Task 7), **or** a line starting `SKIP:` (fastembed not yet installed). Both are correct — the point is exit 0 and a well-formed contract.

- [ ] **Step 6: Commit**

```bash
git add skills/garden/rulebook/passes-semantic-connect.md skills/garden/rulebook/README.md skills/garden/SKILL.md deploy/tasks/garden.md
git commit -m "A65 t6: semantic-connect rulebook pass + garden wiring"
```

---

### Task 7: Dependency declaration + gitignore the cache

Declare the new third-party dependency (contained, garden-scoped) and gitignore the regenerable embedding cache. Verify the real embedder path end-to-end (optional, network-gated) and that the cache is not tracked.

**Files:**
- Create: `engine/tools/requirements-garden.txt`
- Modify: `.gitignore` (repo root)
- Modify: `engine/tools/tests/test_garden_neighbors.py` (add the opt-in real-model integration test, skipped by default)

**Interfaces:** none (packaging + ignore rules).

- [ ] **Step 1: Declare the dependency**

Create `engine/tools/requirements-garden.txt`:

```
# A65 semantic connect oracle (garden_neighbors.py) — the engine's only third-party dep.
# Contained to the garden stage; every other tool is stdlib-only. Optional at runtime:
# garden_neighbors.py SKIPs cleanly (exit 0) when this is absent, so the lexical connect
# pass still runs. Local/offline embedder — no hosted API, no vault content leaves the machine.
fastembed>=0.3
```

- [ ] **Step 2: Gitignore the cache**

Add to the repo-root `.gitignore`:

```
# A65 — derived, machine-local, regenerable embedding cache (rebuilds from content hashes)
state/garden/embeddings/
```

- [ ] **Step 3: Verify the cache is ignored**

Run:

```bash
mkdir -p state/garden/embeddings && echo '{}' > state/garden/embeddings/dev.json
git check-ignore state/garden/embeddings/dev.json
```

Expected: prints `state/garden/embeddings/dev.json` (ignored). Then clean up: `rm -rf state/garden/embeddings`.

- [ ] **Step 4: Add the opt-in real-model integration test**

In `engine/tools/tests/test_garden_neighbors.py`, add a real-embedder test that is **skipped unless `A65_REAL_EMBED=1`** (so CI/offline runs never pull weights), and call it from `main()`:

```python
def test_real_embedder_optional():
    if os.environ.get("A65_REAL_EMBED") != "1":
        print("  skip test_real_embedder_optional (set A65_REAL_EMBED=1 to run)")
        return
    emb = gn.load_embedder()
    check("real embedder loads", emb is not None)
    if emb is None:
        return
    vecs = emb.embed(["cats and kittens", "felines purring"])
    check("real embedder returns 2 vectors", len(vecs) == 2)
    check("real vectors are non-trivial", len(vecs[0]) > 10 and gn.cosine(vecs[0], vecs[1]) > 0.3)
```

- [ ] **Step 5: Run the full hermetic suite (default, no network)**

Run: `python engine/tools/tests/test_garden_neighbors.py`
Expected: PASS — all hermetic checks green; the real-embedder test prints `skip ...`.

Optionally, with the dep installed and network available: `A65_REAL_EMBED=1 python engine/tools/tests/test_garden_neighbors.py` → also PASS.

- [ ] **Step 6: Commit**

```bash
git add engine/tools/requirements-garden.txt .gitignore engine/tools/tests/test_garden_neighbors.py
git commit -m "A65 t7: fastembed dep (garden-scoped) + gitignore embedding cache + opt-in real-model test"
```

---

## Self-Review

**Spec coverage:**
- `garden_neighbors.py` oracle (embed → cache → within-KB nearest for orphans+weakly-linked) → Tasks 2–5. ✓
- `garden_audit` refactor (inbound + adjacency, backward-compatible) → Task 1 (+ regression via existing `test_garden_audit.py`). ✓
- `passes-semantic-connect.md` + SKILL.md step 1 + deploy task + README tier map → Task 6. ✓
- Gitignored cache at `state/garden/embeddings/<kb>.json` → Tasks 5 (write) + 7 (ignore). ✓
- Garden-scoped `requirements.txt` (`fastembed`, first third-party dep) → Task 7. ✓
- Fail-soft SKIP (dep/model absent → exit 0, lexical path unaffected) → Task 5 (`load_embedder`/`main`) + Task 5 soft-fail test + Task 6 smoke test. ✓
- Local/offline only (no hosted API) → enforced structurally (lazy local `fastembed`, no network) + Global Constraints. ✓
- Within-KB v1, embeddings-as-input-never-auto-link, `lane: review` → Task 4 (per-KB), Task 6 (rulebook). ✓
- Testing: hermetic stub-embedder suite + opt-in real-model test + audit regression → Tasks 1–7. ✓

**Placeholder scan:** No "TBD"/"add error handling"/"similar to Task N". Task 6 steps 2 & 4 reference existing files' formatting rather than pasting their full current contents (they are small, local prose edits with the exact insertion text given); every code step shows complete code. ✓

**Type consistency:** `build_embed_text`, `page_hash`, `load_cache`/`save_cache`, `embed_pages` (returns `(vectors, new_cache)`), `cosine`, `select_targets`, `build_excludes`, `nearest`, `run`, `load_embedder` — names/signatures identical across the task that defines each and every later use. `audit_kb` additive keys `inbound`/`adjacency` defined in Task 1 and consumed in Task 5. Embedder protocol `.embed(list[str]) -> list[list[float]]` consistent between `StubEmbedder`, `_FastEmbedder`, and `embed_pages`. ✓
