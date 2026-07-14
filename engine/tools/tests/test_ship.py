#!/usr/bin/env python3
"""ship.py test harness — deterministic gate ship mechanics (A25): slug/target resolution,
draft location + legacy fallback, daily-note merge guard, revert pointers, queue flips.
Scratch; safe to delete."""
import json, os, sys, tempfile, shutil, subprocess

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../engine/tools
sys.path.insert(0, HARNESS)
import queue_tx

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ok  " if cond else " FAIL ") + name)

def run(op, *extra):
    r = subprocess.run([sys.executable, os.path.join(HARNESS, "ship.py"), op] + list(extra),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r

d = tempfile.mkdtemp(prefix="ship_")
try:
    vault = os.path.join(d, "Vault")
    state = os.path.join(d, "state")
    os.makedirs(state)
    queue = os.path.join(state, "queue.json")
    kb_map = json.dumps({"dev": "03_Dev"})
    staging = os.path.join(vault, "03_Dev", "wiki", "staging")
    os.makedirs(staging)

    def draft(name, text):
        p = os.path.join(staging, name)
        open(p, "w", encoding="utf-8").write(text)
        return p

    draft("godaddy.md", "---\ntype: company\n---\n\n# GoDaddy\n\nregistrar notes.\n")
    draft("2026-06-28.md", "---\ntype: session-record\n---\n\n## Session\n\nnew session entry.\n")
    draft("2026-06-29.md", "---\ntype: session-record\n---\n\n## Session\n\nfresh journal day.\n")
    # A43: a full-note HUSK draft — reproduces the whole incumbent body PLUS a new section (the
    # late-capture-append pattern). Appending it verbatim would duplicate the note.
    draft("2026-06-30.md", "---\ntype: session-record\n---\n\n# 2026-06-30\n\n## Sessions\n\n"
                           "- session one.\n\n### Added late\n\n- session two.\n")

    def item(cid, ck, dp, stage="awaiting"):
        return {"id": cid, "stage": stage, "lane": "auto-ship", "conflict_key": ck,
                "draft_path": dp, "history": [{"ts": "2026-07-05T00:00:00Z", "stage": stage}]}

    seed = {"queue": [
        item("it-godaddy", "dev/wiki/companies/godaddy.md", "03_Dev/wiki/staging/godaddy.md"),
        item("it-journal", "dev/wiki/journal/2026-06-28.md", "03_Dev/wiki/staging/2026-06-28.md"),
        item("it-journal-fresh", "dev/wiki/journal/2026-06-29.md", "03_Dev/wiki/staging/2026-06-29.md"),
        item("it-journal-husk", "dev/wiki/journal/2026-06-30.md", "03_Dev/wiki/staging/2026-06-30.md"),
        item("it-legacy", "dev/wiki/companies/legacyco.md", None),
        item("it-nodraft", "dev/wiki/companies/ghost.md", "03_Dev/wiki/staging/ghost.md"),
        item("it-badkb", "mystery/wiki/companies/x.md", "03_Dev/wiki/staging/godaddy.md"),
        item("it-reject-me", "dev/wiki/companies/bad.md", "03_Dev/wiki/staging/godaddy.md"),
    ]}
    rv = item("it-review", "dev/wiki/companies/held.md", "03_Dev/wiki/staging/held.md")
    rv["lane"] = "review"
    seed["queue"].append(rv)
    # seed by direct write: legacy draftless items are GRANDFATHERED already-awaiting state that
    # the guarded commit path (correctly) refuses to create fresh — the fixture models pre-guard data
    json.dump(seed, open(queue, "w", encoding="utf-8"), indent=2)
    draft("legacyco.md", "# LegacyCo\n\nfound via legacy staging fallback.\n")
    draft("held.md", "---\ntype: company\n---\n\n# Held\n\nreview-lane held draft.\n")

    # 1. resolve: slug stripping (incl. the journal `.md.md` bug class), target, excerpt
    r = run("resolve", "--queue", queue, "--vault-root", vault, "--kb-map", kb_map, "--id", "it-godaddy")
    f = json.loads(r.stdout)
    check("resolve: slug strips trailing .md", f["slug"] == "godaddy")
    check("resolve: target under kb-mapped base",
          f["target_path"].endswith(os.path.join("03_Dev", "wiki", "companies", "godaddy.md")))
    check("resolve: draft found via draft_path", f["draft_found"] and "registrar" in f["draft_excerpt"])
    check("resolve: non-journal flagged", f["is_journal"] is False)
    r2 = run("resolve", "--queue", queue, "--vault-root", vault, "--kb-map", kb_map, "--id", "it-journal")
    f2 = json.loads(r2.stdout)
    check("resolve: dated journal slug (the .md.md class)", f2["slug"] == "2026-06-28" and f2["is_journal"])
    r3 = run("resolve", "--queue", queue, "--vault-root", vault, "--kb-map", kb_map, "--id", "it-legacy")
    f3 = json.loads(r3.stdout)
    check("resolve: legacy staging fallback when draft_path is null",
          f3["draft_found"] and f3["draft_path"].endswith(os.path.join("staging", "legacyco.md")))

    # 2. unmapped kb fails loud
    rbad = run("resolve", "--queue", queue, "--vault-root", vault, "--kb-map", kb_map, "--id", "it-badkb")
    check("resolve: kb not in map fails loud (hold+flag)",
          rbad.returncode != 0 and "kb-map" in (rbad.stdout + rbad.stderr))

    # 3. ship (replace): canonical write + revert pointer + queue flip w/ approved_by
    rs = run("ship", "--queue", queue, "--vault-root", vault, "--kb-map", kb_map,
             "--id", "it-godaddy", "--approved-by", "auto-ship-scheduled")
    out = json.loads(rs.stdout.splitlines()[-1])
    target = os.path.join(vault, "03_Dev", "wiki", "companies", "godaddy.md")
    check("ship: exit 0 + canonical file written",
          rs.returncode == 0 and open(target, encoding="utf-8").read().endswith("registrar notes.\n"))
    ptr = json.load(open(out["revert_pointer"], encoding="utf-8"))
    check("ship: revert pointer complete (replace)",
          ptr["id"] == "it-godaddy" and ptr["merged"] is False and ptr["prev_content_path"] is None
          and ptr["shipped_path"] == target)
    it = next(i for i in queue_tx.load(queue)["queue"] if i["id"] == "it-godaddy")
    check("ship: queue flipped to shipped with approved_by",
          it["stage"] == "shipped" and it["history"][-1]["approved_by"] == "auto-ship-scheduled")
    # A30: the ship RETIRES the staging husk — staging ∩ canonical must be ∅ for this slug,
    # and the husk travels into the revert dir so undo-ship can restore it (move, never delete).
    husk = os.path.join(staging, "godaddy.md")
    check("A30 ship: staging husk retired (staging/canonical disjoint, no husk left)",
          (not os.path.exists(husk)) and os.path.exists(target))
    check("A30 ship: revert pointer records the archived husk (present on disk)",
          isinstance(ptr.get("staging_archived"), str)
          and os.path.exists(ptr["staging_archived"]))

    # 4. ship (journal MERGE guard): incumbent preserved, delimited append, prev copy
    jtarget = os.path.join(vault, "03_Dev", "wiki", "journal", "2026-06-28.md")
    os.makedirs(os.path.dirname(jtarget))
    open(jtarget, "w", encoding="utf-8").write("# 2026-06-28\n\nincumbent morning notes.\n")
    rj = run("ship", "--queue", queue, "--vault-root", vault, "--kb-map", kb_map,
             "--id", "it-journal", "--approved-by", "auto-ship-scheduled")
    merged = open(jtarget, encoding="utf-8").read()
    outj = json.loads(rj.stdout.splitlines()[-1])
    ptrj = json.load(open(outj["revert_pointer"], encoding="utf-8"))
    check("merge: incumbent preserved verbatim at head", merged.startswith("# 2026-06-28\n\nincumbent morning notes."))
    check("merge: delimited entry appended, frontmatter stripped",
          "merged by aios gate: it-journal" in merged and "new session entry." in merged
          and merged.count("type: session-record") == 0)
    check("merge: pre-merge copy + merged:true pointer",
          ptrj["merged"] is True and open(ptrj["prev_content_path"], encoding="utf-8").read()
          == "# 2026-06-28\n\nincumbent morning notes.\n")
    # a journal target that does NOT exist yet is a plain replace (no merge, no prev copy)
    rjf = run("ship", "--queue", queue, "--vault-root", vault, "--kb-map", kb_map,
              "--id", "it-journal-fresh", "--approved-by", "auto-ship-scheduled")
    outjf = json.loads(rjf.stdout.splitlines()[-1])
    check("merge: fresh journal day is a plain replace", outjf["merged"] is False)

    # 4b. A43 — full-note-husk merge must NOT duplicate the note. When the incoming draft is a
    # SUPERSET of the incumbent (a complete re-draft, e.g. the late-capture-append pattern), ship
    # REPLACES rather than appending — otherwise the whole body is duplicated (two H1s).
    hjtarget = os.path.join(vault, "03_Dev", "wiki", "journal", "2026-06-30.md")
    open(hjtarget, "w", encoding="utf-8").write("# 2026-06-30\n\n## Sessions\n\n- session one.\n")
    rh = run("ship", "--queue", queue, "--vault-root", vault, "--kb-map", kb_map,
             "--id", "it-journal-husk", "--approved-by", "auto-ship-scheduled")
    outh = json.loads(rh.stdout.splitlines()[-1])
    husktext = open(hjtarget, encoding="utf-8").read()
    n_h1 = sum(1 for ln in husktext.splitlines() if ln.startswith("# "))
    check("A43 merge: full-note-husk does not duplicate — exactly one H1", rh.returncode == 0 and n_h1 == 1)
    check("A43 merge: incumbent body kept exactly once (no dup)", husktext.count("- session one.") == 1)
    check("A43 merge: the new late-captured section is folded in",
          "### Added late" in husktext and "- session two." in husktext)
    check("A43 merge: superset re-draft still records merged:true + a pre-merge copy (revertible)",
          outh["merged"] is True and isinstance(outh.get("revert_pointer"), str))

    # 5. ship refusals: no draft on disk; non-awaiting stage
    rn = run("ship", "--queue", queue, "--vault-root", vault, "--kb-map", kb_map,
             "--id", "it-nodraft", "--approved-by", "x")
    check("ship: refused when no draft on disk (points at reject)",
          rn.returncode != 0 and "reject" in (rn.stdout + rn.stderr))
    ra = run("ship", "--queue", queue, "--vault-root", vault, "--kb-map", kb_map,
             "--id", "it-godaddy", "--approved-by", "x")
    check("ship: refused for a non-awaiting item", ra.returncode != 0 and "awaiting" in (ra.stdout + ra.stderr))
    rterm = run("reject", "--queue", queue, "--id", "it-godaddy", "--reason", "nope")
    check("reject: refused for a terminal (shipped) item — would orphan the vault file",
          rterm.returncode != 0 and "rewind" in (rterm.stdout + rterm.stderr))

    # 5b. review-lane guard: unattended ship refused; --human-approved ships it
    rg = run("ship", "--queue", queue, "--vault-root", vault, "--kb-map", kb_map,
             "--id", "it-review", "--approved-by", "auto-ship-scheduled")
    check("moat: review-lane ship refused without --human-approved",
          rg.returncode != 0 and "human" in (rg.stdout + rg.stderr))
    rg2 = run("ship", "--queue", queue, "--vault-root", vault, "--kb-map", kb_map,
              "--id", "it-review", "--approved-by", "human-gate", "--human-approved")
    check("moat: --human-approved ships the review-lane item", rg2.returncode == 0)

    # 5c. POINTER PARITY — rewind.undo_ship on a MERGED ship restores the incumbent, never deletes
    import rewind
    rewind.undo_ship(queue, "it-journal", vault, os.path.join(d, "state", "revert"),
                     kb_map=json.loads(kb_map))
    check("revert parity: merged undo-ship restores the pre-merge incumbent verbatim",
          open(jtarget, encoding="utf-8").read() == "# 2026-06-28\n\nincumbent morning notes.\n")
    itj = next(i for i in queue_tx.load(queue)["queue"] if i["id"] == "it-journal")
    check("revert parity: item returned to awaiting", itj["stage"] == "awaiting")
    # A30: undo-ship must also RESTORE the retired husk so `awaiting` is a valid, re-shippable
    # state again (reconcile expects an awaiting item to carry its draft).
    check("A30 revert: undo-ship restores the staging husk (merged case)",
          os.path.exists(os.path.join(staging, "2026-06-28.md")))

    # 5d. A30 replace-case round trip: undo-ship a plain replace restores husk + removes canonical
    rewind.undo_ship(queue, "it-godaddy", vault, os.path.join(d, "state", "revert"),
                     kb_map=json.loads(kb_map))
    itg = next(i for i in queue_tx.load(queue)["queue"] if i["id"] == "it-godaddy")
    check("A30 revert: undo-ship (replace) restores husk, removes canonical, item -> awaiting",
          os.path.exists(os.path.join(staging, "godaddy.md"))
          and (not os.path.exists(target)) and itg["stage"] == "awaiting")

    # 6. reject flips with the BLOCK reason
    rr = run("reject", "--queue", queue, "--id", "it-reject-me", "--reason", "source unresolvable")
    itr = next(i for i in queue_tx.load(queue)["queue"] if i["id"] == "it-reject-me")
    check("reject: flipped with reason in history",
          rr.returncode == 0 and itr["stage"] == "rejected"
          and itr["history"][-1]["reason"] == "source unresolvable")

    # 7. queue still validates after the run
    check("final: queue validates", queue_tx.validate(queue_tx.load(queue)) is None)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:", FAIL)
    sys.exit(1 if FAIL else 0)
finally:
    shutil.rmtree(d, ignore_errors=True)
