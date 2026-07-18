#!/usr/bin/env python3
# sanitize:allow-file — fixtures use synthetic/out-of-range ids by design (A79)
"""sort.py test harness — deterministic sort tables (A25): type→path conflict_keys, kb→lane
proposals + escalation signals, session-record pass-through, needs_judgment routing.
Scratch; safe to delete."""
import json, os, sys, tempfile, shutil, subprocess

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS)
import queue_tx

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ok  " if cond else " FAIL ") + name)

def run_cli(*extra):
    return subprocess.run([sys.executable, os.path.join(HARNESS, "sort.py")] + list(extra),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")

d = tempfile.mkdtemp(prefix="sort_")
try:
    vault = os.path.join(d, "Vault")
    queue = os.path.join(d, "queue.json")
    kb_map = json.dumps({"dev": "03_Dev", "fo": "02_FO"})
    auto = json.dumps(["dev"])
    raws = os.path.join(vault, "03_Dev", "raw", "inbox", "x")
    os.makedirs(raws)
    os.makedirs(os.path.join(vault, "03_Dev", "wiki", "entities"))
    os.makedirs(os.path.join(vault, "03_Dev", "wiki", "journal"))
    os.makedirs(os.path.join(vault, "02_FO", "raw", "inbox", "x"))

    def raw(kbdir, name, fm, body="plain notes.\n"):
        p = os.path.join(vault, kbdir, "raw", "inbox", "x", name)
        lines = "\n".join(f"{k}: {v}" for k, v in fm.items())
        open(p, "w", encoding="utf-8").write(f"---\n{lines}\n---\n\n{body}")
        return f"{kbdir}/raw/inbox/x/{name}"

    p_person   = raw("03_Dev", "jane.md",   {"type": "person", "title": "Jane Doe"})
    p_concept  = raw("03_Dev", "idea.md",   {"type": "concept", "title": "Spaced Repetition!"})
    p_tool     = raw("03_Dev", "tool.md",   {"type": "software", "title": "RipGrep"})
    p_econ     = raw("03_Dev", "money.md",  {"type": "article", "title": "Cap Table Basics"},
                     "how a cap table dilutes founders.\n")
    p_econdeep = raw("03_Dev", "deep.md",   {"type": "article", "title": "Long Read"},
                     ("filler text about nothing economic at all. " * 150)
                     + "\n\nburied at the tail: the operating agreement wired $2.5M via escrow.\n")
    p_untyped  = raw("03_Dev", "blob.md",   {"title": "Mystery"})
    p_fo       = raw("02_FO",  "fo-note.md", {"type": "article", "title": "FO Reading"})
    p_sess     = raw("03_Dev", "sess.md",   {"type": "session-record",
                                             "conflict_key": "dev/wiki/journal/2026-07-04.md"})
    p_sess2    = raw("03_Dev", "sess2.md",  {"type": "session-record",
                                             "conflict_key": "dev/wiki/journal/2026-07-03.md"})
    # incumbent daily note for the collision case
    open(os.path.join(vault, "03_Dev", "wiki", "journal", "2026-07-03.md"),
         "w", encoding="utf-8").write("# existing day\n")

    items = []
    for i, (cid, kb, pp) in enumerate([
            ("i-person", "dev", p_person), ("i-concept", "dev", p_concept),
            ("i-tool", "dev", p_tool), ("i-econ", "dev", p_econ),
            ("i-econdeep", "dev", p_econdeep),
            ("i-untyped", "dev", p_untyped), ("i-fo", "fo", p_fo),
            ("i-sess", "dev", p_sess), ("i-sess2", "dev", p_sess2)]):
        items.append({"id": cid, "stage": "captured", "kb": kb, "payload_path": pp,
                      "history": [{"ts": "2026-07-05T00:00:00Z", "stage": "captured"}]})
    json.dump({"queue": items}, open(queue, "w", encoding="utf-8"), indent=2)

    r = run_cli("run", "--queue", queue, "--vault-root", vault, "--kb-map", kb_map,
                "--auto-ship-kbs", auto)
    out = json.loads(r.stdout[r.stdout.index("{"):])   # queue_tx prints its update line first
    by = {i["id"]: i for i in queue_tx.load(queue)["queue"]}

    check("run exits 0", r.returncode == 0)
    check("person -> people/ + slugified title",
          by["i-person"]["conflict_key"] == "dev/wiki/people/jane-doe.md")
    check("concept -> knowledge/ (punctuation slugged away)",
          by["i-concept"]["conflict_key"] == "dev/wiki/knowledge/spaced-repetition.md")
    check("software -> entities/ where the KB keeps that folder",
          by["i-tool"]["conflict_key"] == "dev/wiki/entities/ripgrep.md")
    check("auto-ship kb, clean content -> auto-ship lane", by["i-person"]["lane"] == "auto-ship")
    check("economic signal escalates to review even in an auto-ship kb",
          by["i-econ"]["lane"] == "review")
    check("economic signal DEEP in the raw (past 4800 chars) still escalates",
          by["i-econdeep"]["lane"] == "review")
    check("non-auto-ship kb -> review (kb backstop)", by["i-fo"]["lane"] == "review")
    check("session-record pre-key used verbatim",
          by["i-sess"]["conflict_key"] == "dev/wiki/journal/2026-07-04.md")
    check("fresh journal day -> auto-ship", by["i-sess"]["lane"] == "auto-ship")
    check("journal collision (incumbent note) -> review", by["i-sess2"]["lane"] == "review")
    check("all routable items flipped to sorted",
          all(by[i]["stage"] == "sorted" for i in
              ("i-person", "i-concept", "i-tool", "i-econ", "i-econdeep", "i-fo", "i-sess", "i-sess2")))
    check("untyped raw -> needs_judgment, left captured",
          by["i-untyped"]["stage"] == "captured"
          and any(n["id"] == "i-untyped" for n in out["needs_judgment"]))
    check("run summary counts reconcile", out["sorted"] == 8 and len(out["needs_judgment"]) == 1)

    # `one` — model classified the ambiguous item; tool still owns the lane + flip
    r1 = run_cli("one", "--queue", queue, "--vault-root", vault, "--kb-map", kb_map,
                 "--auto-ship-kbs", auto, "--id", "i-untyped",
                 "--ck", "dev/wiki/sources/mystery.md")
    by2 = {i["id"]: i for i in queue_tx.load(queue)["queue"]}
    check("one: finalizes the judged ck + deterministic lane",
          r1.returncode == 0 and by2["i-untyped"]["stage"] == "sorted"
          and by2["i-untyped"]["conflict_key"] == "dev/wiki/sources/mystery.md"
          and by2["i-untyped"]["lane"] == "auto-ship")
    r2 = run_cli("one", "--queue", queue, "--vault-root", vault, "--kb-map", kb_map,
                 "--auto-ship-kbs", auto, "--id", "i-untyped",
                 "--ck", "dev/wiki/sources/mystery.md")
    check("one: refuses a non-captured item", r2.returncode != 0)

    # review_gates full clamps an auto-ship kb to review
    items2 = [{"id": "g-item", "stage": "captured", "kb": "dev", "payload_path": p_person,
               "history": []}]
    q2 = os.path.join(d, "q2.json")
    json.dump({"queue": items2}, open(q2, "w", encoding="utf-8"), indent=2)
    run_cli("run", "--queue", q2, "--vault-root", vault, "--kb-map", kb_map,
            "--auto-ship-kbs", auto, "--review-gates", json.dumps({"dev": "full"}))
    g = queue_tx.load(q2)["queue"][0]
    check("review_gates 'full' forces the review lane", g["lane"] == "review")

    # ── A89 worthiness floor ──────────────────────────────────────────────────────────────────
    import pipeline_health
    b_short   = raw("03_Dev", "b-short.md",  {"type": "bookmark", "source_tier": "tertiary",
                                              "title": "Cool Link"}, "a neat link.\n")
    b_econ    = raw("03_Dev", "b-econ.md",   {"type": "article", "source_tier": "tertiary",
                                              "title": "Note"}, "cap table thoughts.\n")
    b_linked  = raw("03_Dev", "b-linked.md", {"type": "bookmark", "source_tier": "tertiary",
                                              "title": "Linked", "papered_source": "raw/foo.pdf"},
                    "short.\n")
    b_chg_lo  = raw("03_Dev", "b-chg-lo.md", {"type": "bookmark", "source_tier": "tertiary",
                                              "title": "Charge"}, "vendor charge of $3.50 today.\n")
    b_chg_hi  = raw("03_Dev", "b-chg-hi.md", {"type": "bookmark", "source_tier": "tertiary",
                                              "title": "Big"}, "a $250 charge landed.\n")
    b_long    = raw("03_Dev", "b-long.md",   {"type": "bookmark", "source_tier": "tertiary",
                                              "title": "Long"}, "word " * 60)
    b_second  = raw("03_Dev", "b-second.md", {"type": "bookmark", "source_tier": "secondary",
                                              "title": "Sec"}, "short secondary.\n")
    fl_items = [{"id": cid, "stage": "captured", "kb": "dev", "payload_path": pp,
                 "history": [{"ts": "2026-07-05T00:00:00Z", "stage": "captured"}]}
                for cid, pp in [("f-short", b_short), ("f-econ", b_econ), ("f-linked", b_linked),
                                ("f-chglo", b_chg_lo), ("f-chghi", b_chg_hi), ("f-long", b_long),
                                ("f-second", b_second)]]
    q3 = os.path.join(d, "q3.json")
    clog = os.path.join(d, "context-log.jsonl")
    json.dump({"queue": fl_items}, open(q3, "w", encoding="utf-8"), indent=2)
    rf = run_cli("run", "--queue", q3, "--vault-root", vault, "--kb-map", kb_map,
                 "--auto-ship-kbs", auto, "--len-floor", "200", "--dollar-floor", "100",
                 "--context-log", clog)
    fo = json.loads(rf.stdout[rf.stdout.index("{"):])
    fb = {i["id"]: i for i in queue_tx.load(q3)["queue"]}
    check("A89: short tertiary non-economic capture -> reference (floored, not drafted)",
          fb["f-short"]["stage"] == "reference" and fb["f-short"]["lane"] is None)
    check("A89 invariant: econ-flagged short capture NOT floored (drafted + held review)",
          fb["f-econ"]["stage"] == "sorted" and fb["f-econ"]["lane"] == "review")
    check("A89 invariant: entity/paper-linked capture NOT floored",
          fb["f-linked"]["stage"] == "sorted")
    # block-style multi-line `entities:` list — the flat reader collapses it to "", so the invariant
    # must be enforced by scanning the raw block (else an entity-linked capture leaks to `reference`).
    b_ents = os.path.join(vault, "03_Dev", "raw", "inbox", "x", "b-ents.md")
    open(b_ents, "w", encoding="utf-8").write(
        "---\ntype: bookmark\nsource_tier: tertiary\ntitle: Ents\nentities:\n  - Largo\n  - Jenkins\n---\n\nshort.\n")
    q5 = os.path.join(d, "q5.json")
    json.dump({"queue": [{"id": "f-ents", "stage": "captured", "kb": "dev",
                          "payload_path": "03_Dev/raw/inbox/x/b-ents.md", "history": []}]},
              open(q5, "w", encoding="utf-8"), indent=2)
    run_cli("run", "--queue", q5, "--vault-root", vault, "--kb-map", kb_map, "--auto-ship-kbs", auto,
            "--len-floor", "200", "--dollar-floor", "100")
    check("A89 invariant: block-style multi-line entities list NOT floored (flat-reader leak closed)",
          queue_tx.load(q5)["queue"][0]["stage"] == "sorted")
    check("A89: sub-DOLLAR_FLOOR charge floored", fb["f-chglo"]["stage"] == "reference")
    check("A89: >=DOLLAR_FLOOR charge held (not floored)", fb["f-chghi"]["stage"] == "sorted")
    check("A89: above-LEN_FLOOR item drafts as today (regression)", fb["f-long"]["stage"] == "sorted")
    check("A89: non-tertiary tier not floored", fb["f-second"]["stage"] == "sorted")
    check("A89: run summary reports floored count + ids",
          fo["floored"] == 2 and set(fo["floored_ids"]) == {"f-short", "f-chglo"})
    check("A89: reference item still carries its conflict_key (re-openable draft target)",
          fb["f-short"].get("conflict_key") == "dev/wiki/sources/cool-link.md")
    check("A89 no-silent-caps: each floored item logged to the context-log",
          sum(1 for ln in open(clog, encoding="utf-8") if '"event": "floored"' in ln) == 2)
    check("A89 health line reports the floored count",
          "floored→raw" in pipeline_health.render(clog, hours=100000,
                                                   now="2026-07-05T12:00:00Z"))
    check("A89: a floored queue still validates (reference is a terminal stage)",
          queue_tx.validate(queue_tx.load(q3)) is None)
    # re-open: nothing is lost — the raw is untouched and the item flips back to captured cleanly
    reopened = {i["id"]: i for i in queue_tx.load(q3)["queue"]}["f-short"]
    reopened["stage"] = "captured"
    queue_tx._apply_items(q3, [reopened], "update")
    reloaded = {i["id"]: i for i in queue_tx.load(q3)["queue"]}["f-short"]
    check("A89: a floored item re-opens to captured (raw untouched, nothing lost)",
          os.path.exists(os.path.join(vault, b_short.replace("/", os.sep)))
          and reloaded["stage"] == "captured")
    # floor DISABLED by default (no thresholds) — the same short capture drafts as today
    q4 = os.path.join(d, "q4.json")
    json.dump({"queue": [{"id": "f-off", "stage": "captured", "kb": "dev", "payload_path": b_short,
                          "history": []}]}, open(q4, "w", encoding="utf-8"), indent=2)
    run_cli("run", "--queue", q4, "--vault-root", vault, "--kb-map", kb_map, "--auto-ship-kbs", auto)
    check("A89: unset thresholds -> floor disabled, capture drafts (safe default)",
          queue_tx.load(q4)["queue"][0]["stage"] == "sorted")

    check("final: queue validates", queue_tx.validate(queue_tx.load(queue)) is None)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:", FAIL)
    sys.exit(1 if FAIL else 0)
finally:
    shutil.rmtree(d, ignore_errors=True)
