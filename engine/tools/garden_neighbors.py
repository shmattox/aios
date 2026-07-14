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


def run(vault_root, kb_map, embedder, cache_dir, k=5, floor=0.55, weak_threshold=2):
    result = {}
    for kb, folder in sorted((kb_map or {}).items()):
        a = audit_kb(vault_root, folder)
        inbound = a.get("inbound", {})
        adjacency = a.get("adjacency", {})
        page_paths = walk_pages(vault_root, folder)
        page_texts = {}
        for rel, abspath in page_paths.items():
            if _exempt(rel):   # excludes STRUCTURAL + journal (ORPHAN_EXEMPT_DIRS) — spec: journal neither embedded nor suggested
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
    if flag in a:
        i = a.index(flag) + 1
        if i < len(a):                     # a trailing valueless flag degrades to default,
            return cast(a[i])              # never an IndexError (this leg must stay fail-soft)
    return default


if __name__ == "__main__":
    _utf8_stdio()
    a = sys.argv[1:]
    vault_root = _arg(a, "--vault-root")
    kb_map_raw = _arg(a, "--kb-map")
    cache_dir = _arg(a, "--cache-dir")
    if not vault_root or not kb_map_raw or not cache_dir:
        print(__doc__); sys.exit(1)        # required flags absent OR valueless -> usage, not a crash
    kb_map = json.loads(kb_map_raw)
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
