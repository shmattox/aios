# Task Resolution Layer — Slice 1 (FO economic lane) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic core of the per-task resolution layer and wire deep-resolve into the brief for the Family-Office economic lane, so the property-insurance task resolves from paper (cited) instead of being reported "verbal / silent."

**Architecture:** Three stdlib-only Python engine tools — `resolve_verdict.py` (the un-fakeable auto-promote gate), `resolve_fetch.py` (entity-crosswalk + cache reader), `resolve_sweep.py` (high-recall unresolved-task flagger) — plus a `links:` crosswalk on the Bayview entity page and a brief-skill wiring that runs deep-resolve (model assembles typed evidence → scripted gate) and renders the verdict into `system_voice`. The model orchestrates and comprehends; scripts own the auditable joints.

**Tech Stack:** Python 3 (stdlib only — no PyYAML, no third-party deps), pytest (`engine/tools/tests/`), the existing `queue_tx.py`/`brief_render.py`/gather conventions.

**Spec:** `docs/superpowers/specs/2026-07-07-task-resolution-layer-design.md`

## Global Constraints

- **Stdlib-only.** No third-party imports in any engine tool (matches every existing tool). Frontmatter is hand-parsed.
- **Fact-free.** Paths, model tiers, DB ids, economic keywords come from `profile/` (`connectors.yaml` / `domains.yaml`) — never hardcoded in a tool. Tools take them as args/JSON.
- **The verdict gate is deterministic and tested.** `resolve_verdict.py` is a pure boolean over structured inputs. A model may assemble evidence but NEVER sets `verdict=papered`/`auto_promote=true` by fiat.
- **Deference ladder (highest first): paper > operational > verbal.** Applied by the gate, not invented.
- **Strict clean rule for auto-promote:** exactly ONE executed paper row, qty-aligned, no contradicting aligned row, no untyped value-bearing candidate. Any wobble → `conflict`/`verbal-only`, `auto_promote=false`.
- **`qty` alignment precedes comparison.** Only rows whose `qty` equals the claim's `qty` are compared; different-`qty` rows are ignored, not treated as conflicts.
- **Writes route through `gate`.** Resolution reads and assembles; the self-heal link-add and any human-confirmed promotion are `gate` writes. Slice-1 tools never write the vault/Notion.
- **Tests pytest-collectable** under `engine/tools/tests/`, using the repo's `sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))` pattern.
- **Real ids.** The Bayview crosswalk uses real Drive file_ids / Trello card_url / Notion page_id supplied by Seth (Task 4 is `[GATE: human]` for the values).

**Evidence row schema (the shared contract every task uses):**
```
{ "source":  "drive" | "trello" | "notion" | "vault",
  "ref":     str,            # file_id / card_url / page_id / vault-relative path
  "says":    str | None,     # the figure/fact AS STATED, e.g. "$4,200/yr"
  "value":   float | None,   # normalized numeric (model-populated); None if non-numeric
  "qty":     str | None,     # semantic type: "annual-premium" | "monthly-premium" | "coverage-limit" | ...
  "tier":    "paper" | "operational" | "verbal",
  "executed": bool }         # only meaningful for tier=="paper"
```

**Verdict dict (what `compute_verdict` returns — later tasks rely on these exact keys):**
```
{ "verdict": "papered" | "conflict" | "verbal-only" | "silent",
  "canonical": str | None,   # cite string, set for papered/conflict-with-paper
  "conflict":  str | None,   # human-readable discrepancy, set for conflict
  "provenance": [str],       # source names, highest tier first
  "auto_promote": bool }     # True ONLY for a strict-clean papered verdict
```

---

### Task 1: `resolve_verdict.py` — the deterministic auto-promote gate

The load-bearing piece; gets the most tests. Pure function + a thin CLI.

**Files:**
- Create: `engine/tools/resolve_verdict.py`
- Test: `engine/tools/tests/test_resolve_verdict.py`

**Interfaces:**
- Produces: `compute_verdict(claim_qty: str, evidence: list[dict]) -> dict` (verdict dict above); `main(argv=None) -> int` CLI reading `{claim_qty, evidence}` JSON.

- [ ] **Step 1: Write the failing tests**

```python
# engine/tools/tests/test_resolve_verdict.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # .../engine/tools
import resolve_verdict as rv

PAPER = {"source": "drive", "ref": "F1", "says": "$4,200/yr", "value": 4200.0,
         "qty": "annual-premium", "tier": "paper", "executed": True}
TRELLO_SAME = {"source": "trello", "ref": "C1", "says": "$4,200", "value": 4200.0,
               "qty": "annual-premium", "tier": "verbal", "executed": False}
TRELLO_DIFF = {"source": "trello", "ref": "C1", "says": "$4,500", "value": 4500.0,
               "qty": "annual-premium", "tier": "verbal", "executed": False}
TRELLO_MONTHLY = {"source": "trello", "ref": "C1", "says": "$350/mo", "value": 350.0,
                  "qty": "monthly-premium", "tier": "verbal", "executed": False}
PAPER2_DIFF = {"source": "drive", "ref": "F2", "says": "$4,900/yr", "value": 4900.0,
               "qty": "annual-premium", "tier": "paper", "executed": True}
UNTYPED = {"source": "trello", "ref": "C9", "says": "$4,200", "value": 4200.0,
           "qty": None, "tier": "verbal", "executed": False}

def test_clean_match_papers_and_auto_promotes():
    out = rv.compute_verdict("annual-premium", [PAPER, TRELLO_SAME])
    assert out["verdict"] == "papered"
    assert out["auto_promote"] is True
    assert "drive:F1" in out["canonical"]

def test_doc_conflicts_with_card_no_promote():
    out = rv.compute_verdict("annual-premium", [PAPER, TRELLO_DIFF])
    assert out["verdict"] == "conflict"
    assert out["auto_promote"] is False
    assert "4,500" in out["conflict"] or "4500" in out["conflict"]

def test_two_candidate_docs_conflict():
    out = rv.compute_verdict("annual-premium", [PAPER, PAPER2_DIFF])
    assert out["verdict"] == "conflict"
    assert out["auto_promote"] is False

def test_qty_mismatch_is_not_a_conflict():
    # a monthly figure is a DIFFERENT quantity — it must NOT create a false conflict
    out = rv.compute_verdict("annual-premium", [PAPER, TRELLO_MONTHLY])
    assert out["verdict"] == "papered"
    assert out["auto_promote"] is True

def test_verbal_only_no_paper():
    out = rv.compute_verdict("annual-premium", [TRELLO_SAME])
    assert out["verdict"] == "verbal-only"
    assert out["auto_promote"] is False
    assert out["canonical"] is None

def test_silent_when_no_aligned_evidence():
    out = rv.compute_verdict("annual-premium", [])
    assert out["verdict"] == "silent"
    assert out["auto_promote"] is False

def test_untyped_candidate_blocks_auto_promote():
    # the model failed to type a value-bearing figure -> cannot confirm alignment -> never clean
    out = rv.compute_verdict("annual-premium", [PAPER, UNTYPED])
    assert out["verdict"] == "conflict"
    assert out["auto_promote"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest engine/tools/tests/test_resolve_verdict.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'resolve_verdict'`.

- [ ] **Step 3: Write the implementation**

```python
# engine/tools/resolve_verdict.py
#!/usr/bin/env python3
"""resolve_verdict.py — the DETERMINISTIC auto-promote gate of the task resolution layer.

The model assembles + semantically types evidence; it NEVER declares a figure "papered" by fiat.
It hands this tool a claim (the quantity under question) plus typed evidence rows, and THIS tool
applies the deference ladder + the strict clean rule to produce the verdict. Whether an economic
`verbal -> papered` auto-promotion fires is a deterministic boolean over structured inputs — so it
is auditable and cannot misfire on a model's whim. (Paper-Governs as code — see
docs/superpowers/specs/2026-07-07-task-resolution-layer-design.md.) Fact-free, stdlib-only.

Deference ladder (highest first): paper > operational > verbal.
"""
import argparse, json, sys

TIER_RANK = {"paper": 3, "operational": 2, "verbal": 1}
MONEY_EPS = 0.01


def _eq(a, b):
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) < MONEY_EPS


def compute_verdict(claim_qty, evidence):
    """(claim_qty, evidence[]) -> verdict dict (see the plan's shared contract)."""
    evidence = evidence or []

    # Ambiguity guard: any value-bearing candidate the model could not type blocks a clean verdict.
    untyped = [r for r in evidence if r.get("value") is not None and not r.get("qty")]

    # Aligned set: rows about the SAME quantity as the claim. Different-qty rows are NOT conflicts.
    aligned = [r for r in evidence if r.get("qty") == claim_qty]
    provenance = [r.get("source") for r in sorted(
        aligned, key=lambda r: TIER_RANK.get(r.get("tier"), 0), reverse=True)]

    if not aligned:
        return {"verdict": "silent", "canonical": None, "conflict": None,
                "provenance": provenance, "auto_promote": False}

    papers = [r for r in aligned if r.get("tier") == "paper" and r.get("executed")]
    paper_values = {round(float(r["value"]), 2) for r in papers if r.get("value") is not None}

    # Two candidate governing docs disagree -> conflict.
    if len(paper_values) > 1:
        return {"verdict": "conflict", "canonical": None,
                "conflict": "multiple executed docs disagree: %s" % sorted(paper_values),
                "provenance": provenance, "auto_promote": False}

    if papers:
        gov = papers[0]
        gv = gov.get("value")
        contradictions = [r for r in aligned
                          if r is not gov and r.get("value") is not None and not _eq(r.get("value"), gv)]
        canonical = "%s — cited to %s:%s" % (gov.get("says"), gov.get("source"), gov.get("ref"))
        if contradictions or untyped:
            why = []
            if contradictions:
                c = contradictions[0]
                why.append("%s says %s; paper says %s" % (c.get("source"), c.get("says"), gov.get("says")))
            if untyped:
                why.append("an untyped figure is present — alignment unconfirmed")
            return {"verdict": "conflict", "canonical": canonical, "conflict": "; ".join(why),
                    "provenance": provenance, "auto_promote": False}
        # STRICT CLEAN: exactly one executed paper, qty-aligned, nothing contradicts, nothing untyped.
        return {"verdict": "papered", "canonical": canonical, "conflict": None,
                "provenance": provenance, "auto_promote": True}

    # No paper in the aligned set -> operational/verbal only; never auto-promotes.
    return {"verdict": "verbal-only", "canonical": None, "conflict": None,
            "provenance": provenance, "auto_promote": False}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Deterministic resolve verdict gate")
    ap.add_argument("payload", help="path to JSON: {claim_qty, evidence:[...]}")
    args = ap.parse_args(argv)
    with open(args.payload, encoding="utf-8") as f:
        data = json.load(f)
    print(json.dumps(compute_verdict(data.get("claim_qty"), data.get("evidence")), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest engine/tools/tests/test_resolve_verdict.py -v`
Expected: PASS — 7 passed.

- [ ] **Step 5: Commit**

```bash
git add engine/tools/resolve_verdict.py engine/tools/tests/test_resolve_verdict.py
git commit -m "A31: resolve_verdict.py — deterministic auto-promote gate + tests"
```

---

### Task 2: `resolve_fetch.py` — entity crosswalk + cache reader

Deterministic: read an entity page's `links:` block into evidence *candidates* (source + ref + desc), and read any pre-scraped cache. It enumerates candidates; it does NOT decide which doc governs (that is the model's relevance call downstream) and does NOT extract figures (model work).

**Files:**
- Create: `engine/tools/resolve_fetch.py`
- Test: `engine/tools/tests/test_resolve_fetch.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `read_links_block(text: str) -> dict[str, list[str]]`; `candidates_for(entity_text: str) -> dict` returning `{"has_crosswalk": bool, "aliases": [str], "candidates": [{"source", "ref", "desc"}]}`.

- [ ] **Step 1: Write the failing tests**

```python
# engine/tools/tests/test_resolve_fetch.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import resolve_fetch as rf

ENTITY = """---
title: Bayview property
kb: familyoffice
links:
  drive:
    - "1AbCdeclarationID — 2024 insurance declaration"
    - "1XyzHUD — purchase HUD"
  trello:
    - "https://trello.com/c/abc — Bayview weekly OPS"
  notion:
    - "page-123 — Bayview (Assets & Liabilities row)"
  aliases: ["Bayview", "the Bayview deal"]
tags: [property, florida]
---

Body text about Bayview.
"""

NO_LINKS = """---
title: Some entity
kb: familyoffice
---
Body.
"""

def test_reads_block_lists_and_inline_lists():
    blk = rf.read_links_block(ENTITY)
    assert blk["drive"][0].startswith("1AbCdeclarationID")
    assert len(blk["drive"]) == 2
    assert blk["trello"] == ["https://trello.com/c/abc — Bayview weekly OPS"]
    assert blk["aliases"] == ["Bayview", "the Bayview deal"]

def test_candidates_split_ref_from_desc():
    out = rf.candidates_for(ENTITY)
    assert out["has_crosswalk"] is True
    drive = [c for c in out["candidates"] if c["source"] == "drive"]
    assert {"source": "drive", "ref": "1AbCdeclarationID", "desc": "2024 insurance declaration"} in drive
    assert "Bayview" in out["aliases"]

def test_no_links_block_signals_fallback():
    out = rf.candidates_for(NO_LINKS)
    assert out["has_crosswalk"] is False
    assert out["candidates"] == []
    assert out["aliases"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest engine/tools/tests/test_resolve_fetch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'resolve_fetch'`.

- [ ] **Step 3: Write the implementation**

```python
# engine/tools/resolve_fetch.py
#!/usr/bin/env python3
"""resolve_fetch.py — read an entity's `links:` crosswalk into evidence CANDIDATES.

The top-level frontmatter reader in the engine (capture_router.read_frontmatter) deliberately skips
nested blocks; the crosswalk IS nested (a mapping of lists), so this tool parses the `links:` block
directly. It enumerates candidates only — it does NOT pick the governing doc (model relevance) or
extract figures (model). Stdlib-only, fact-free.
"""
import argparse, json, sys

SEP = " — "   # em dash separating "ref — human description" in each crosswalk entry


def _unquote(s):
    s = s.strip()
    if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
        return s[1:-1]
    return s


def read_links_block(text):
    """Parse the entity's `links:` frontmatter sub-block into {subkey: [items]}.
    Handles block lists (indented `- item`) and inline lists (`key: [a, b]`). {} if absent."""
    if not text or not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out, in_links, cur = {}, False, None
    for line in text[3:end].splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_links = (stripped.endswith(":") and stripped[:-1].strip() == "links")
            cur = None
            continue
        if not in_links:
            continue
        if stripped.startswith("- "):
            if cur is not None:
                out.setdefault(cur, []).append(_unquote(stripped[2:]))
            continue
        if ":" in stripped:
            k, v = stripped.split(":", 1)
            k, v = k.strip(), v.strip()
            if v.startswith("[") and v.endswith("]"):
                out[k] = [_unquote(x) for x in v[1:-1].split(",") if x.strip()]
                cur = None
            elif v == "":
                out.setdefault(k, [])
                cur = k
            else:
                out[k] = [_unquote(v)]
                cur = None
    return out


def candidates_for(entity_text):
    """Entity page text -> {has_crosswalk, aliases[], candidates[{source,ref,desc}]}."""
    blk = read_links_block(entity_text)
    aliases = blk.pop("aliases", []) if blk else []
    candidates = []
    for source, items in (blk or {}).items():
        for item in items:
            ref, _, desc = item.partition(SEP)
            candidates.append({"source": source, "ref": ref.strip(), "desc": desc.strip()})
    return {"has_crosswalk": bool(candidates), "aliases": aliases, "candidates": candidates}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read an entity crosswalk into evidence candidates")
    ap.add_argument("entity_path", help="path to the entity .md page")
    args = ap.parse_args(argv)
    with open(args.entity_path, encoding="utf-8") as f:
        print(json.dumps(candidates_for(f.read()), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest engine/tools/tests/test_resolve_fetch.py -v`
Expected: PASS — 3 passed.

- [ ] **Step 5: Commit**

```bash
git add engine/tools/resolve_fetch.py engine/tools/tests/test_resolve_fetch.py
git commit -m "A31: resolve_fetch.py — entity crosswalk reader + tests"
```

---

### Task 3: `resolve_sweep.py` — high-recall unresolved-task flagger

Scans open tasks and flags the ones that likely need resolution — **high-recall on purpose** (a missed flag = never looked = the black box). Trigger = a money figure OR an economic keyword (profile-supplied) OR a subject with no crosswalk. Already-resolved ids are skipped. Pre-scrape wiring is deferred (Task 5 does live reads); this task delivers the deterministic flag.

**Files:**
- Create: `engine/tools/resolve_sweep.py`
- Test: `engine/tools/tests/test_resolve_sweep.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `flag_task(task: dict, economic_keywords: list[str], resolved_ids: set) -> dict | None` returning `{"id", "title", "reason"}` (`reason` ∈ `"figure" | "economic-keyword"`); `sweep(tasks, economic_keywords, resolved_ids) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

```python
# engine/tools/tests/test_resolve_sweep.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import resolve_sweep as rs

KW = ["insurance", "premium", "policy", "loan", "tax", "renew", "wire", "payoff"]

def test_flags_task_with_money_figure():
    t = {"id": "t1", "title": "Pay property insurance $4,200"}
    f = rs.flag_task(t, KW, set())
    assert f["reason"] == "figure"

def test_flags_economic_keyword_without_figure():
    # "Renew the policy" has no dollar amount but still needs paper -> must be flagged
    t = {"id": "t2", "title": "Renew the policy before it lapses"}
    f = rs.flag_task(t, KW, set())
    assert f["reason"] == "economic-keyword"

def test_does_not_flag_non_economic_task():
    t = {"id": "t3", "title": "Call mom about the weekend"}
    assert rs.flag_task(t, KW, set()) is None

def test_skips_already_resolved_task():
    t = {"id": "t1", "title": "Pay property insurance $4,200"}
    assert rs.flag_task(t, KW, {"t1"}) is None

def test_sweep_returns_only_flagged():
    tasks = [
        {"id": "t1", "title": "Pay property insurance $4,200"},
        {"id": "t3", "title": "Call mom about the weekend"},
        {"id": "t2", "title": "Renew the policy"},
    ]
    out = rs.sweep(tasks, KW, set())
    assert {f["id"] for f in out} == {"t1", "t2"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest engine/tools/tests/test_resolve_sweep.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'resolve_sweep'`.

- [ ] **Step 3: Write the implementation**

```python
# engine/tools/resolve_sweep.py
#!/usr/bin/env python3
"""resolve_sweep.py — flag open tasks that likely need cross-system resolution.

HIGH-RECALL by design: a missed flag means the task is acted on blind (the black box). Trigger =
a money figure OR an economic keyword (profile-supplied) OR (Task-5 wiring) a subject with no
crosswalk. Flag only — it does not resolve. Fact-free (keywords come from the profile), stdlib-only.
"""
import argparse, json, re, sys

MONEY_RE = re.compile(r"\$\s?\d")   # any explicit dollar figure


def flag_task(task, economic_keywords, resolved_ids):
    """task -> {id, title, reason} if it needs resolution, else None."""
    tid = task.get("id")
    if tid in (resolved_ids or set()):
        return None
    text = "%s %s" % (task.get("title", ""), task.get("body", ""))
    if MONEY_RE.search(text):
        return {"id": tid, "title": task.get("title"), "reason": "figure"}
    low = text.lower()
    if any(kw.lower() in low for kw in (economic_keywords or [])):
        return {"id": tid, "title": task.get("title"), "reason": "economic-keyword"}
    return None


def sweep(tasks, economic_keywords, resolved_ids):
    out = []
    for t in tasks or []:
        f = flag_task(t, economic_keywords, resolved_ids or set())
        if f:
            out.append(f)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Flag open tasks that need resolution")
    ap.add_argument("payload", help="JSON: {tasks:[...], economic_keywords:[...], resolved_ids:[...]}")
    args = ap.parse_args(argv)
    with open(args.payload, encoding="utf-8") as f:
        d = json.load(f)
    flagged = sweep(d.get("tasks"), d.get("economic_keywords"), set(d.get("resolved_ids") or []))
    print(json.dumps({"flagged": flagged}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest engine/tools/tests/test_resolve_sweep.py -v`
Expected: PASS — 5 passed.

- [ ] **Step 5: Commit**

```bash
git add engine/tools/resolve_sweep.py engine/tools/tests/test_resolve_sweep.py
git commit -m "A31: resolve_sweep.py — high-recall unresolved-task flagger + tests"
```

---

### Task 4: Bayview entity crosswalk (`links:`) `[GATE: human]`

Add the `links:` block to the real Bayview entity page so `resolve_fetch` has a crosswalk to follow. **The real ids must come from Seth** — the Drive declaration file_id, the Trello card_url, the Notion A&L page_id. Do not invent them; if unavailable, stop and ask.

**Files:**
- Modify: the Bayview entity page under `SecondBrain/02_FamilyOffice/wiki/entities/` (confirm exact filename first — `bayview-property.md` or the existing Bayview entity slug).

**Interfaces:**
- Consumes: `resolve_fetch.candidates_for` (Task 2) will parse this block.
- Produces: a parseable `links:` crosswalk for the insurance claim.

- [ ] **Step 1: Locate the real entity page**

Run: `ls SecondBrain/02_FamilyOffice/wiki/entities/ | grep -i bayview`
Expected: the Bayview entity filename. If none exists, STOP — the crosswalk needs a home; ask Seth whether to create the entity page or point at a different slug.

- [ ] **Step 2: Get the real ids from Seth**

Ask Seth for: (a) the Drive file_id of the executed insurance declaration, (b) the Bayview Trello card_url, (c) the Notion Assets & Liabilities page_id (row) for Bayview, (d) any aliases the insurance task might use. Do not proceed with placeholders.

- [ ] **Step 3: Add the `links:` block to the entity frontmatter**

Insert into the page's frontmatter (real ids substituted for the angle-bracket placeholders):

```yaml
links:
  drive:
    - "<real-drive-file_id> — 2024 insurance declaration"
  trello:
    - "<real-trello-card_url> — Bayview weekly OPS"
  notion:
    - "<real-notion-page_id> — Bayview (Assets & Liabilities row)"
  aliases: ["Bayview", "the Bayview deal", "property insurance"]
```

- [ ] **Step 4: Verify the crosswalk parses**

Run: `python engine/tools/resolve_fetch.py SecondBrain/02_FamilyOffice/wiki/entities/<bayview-file>.md`
Expected: JSON with `has_crosswalk: true`, the drive/trello/notion candidates (ref split from desc), and the aliases.

- [ ] **Step 5: Commit (env repo, native — vault change)**

> Note: the vault is the `secondbrain` repo, not `aios`. Commit there. This is a data edit, not engine code.
```bash
git -C SecondBrain add 02_FamilyOffice/wiki/entities/<bayview-file>.md
git -C SecondBrain commit -m "A31: Bayview entity links crosswalk (resolution layer slice 1)"
```

---

### Task 5: Wire deep-resolve into the brief (FO economic lane) + end-to-end proof

Two deliverables: (a) an e2e test chaining the three tools on a two-task fixture (deterministic spine proof), and (b) the brief-skill wiring + the live acceptance demo. The model legs (subject-match, governing-doc selection, figure extraction + `qty` typing) happen in the brief thread; the scripted gate decides the verdict; the verdict feeds `system_voice`.

**Files:**
- Create: `engine/tools/tests/test_resolve_e2e.py`
- Modify: `skills/brief/references/gather.md` (add the resolve step to the FO gather) and `skills/brief/SKILL.md` (the `system_voice` grade reads the dossier verdict).
- Modify: `profile/domains.yaml` (add `resolve:` block — economic keywords + model tiers, fact-free).

**Interfaces:**
- Consumes: `resolve_sweep.sweep` (Task 3), `resolve_fetch.candidates_for` (Task 2), `resolve_verdict.compute_verdict` (Task 1).
- Produces: a dossier per flagged task that the brief renders; the `system_voice` grade mapping (papered→Grade 1 cited; conflict→Grade 1 flagged; silent→Grade 0 only after search).

- [ ] **Step 1: Add the `resolve:` profile block (fact-free config)**

Append to `profile/domains.yaml`:

```yaml
# resolve — the per-task cross-system resolution layer (A31). Fact-free levers the resolve tools read.
resolve:
  economic_keywords: [insurance, premium, policy, loan, interest, tax, wire, payoff, guaranty,
                      escrow, lien, note, rent, mortgage, renew]
  leaf_model: "claude-haiku-4-5-20251001"   # cheap per-source extractor leaves (read a figure off a page)
  deep_model: "claude-opus-4-8"             # the per-task orchestrator (align, select governing doc, decide)
  cache_dir: "state/resolve-cache"          # env_root-relative; pre-scraped evidence + dossiers
```

- [ ] **Step 2: Write the failing e2e test**

```python
# engine/tools/tests/test_resolve_e2e.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import resolve_sweep as rs, resolve_verdict as rv

KW = ["insurance", "premium", "policy", "renew"]

# Simulates the spine: sweep flags -> (model assembles typed evidence, stubbed here) -> verdict.
# Two tasks prove the fan-out set resolves independently.
EVIDENCE_BY_TASK = {
    "t1": ("annual-premium", [
        {"source": "drive", "ref": "F1", "says": "$4,200/yr", "value": 4200.0,
         "qty": "annual-premium", "tier": "paper", "executed": True},
        {"source": "trello", "ref": "C1", "says": "$4,200", "value": 4200.0,
         "qty": "annual-premium", "tier": "verbal", "executed": False}]),
    "t2": ("annual-premium", [
        {"source": "drive", "ref": "F9", "says": "$8,000/yr", "value": 8000.0,
         "qty": "annual-premium", "tier": "paper", "executed": True},
        {"source": "trello", "ref": "C9", "says": "$9,500", "value": 9500.0,
         "qty": "annual-premium", "tier": "verbal", "executed": False}]),
}

def test_spine_flags_then_verdicts_two_tasks():
    tasks = [
        {"id": "t1", "title": "Pay property insurance $4,200"},
        {"id": "t2", "title": "Confirm Bayview insurance premium"},
        {"id": "t3", "title": "Call mom"},
    ]
    flagged = rs.sweep(tasks, KW, set())
    assert {f["id"] for f in flagged} == {"t1", "t2"}

    dossiers = {}
    for f in flagged:                       # each task resolves independently (fan-out unit)
        claim_qty, evidence = EVIDENCE_BY_TASK[f["id"]]
        dossiers[f["id"]] = rv.compute_verdict(claim_qty, evidence)

    assert dossiers["t1"]["verdict"] == "papered"      # clean -> cite the declaration
    assert dossiers["t1"]["auto_promote"] is True
    assert dossiers["t2"]["verdict"] == "conflict"     # doc != card -> hold, wait
    assert dossiers["t2"]["auto_promote"] is False
```

- [ ] **Step 3: Run the e2e test to verify it fails, then passes**

Run: `python -m pytest engine/tools/tests/test_resolve_e2e.py -v`
Expected: FAIL first only if Tasks 1/3 are absent; with them present it PASSES (2 tasks, both verdicts). It exercises sweep → per-task verdict on a two-item set — the deterministic half of the fan-out.

- [ ] **Step 4: Wire the resolve step into the FO gather**

In `skills/brief/references/gather.md`, under the Family-Office gather, add a resolve step (prose procedure — the brief is a skill, not a script). Insert after the node-3 (Drive) bullet:

```markdown
4. **Resolve — economic FO tasks (A31).** Run `resolve_sweep.py` over the gathered FO open tasks
   (`resolve.economic_keywords` from `profile/domains.yaml`). For each flagged task, in parallel
   (one sub-agent per task — dispatching-parallel-agents; deep_model): (a) match its subject to a
   `familyoffice` entity (alias/semantic); (b) `resolve_fetch.py <entity>` for candidates; (c) for
   each relevant candidate, read the source and extract the figure, assigning `value` + `qty`
   (leaf_model) — SELECT the governing doc for the claim's quantity, do not dump every link; (d)
   assemble the typed `evidence[]` and run `resolve_verdict.py` — NEVER set the verdict yourself.
   Write the dossier to `resolve.cache_dir` keyed by task id + content hash. If the entity has no
   crosswalk or the fetch is empty, fall back to semantic search across the sources and, on a hit,
   propose adding the link back to the entity page **via `gate`** (self-heal; the brief never writes).
```

- [ ] **Step 5: Map the dossier verdict into `system_voice`**

In `skills/brief/SKILL.md`, in the graded-voice section, add that for a resolved FO economic item the `system_voice` is taken from its dossier:

```markdown
- **Resolved economic items (A31):** the `system_voice` grade comes from the dossier verdict, not a
  vault-page scan: `papered` → **Grade 1**, text = dossier `canonical`, cite = the Drive file_id;
  `conflict` → **Grade 1, flagged** ("$X per paper, but {source} says $Y — reconcile", from
  `dossier.conflict`); `verbal-only` → **Grade 2b** (verbal, unpapered — never Grade 1);
  `silent` → **Grade 0**, emitted ONLY after the resolve step's search genuinely found nothing.
  `auto_promote:true` items may fix-then-tell (record the papered cite); every other verdict waits.
```

- [ ] **Step 6: Run the full engine suite (no regressions)**

Run: `python -m pytest engine/tools/tests/ -q`
Expected: PASS — all prior tests plus the four new files green.

- [ ] **Step 7: Live acceptance demo (the human gate)**

Fire the FO brief (`Wake up. Daddy's home.` inside `Projects/family-office`, or scoped) and show in chat:
1. The property-insurance task returns **Grade 1 "your system says $X — cited to the 2024 declaration (Drive:<file_id>)"**, resolved from the crosswalk (`qty`-aligned).
2. Seed a conflicting number on the Bayview Trello card → re-fire → the same task returns **conflict**, refuses to auto-promote, and waits.
3. With two FO economic tasks flagged, both resolve (dossiers written to `resolve.cache_dir`) — concurrently via the parallel dispatch.

- [ ] **Step 8: Commit**

```bash
git add engine/tools/tests/test_resolve_e2e.py skills/brief/references/gather.md skills/brief/SKILL.md profile/domains.yaml
git commit -m "A31: wire deep-resolve into the FO brief gather + verdict-driven system_voice + e2e"
```

---

## Self-Review

**Spec coverage:**
- Sweep (flag high-recall + pre-scrape) → Task 3 (flag; pre-scrape wiring deferred per slice, noted).
- Entity crosswalk `links:` + fetch → Tasks 2 + 4.
- Dossier + verdict + deference ladder → Task 1.
- Determinism split (model assembles, scripted gate decides) → Task 1 (gate) + Task 5 step 4 ("NEVER set the verdict yourself").
- Strict clean rule (one executed paper, qty-aligned, no contradiction, no untyped) → Task 1 impl + tests (clean / doc≠card / two-doc / qty-mismatch / untyped).
- `qty` alignment before comparison → Task 1 `test_qty_mismatch_is_not_a_conflict`.
- Kills false "silent" (search-then-empty) → Task 5 step 5 (`silent` only after the resolve step searched).
- Fan-out across tasks → Task 5 step 4 (parallel sub-agent per flagged task) + e2e two-task proof.
- Self-heal via gate → Task 5 step 4 fallback clause.
- Model tiers as profile fields → Task 5 step 1 (`leaf_model`/`deep_model`).
- Writes route through gate → Global Constraints + Task 4 (vault repo) + Task 5 self-heal.
- Deferred (sync-trello, all-domain header flag, scaled self-heal, native sweep registration) → out of slice, recorded in spec/backlog. No task, by design.

**Placeholder scan:** No TBD/TODO. Task 4 uses `<real-…>` angle-bracket placeholders *intentionally* — it is `[GATE: human]` and its steps require Seth's real ids before proceeding (not a code placeholder).

**Type consistency:** `compute_verdict(claim_qty, evidence)` returns the verdict dict used identically in Task 1 tests and Task 5 e2e/wiring. `flag_task`/`sweep` signatures match across Task 3 and Task 5. `candidates_for` returns `{has_crosswalk, aliases, candidates[{source,ref,desc}]}` consumed by Task 4 verify and Task 5 wiring. Evidence-row keys (`source/ref/says/value/qty/tier/executed`) are identical everywhere.
