#!/usr/bin/env python3
r"""A3/garden — mechanical hygiene finding-set (the rulebook harvest's deterministic tier).

The Benos os-optimizer harvest (design: docs/superpowers/specs/2026-07-01-a3-garden-rulebook-
harvest-design.md) splits garden findings into MECHANICAL (single correct output, reversible —
the auto-ship tier on cleared KBs) and SEMANTIC (judgment — always review-laned). This tests the
mechanical tier's oracle, `garden_hygiene.py`, as a logic mirror over a fixture vault, plus the
B4 lane mapping through the UNCHANGED lane_policy (proving the tier needs zero moat edits).

Finding semantics under test (each deliberate):
  dup_h1        = first content line is `# Title` whose slug equals the filename stem (Obsidian
                  shows the filename; the H1 is redundant). Structural files exempt.
                  strip_dup_h1() is the deterministic fix body.
  frontmatter   = page lacks a leading `---` block ("frontmatter") or the block lacks a `type:`
                  key ("type") — the kb-schema contract's floor. journal/ exempt (episodic
                  dailies), structural files exempt, staging/.templates never walked.
  index_missing = content page not wikilink-reachable from wiki/index.md ("the index never goes
                  stale"). journal/ + sources/ exempt (episodic / transient distill inbox);
                  structural files exempt. has_index=False when index.md is absent (the finding
                  is then "create the index", not a per-page list).
  repoints      = a DEAD wikilink (per garden_audit semantics) whose target stem sits within
                  Levenshtein distance 2 of exactly ONE page stem, strictly closer than every
                  other — the typo class, the only dead link with a single correct output.
                  Anything ambiguous stays semantic (Connect judgment), NOT reported here.

Lane mapping under test (B4, via lane_policy AS-IS):
  mechanical (lane auto-ship) ships on a cleared dev KB, HOLDS on familyoffice (kb backstop);
  semantic (lane review) always holds; the economic tripwire still floors a mechanical dev item
  whose text smells economic (scheduled/unattended path).

Hermetic: everything under tempfile.mkdtemp(). Run: python engine/tools/tests/test_garden_hygiene.py
"""
import os, shutil, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)
import garden_hygiene
import lane_policy

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


FM = "---\ntitle: x\ntype: knowledge\n---\n"


def main():
    vault = tempfile.mkdtemp(prefix="hygiene-vault-")
    try:
        KB_MAP = {"dev": "03_Dev", "personal": "01_Personal"}
        B = "03_Dev/wiki/"
        # clean page: frontmatter+type, H1 differs from stem, indexed -> zero findings
        w(vault, B + "knowledge/clean.md", FM + "# Clean Insights\nbody\n")
        # dup-H1: slug of the H1 == filename stem
        w(vault, B + "knowledge/dup-h1.md", FM + "# Dup H1\n\nbody stays\n")
        # frontmatter gaps
        w(vault, B + "people/no-fm.md", "# A Person\nno frontmatter at all\n")
        w(vault, B + "companies/no-type.md", "---\ntitle: co\n---\nbody\n")
        # unindexed content page (also an orphan; that is the audit's report, not this one's)
        w(vault, B + "knowledge/unindexed.md", FM + "body\n")
        # typo'd dead link -> unique nearest stem within distance 2 = mechanical repoint;
        # plus a hopeless dead link -> semantic, absent from repoints
        w(vault, B + "knowledge/typo-linker.md",
          FM + "see [[knowledge/claen]] and [[totally-gone-xyz]] and [[dog]]\n")
        # exemptions: journal (episodic), sources (transient inbox), staging (never walked)
        w(vault, B + "journal/2026-01-01.md", "daily note, no frontmatter, unindexed\n")
        w(vault, B + "sources/stub.md", "---\ntitle: s\ntype: source\n---\nstub\n")
        w(vault, B + "staging/draft.md", "# draft\n")
        # structural files: never findings; index links define indexed-ness
        w(vault, B + "index.md", "- [[knowledge/clean]]\n- [[knowledge/dup-h1]]\n"
                                 "- [[people/no-fm]]\n- [[companies/no-type]]\n"
                                 "- [[knowledge/typo-linker]]\n")
        w(vault, B + "log.md", "- touched [[knowledge/unindexed]]\n")   # log is not the index
        w(vault, B + "README.md", "docs\n")
        # second KB with NO index.md
        w(vault, "01_Personal/wiki/knowledge/solo.md", FM + "body\n")

        res = garden_hygiene.hygiene(vault, KB_MAP)
        dev, per = res["dev"], res["personal"]

        # ── dup-H1 ──
        dup_pages = {f["page"] for f in dev["dup_h1"]}
        check("dup-H1 flags the slug-equal page", dup_pages == {"knowledge/dup-h1.md"})
        fixed, stripped = garden_hygiene.strip_dup_h1(FM + "# Dup H1\n\nbody stays\n", "dup-h1")
        check("strip_dup_h1 removes H1 + blank, keeps body+frontmatter",
              stripped and fixed == FM + "body stays\n")
        same, untouched = garden_hygiene.strip_dup_h1(FM + "# Clean Insights\nbody\n", "clean")
        check("strip_dup_h1 no-ops on a non-duplicate H1",
              not untouched and same == FM + "# Clean Insights\nbody\n")

        # ── frontmatter ──
        fm = {f["page"]: f["missing"] for f in dev["frontmatter"]}
        check("no-frontmatter page reported", fm.get("people/no-fm.md") == ["frontmatter"])
        check("typeless frontmatter reported", fm.get("companies/no-type.md") == ["type"])
        check("journal + structural + staging exempt from frontmatter",
              not any(p.startswith(("journal/", "staging/")) or
                      os.path.basename(p).lower() in ("index.md", "log.md", "readme.md")
                      for p in fm))
        check("typed source stub is clean", "sources/stub.md" not in fm)

        # ── index freshness ──
        check("dev KB has an index", dev["has_index"] is True)
        check("unindexed content page reported (log.md link does not count)",
              dev["index_missing"] == ["knowledge/unindexed.md"])
        check("journal + sources exempt from index_missing",
              not any(p.startswith(("journal/", "sources/")) for p in dev["index_missing"]))
        check("index line render is the deterministic fix",
              garden_hygiene.index_line("knowledge/unindexed.md")
              == "- [[knowledge/unindexed]]")
        check("KB without index.md: has_index False, no per-page list",
              per["has_index"] is False and per["index_missing"] == [])

        # ── mechanical repoints ──
        rp = {(r["src"], r["target"]): r["repoint_to"] for r in dev["repoints"]}
        check("typo'd dead link repoints to the unique near stem",
              rp.get(("knowledge/typo-linker.md", "knowledge/claen")) == "knowledge/clean.md")
        check("hopeless dead link stays semantic (absent from repoints)",
              not any(t == "totally-gone-xyz" for _, t in rp))
        check("typo near a STRUCTURAL stem never repoints ([[dog]] !-> log.md)",
              not any(t == "dog" for _, t in rp))

        # ── B4 lane mapping through lane_policy AS-IS (no moat edits) ──
        CLEARED = frozenset({"dev", "personal"})
        mech_dev = {"kb": "dev", "lane": "auto-ship", "conflict_key": "dev/wiki/knowledge/x.md",
                    "title": "hygiene: add missing type frontmatter", "source": "garden"}
        mech_fo = dict(mech_dev, kb="familyoffice",
                       conflict_key="familyoffice/wiki/companies/y.md")
        sem_dev = dict(mech_dev, lane="review")
        econ_dev = dict(mech_dev, title="hygiene fix on the cap table page")
        check("mechanical auto-ship SHIPS on cleared dev KB",
              lane_policy.ship_action(mech_dev, auto_ship_kbs=CLEARED) == "ship")
        check("mechanical auto-ship HOLDS on familyoffice (kb backstop)",
              lane_policy.ship_action(mech_fo, auto_ship_kbs=CLEARED) == "hold")
        check("semantic review-lane always HOLDS",
              lane_policy.ship_action(sem_dev, auto_ship_kbs=CLEARED) == "hold")
        check("economic-smelling mechanical item floored by the tripwire (unattended path)",
              lane_policy.scheduled_ship_action(econ_dev, auto_ship_kbs=CLEARED) == "hold")
        check("ballot consistency: auto-ship pairs with approve",
              lane_policy.ballot_consistent("auto-ship", "approve")
              and not lane_policy.ballot_consistent("auto-ship", "hold"))

        # ── kb_map fence ──
        w(vault, "99_Unmapped/wiki/knowledge/never.md", "# never\n")
        res2 = garden_hygiene.hygiene(vault, KB_MAP)
        check("unmapped folder is never scanned", "99_Unmapped" not in str(res2))
    finally:
        shutil.rmtree(vault, ignore_errors=True)

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
