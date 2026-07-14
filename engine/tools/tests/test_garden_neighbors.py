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


class StubEmbedder:
    """Deterministic stub: vector = [len(text), count of 'a', count of 'b']. Records call args."""
    def __init__(self):
        self.calls = []
    def embed(self, texts):
        texts = list(texts)
        self.calls.append(texts)
        return [[float(len(t)), float(t.count("a")), float(t.count("b"))] for t in texts]


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
        w(vault, B + "journal/2026-01-01.md", "# 2026-01-01\nmeow purr felines cats kittens journal daily\n")

        # StubEmbedder here yields [len, a-count, b-count]; assert structure, not semantics
        emb = StubEmbedder()
        res = gn.run(vault, {"dev": "03_Dev"}, emb, cache, k=5, floor=0.0, weak_threshold=2)
        check("run returns the dev KB", "dev" in res)
        check("orphan is a target key", "entities/feline-orphan.md" in res["dev"])
        # cats.md has inbound=2 (index+kittens) -> not a target
        check("well-connected page not a target", "knowledge/cats.md" not in res["dev"])
        # structural index never a neighbour
        flat = [n["neighbor"] for cand in res["dev"].values() for n in cand]
        check("index.md never suggested", "index.md" not in flat)
        # journal pages: neither embedded/suggested nor a target (spec fidelity)
        check("journal not a target", "journal/2026-01-01.md" not in res["dev"])
        check("journal never suggested as neighbour", "journal/2026-01-01.md" not in flat)
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


def test_cli_skip_contract():
    """The load-bearing garden contract: with no embedder the CLI prints a `SKIP:` line and
    exits 0 (lexical connect pass unaffected). Force the no-embedder state deterministically by
    shadowing fastembed with a package that raises on import, so this holds even where the real
    dep is installed."""
    import subprocess
    d = tempfile.mkdtemp(prefix="gn-cli-")
    try:
        # a fake `fastembed` package that raises on import -> load_embedder() returns None
        os.makedirs(os.path.join(d, "fastembed"))
        with open(os.path.join(d, "fastembed", "__init__.py"), "w", encoding="utf-8") as f:
            f.write("raise ImportError('forced-absent for SKIP contract test')\n")
        vault = os.path.join(d, "vault"); cache = os.path.join(d, "cache")
        w(vault, "03_Dev/wiki/knowledge/a.md", "# A\nbody\n")
        env = dict(os.environ)
        env["PYTHONPATH"] = d + os.pathsep + env.get("PYTHONPATH", "")
        cmd = [sys.executable, os.path.join(TOOLS, "garden_neighbors.py"),
               "--vault-root", vault, "--kb-map", json.dumps({"dev": "03_Dev"}),
               "--cache-dir", cache, "--json"]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        check("CLI exits 0 when embedder absent", r.returncode == 0)
        check("CLI prints SKIP: prefix", r.stdout.startswith("SKIP:"))
        check("CLI names the lexical pass as unaffected", "lexical connect pass unaffected" in r.stdout)

        # missing required flag -> usage + exit 1 (not a crash)
        r2 = subprocess.run([sys.executable, os.path.join(TOOLS, "garden_neighbors.py"),
                             "--vault-root", vault], capture_output=True, text=True, env=env)
        check("CLI missing required flag -> exit 1", r2.returncode == 1)
    finally:
        shutil.rmtree(d, ignore_errors=True)


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


def main():
    test_embed_text()
    test_hash()
    test_cache()
    test_selection_and_nearest()
    test_run_end_to_end()
    test_load_embedder_softfail()
    test_cli_skip_contract()
    test_real_embedder_optional()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
