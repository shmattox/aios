#!/usr/bin/env python3
r"""H35/garden — full-inventory wiki audit: orphans + dead links (filesystem walk, not graph walk).

The 2026-07-05 catch-up lesson: garden Connect's frontier was the link graph ("recent + linked"),
so a page with zero inlinks was structurally unreachable — orphans accumulated monotonically (361
on the reference vault). The audit under test walks the FULL wiki inventory per KB and reports:
  - orphans: content pages with zero inbound wikilinks from any counted source page;
  - dead links: wikilink targets that resolve to no file.

Counting rules under test (each is a deliberate semantic, not an accident):
  - index.md links COUNT as inbound ("the index never goes stale" — indexed = minimally connected);
  - log.md is NOT a link source (append-only history; its links rot by design);
  - staging/ and .templates/ are excluded entirely (transient, not knowledge);
  - index.md / log.md / README.md are never themselves orphans (structural);
  - bare folder-name links ([[people]]) are structural references, not page links — skipped;
  - links inside fenced code blocks or inline backticks are examples/templates — skipped;
  - a target that exists under the KB ROOT outside wiki/ ([[outputs/queries/x]]) is not dead;
  - [[page|alias]] and [[page#anchor]] resolve on the page part;
  - bare-stem links ([[ambient-computing]]) resolve via unique stem match;
  - kb_map: only mapped folders are audited (an unmapped folder is skipped, never scanned).

Hermetic: everything under tempfile.mkdtemp(). Run: python engine/tools/tests/test_garden_audit.py
"""
import json, os, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)
import garden_audit

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok   {name}")
    else:
        FAIL += 1; print(f"  FAIL {name}")


def w(root, rel, text=""):
    p = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    vault = tempfile.mkdtemp(prefix="audit-vault-")
    try:
        KB_MAP = {"dev": "03_Dev"}
        B = "03_Dev/wiki/"
        # linked pair: hub links leaf
        w(vault, B + "projects/hub.md", "# hub\nsee [[knowledge/leaf]]\n")
        w(vault, B + "knowledge/leaf.md", "# leaf\nback to [[projects/hub]]\n")
        # orphan: nothing links it
        w(vault, B + "entities/lonely.md", "# lonely\n")
        # indexed-only page: index link counts -> NOT an orphan
        w(vault, B + "knowledge/indexed-only.md", "# indexed only\n")
        # log-only page: log.md is not a source -> IS an orphan
        w(vault, B + "knowledge/log-only.md", "# log only\n")
        # stem-resolved link + alias/anchor forms
        w(vault, B + "people/ada.md", "# ada\n")
        w(vault, B + "companies/acme.md", "[[ada|Ada L]] and [[knowledge/leaf#notes]]\n")
        # dead link + folder-name link + code-fenced link, all from one page
        w(vault, B + "knowledge/linker.md",
          "[[missing-page]] and [[people]] folder\n```\n[[fenced-example]]\n```\nand `[[inline-example]]`\n")
        # outputs fallback: target exists under KB root outside wiki/
        w(vault, "03_Dev/outputs/queries/report.md", "x")
        w(vault, B + "knowledge/citer.md", "see [[outputs/queries/report]]\n[[projects/hub]]\n")
        # structural files: index links count; log links do not; none are orphans
        w(vault, B + "index.md", "- [[knowledge/indexed-only]]\n- [[projects/hub]]\n- [[knowledge/linker]]\n"
                                 "- [[companies/acme]]\n- [[knowledge/citer]]\n- [[knowledge/log-only-DEAD-NO]]\n")
        w(vault, B + "log.md", "- touched [[knowledge/log-only]] and [[staging/gone-draft]]\n")
        w(vault, B + "README.md", "docs\n")
        # excluded dirs: never pages, never sources
        w(vault, B + "staging/draft.md", "[[nothing-counts-here]]\n")
        w(vault, B + ".templates/tpl.md", "[[tpl-link]]\n")
        # journal exemption: unlinked daily is by-design episodic, never an orphan;
        # but its DEAD links still report (it stays a link source)
        w(vault, B + "journal/2026-01-01.md", "worked on [[missing-from-journal]]\n")
        # stem collision: one bare [[twin]] link must credit BOTH twins (neither orphan) -
        # first-hit attribution would mask whichever twin lost the pick
        w(vault, B + "knowledge/twin.md", "# twin a\n")
        w(vault, B + "entities/twin.md", "# twin b\n")
        w(vault, B + "projects/twin-linker.md", "[[twin]] and [[projects/hub]]\n")
        # self-link is not connection
        w(vault, B + "entities/selfie.md", "[[entities/selfie]]\n")
        # traversal-shaped target must not resolve outside the KB
        w(vault, "outside-secret.md", "x")
        w(vault, B + "knowledge/traverser.md", "[[../../../outside-secret]] [[projects/hub]]\n")
        # unmapped folder: never scanned
        w(vault, "99_Unmapped/wiki/ghost.md", "# ghost\n")

        res = garden_audit.audit(vault, KB_MAP)
        check("returns one entry per mapped kb", set(res.keys()) == {"dev"})
        dev = res["dev"]
        orphans = set(dev["orphans"])

        check("unlinked page is an orphan", "entities/lonely.md" in orphans)
        check("index.md link counts as inbound (indexed-only NOT orphan)",
              "knowledge/indexed-only.md" not in orphans)
        check("log.md is not a link source (log-only IS orphan)",
              "knowledge/log-only.md" in orphans)
        check("linked pair not orphans",
              not {"projects/hub.md", "knowledge/leaf.md"} & orphans)
        check("stem/alias link resolves (ada NOT orphan)", "people/ada.md" not in orphans)
        check("structural files never orphans",
              not {"index.md", "log.md", "README.md"} & orphans)
        check("staging/.templates pages not audited",
              not any(o.startswith(("staging/", ".templates/")) for o in orphans))
        check("journal/ pages exempt from orphan report (episodic by design)",
              "journal/2026-01-01.md" not in orphans)
        check("stem collision credits ALL hits (neither twin orphan)",
              not {"knowledge/twin.md", "entities/twin.md"} & orphans)
        check("self-link is not connection (selfie IS orphan)",
              "entities/selfie.md" in orphans)

        dead_targets = {t for (_src, t) in dev["dead_links"]}
        dead_sources = {s for (s, _t) in dev["dead_links"]}
        check("dead link reported", "missing-page" in dead_targets)
        check("journal dead links still report (journal stays a source)",
              "missing-from-journal" in dead_targets)
        check("index.md dead link reported (the index must stay live)",
              "knowledge/log-only-DEAD-NO" in dead_targets)
        check("folder-name link skipped", "people" not in dead_targets)
        check("code-fenced / inline-backtick links skipped",
              not {"fenced-example", "inline-example"} & dead_targets)
        check("kb-root fallback resolves outputs link", "outputs/queries/report" not in dead_targets)
        check("traversal-shaped target never resolves outside the KB (reported dead)",
              "../../../outside-secret" in dead_targets)
        check("log.md never a dead-link source", "log.md" not in dead_sources)
        check("staging draft never a dead-link source",
              not any(s.startswith("staging/") for s in dead_sources))

        check("page count excludes staging/templates but includes structural",
              dev["pages"] == 18)  # 13 (hub leaf lonely indexed-only log-only ada acme linker citer index log README journal-daily) + twins x2, twin-linker, selfie, traverser

        # unmapped folder untouched
        check("unmapped folder skipped entirely",
              all(not o.startswith("99_") for o in orphans))

        # CLI --json shape
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "garden_audit.py"),
                            "--vault-root", vault, "--kb-map", json.dumps(KB_MAP), "--json"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("CLI exits 0", r.returncode == 0)
        try:
            j = json.loads(r.stdout)
            check("--json parses with kb keys + counts",
                  j["dev"]["pages"] == 18 and "orphans" in j["dev"] and "dead_links" in j["dev"])
        except Exception as e:
            check(f"--json parses ({e})", False)

        print(f"\n{PASS} passed, {FAIL} failed")
        sys.exit(1 if FAIL else 0)
    finally:
        shutil.rmtree(vault, ignore_errors=True)


if __name__ == "__main__":
    main()
