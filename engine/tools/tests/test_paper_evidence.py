#!/usr/bin/env python3
# sanitize:allow-file — fixtures use synthetic entity names/ids by design (A79)
"""paper_evidence.py test harness (A75) — resolve papered_source -> local projection, per-source read
cache (A76), advisory attach (never touches lane/stage/recommended), no-paper-found residual, and the
brief hold-card render. Scratch; safe to delete."""
import json, os, sys, tempfile, shutil, subprocess

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS)
import queue_tx
import paper_evidence as pe
import brief_render

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ok  " if cond else " FAIL ") + name)

def run(op, *extra):
    return subprocess.run([sys.executable, os.path.join(HARNESS, "paper_evidence.py"), op] + list(extra),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")

d = tempfile.mkdtemp(prefix="pe_")
try:
    vault = os.path.join(d, "Vault")
    ents = os.path.join(vault, "02_FO", "wiki", "entities")
    rawd = os.path.join(vault, "02_FO", "raw", "inbox")
    os.makedirs(ents); os.makedirs(rawd)
    state = os.path.join(d, "state"); os.makedirs(state)
    queue = os.path.join(state, "queue.json")

    # an executed-paper projection stating a term, linked by a relative papered_source
    open(os.path.join(rawd, "acme-agreement.md"), "w", encoding="utf-8").write(
        "# Acme Note Agreement\n\n## Terms\n\nThe principal is $250,000 at 8% interest.\n")
    open(os.path.join(ents, "acme.md"), "w", encoding="utf-8").write(
        "---\ntype: entity\ntitle: Acme\npapered_source: ../../raw/inbox/acme-agreement.md\n---\n\nnotes.\n")
    # a second entity sharing the SAME projection (A76 cache)
    open(os.path.join(ents, "acme-sub.md"), "w", encoding="utf-8").write(
        "---\ntype: entity\ntitle: Acme Sub\npapered_source: ../../raw/inbox/acme-agreement.md\n---\n\nx.\n")
    # an entity with NO papered_source, and one with a DANGLING path
    open(os.path.join(ents, "nopaper.md"), "w", encoding="utf-8").write(
        "---\ntype: entity\ntitle: NoPaper\n---\n\nno paper.\n")
    open(os.path.join(ents, "dangling.md"), "w", encoding="utf-8").write(
        "---\ntype: entity\ntitle: Dangling\npapered_source: ../../raw/inbox/missing.md\n---\n\nx.\n")

    acme_page = os.path.join(ents, "acme.md")

    # 1. resolve — readable projection surfaces its text for the extract-and-compare
    r = json.loads(run("resolve", "--entity-page", acme_page, "--vault-root", vault).stdout)
    check("resolve: papered_source resolved + projection readable",
          r["readable"] and r["papered_source"].endswith("acme-agreement.md")
          and "principal is $250,000" in r["projection_text"])

    # 2. resolve — no papered_source -> not readable (-> the model uses no-paper-found)
    rn = json.loads(run("resolve", "--entity-page", os.path.join(ents, "nopaper.md"),
                        "--vault-root", vault).stdout)
    check("resolve: entity without papered_source -> readable False, no crash",
          rn["readable"] is False and rn["papered_source"] is None)

    # 3. resolve — dangling papered_source path -> not readable, no crash
    rd = json.loads(run("resolve", "--entity-page", os.path.join(ents, "dangling.md"),
                        "--vault-root", vault).stdout)
    check("resolve: dangling papered_source -> readable False, no crash", rd["readable"] is False)

    # 3b. resolve — a papered_source ESCAPING the vault root is confined out (no out-of-vault read)
    open(os.path.join(ents, "escape.md"), "w", encoding="utf-8").write(
        "---\ntype: entity\ntitle: Escape\npapered_source: ../../../../../../etc/passwd\n---\n\nx.\n")
    re_ = json.loads(run("resolve", "--entity-page", os.path.join(ents, "escape.md"),
                         "--vault-root", vault).stdout)
    check("resolve: papered_source escaping vault_root is confined out -> readable False",
          re_["readable"] is False and re_["projection_path"] is None)

    # 4. resolve-batch — two holds sharing one projection read it ONCE (A76 cache)
    rb = json.loads(run("resolve-batch", "--entity-pages",
                        json.dumps([acme_page, os.path.join(ents, "acme-sub.md")]),
                        "--vault-root", vault).stdout)
    check("A76 cache: the 2nd hold on a shared papered_source is a cache hit (read once)",
          rb[0]["cache_hit"] is False and rb[1]["cache_hit"] is True and rb[1]["readable"])

    # 5. attach — ADVISORY INVARIANT: the packet lands but lane/stage/recommended are untouched
    json.dump({"queue": [{"id": "h-acme", "stage": "awaiting", "lane": "review",
                          "recommended": "hold", "conflict_key": "fo/wiki/entities/acme.md",
                          "draft_path": "02_FO/wiki/staging/acme.md",
                          "history": [{"ts": "2026-07-05T00:00:00Z", "stage": "awaiting"}]}]},
              open(queue, "w", encoding="utf-8"), indent=2)
    ra = run("attach", "--queue", queue, "--id", "h-acme", "--verdict", "matches",
             "--doc", "acme-agreement.md", "--section", "Terms",
             "--quote", "The principal is $250,000 at 8% interest.")
    it = next(i for i in queue_tx.load(queue)["queue"] if i["id"] == "h-acme")
    check("attach: matches verdict packet attached with doc/section/quote",
          ra.returncode == 0 and it["paper_evidence"]["verdict"] == "matches"
          and it["paper_evidence"]["quote"].startswith("The principal is $250,000"))
    check("A75 advisory invariant: a matches verdict does NOT change lane/stage/recommended",
          it["lane"] == "review" and it["stage"] == "awaiting" and it["recommended"] == "hold")

    # 6. attach — conflicts verdict (the high-value catch) is a valid packet, still advisory
    rc = run("attach", "--queue", queue, "--id", "h-acme", "--verdict", "conflicts",
             "--quote", "principal is $500,000")
    it2 = next(i for i in queue_tx.load(queue)["queue"] if i["id"] == "h-acme")
    check("attach: conflicts verdict recorded, lane still review (advisory)",
          rc.returncode == 0 and it2["paper_evidence"]["verdict"] == "conflicts"
          and it2["lane"] == "review")

    # 7. attach — no-paper-found needs no doc; an unknown verdict is refused
    rnp = run("attach", "--queue", queue, "--id", "h-acme", "--verdict", "no-paper-found")
    check("attach: no-paper-found verdict recorded without a doc", rnp.returncode == 0)
    rbad = run("attach", "--queue", queue, "--id", "h-acme", "--verdict", "approve")
    check("attach: an unknown verdict is refused (argparse choices)", rbad.returncode != 0)

    # 8. queue still validates
    check("final: queue validates", queue_tx.validate(queue_tx.load(queue)) is None)

    # 9. brief render — the packet renders one hold-card line; no packet renders nothing
    line = brief_render.render_paper_evidence(
        {"paper_evidence": {"verdict": "conflicts", "doc": "acme-agreement.md",
                            "section": "Terms", "quote": "principal is $500,000"}})
    check("render: conflicts packet renders one line with the quote + doc",
          "Paper evidence:" in line and "conflicts" in line and "principal is $500,000" in line
          and "acme-agreement.md" in line)
    check("render: an item with no packet renders nothing", brief_render.render_paper_evidence({}) == "")

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:", FAIL)
    sys.exit(1 if FAIL else 0)
finally:
    shutil.rmtree(d, ignore_errors=True)
