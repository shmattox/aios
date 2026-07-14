# A56 — Ingest/Distill Depth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deepen the AIOS ingest→distill path so a captured *operational concept* is integrated into the KB's `knowledge/` layer (Karpathy fan-out synthesis) instead of aging into a shallow `sources/` stub that later reads as retire-noise.

**Architecture:** One classification signal — `distill_class: concept | reference` — decided at ingest, carried on the stub frontmatter, threads three depth legs on the *preserved* capture→sort→ingest→sources→distill→gate funnel: deeper concept stubs (ingest), enhanced fan-out synthesis (garden distill), value-weighted throughput (a batch selector replacing "1/night"), plus a retire-fence so a concept stub can't be retired as noise un-distilled. The deterministic parts live in `garden_distill.py` (extended in place); the judgment parts are SKILL prose in `skills/ingest` + `skills/garden`.

**Tech Stack:** Python 3 **stdlib only** (matches every engine tool — no pyyaml, no third-party); markdown + YAML-frontmatter SKILLs; `queue_tx` single-file queue; pytest (tests also run standalone via each file's `__main__`).

## Global Constraints

- **Stdlib only** in `engine/tools/` — no third-party imports (portability to a stranger's Python is a shipped guarantee). Read frontmatter via `from frontmatter import read_frontmatter`, never `python-frontmatter`.
- **Funnel preserved (spec option A):** concept captures still land a `type: source` stub in `wiki/sources/`; NO direct-to-`knowledge/` ingest.
- **Never a hard delete:** retire MOVES a husk to `raw/archive/`, only after the knowledge shipped (existing `garden_distill` invariant — do not weaken).
- **Gate untouched:** every wiki edit/merge is a `lane: review` proposal; no auto-applied wiki write; FamilyOffice no-elevation (carry `source_tier`/`confidence`, no unpapered promotion) holds through synthesis.
- **Backward-compatible classification:** a stub with **no** `distill_class` classifies as `reference` — legacy pre-A56 stubs are never falsely fenced.
- **Binary classification (MVP):** `concept | reference` only; `entity-fact` → `reference`.
- **Tests are stdlib-only + dual-runnable:** add to `engine/tools/tests/test_garden_distill.py`, matching its `_write`/`tempfile`/`shutil.rmtree` pattern and `__main__` runner.
- **Go/no-go gate:** the live nightly wiring (Task 8) ships ONLY after the Task 7 backfill run over the 217 H39 stubs is reviewed in the gate and judged worth keeping **[GATE: human]**.

---

### Task 1: The spine — `distill_class` contract + `stub_class()` helper

**Files:**
- Modify: `engine/kb-schema/README.md` (frontmatter contract block, ~line 27-41)
- Modify: `engine/tools/garden_distill.py` (add helper after `enumerate_stubs`)
- Test: `engine/tools/tests/test_garden_distill.py`

**Interfaces:**
- Consumes: `frontmatter.read_frontmatter` (already imported as `_frontmatter`).
- Produces: `garden_distill.stub_class(fm: dict) -> str` returning `"concept"` or `"reference"`. Used by Tasks 3, 4, 6, 7.

- [ ] **Step 1: Write the failing test**

Add to `engine/tools/tests/test_garden_distill.py`:

```python
def test_stub_class_reads_concept_and_defaults_reference():
    assert gd.stub_class({"distill_class": "concept"}) == "concept"
    assert gd.stub_class({"distill_class": "Concept"}) == "concept"   # case-insensitive
    assert gd.stub_class({"distill_class": "reference"}) == "reference"
    assert gd.stub_class({}) == "reference"                          # absent -> reference (legacy-safe)
    assert gd.stub_class({"distill_class": "garbage"}) == "reference"  # unknown -> reference
    assert gd.stub_class({"distill_class": None}) == "reference"      # None guard
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest engine/tools/tests/test_garden_distill.py::test_stub_class_reads_concept_and_defaults_reference -v`
Expected: FAIL with `AttributeError: module 'garden_distill' has no attribute 'stub_class'`

- [ ] **Step 3: Write minimal implementation**

In `engine/tools/garden_distill.py`, add directly after `enumerate_stubs` (before `_stem_token_match`):

```python
def stub_class(fm):
    """A56 spine — the distill class of a stub:
      'concept'   — a transferable operational idea; gets deep ingest, enhanced (fan-out) synthesis
                    distill, priority throughput, and the noise-retire fence.
      'reference' — a link / artifact / entity-fact; keeps the shallow stub + cheap fold-or-retire.
    Absent / None / unknown -> 'reference' so LEGACY stubs (pre-A56, no distill_class) are
    backward-compatible and never falsely fenced. Case-insensitive."""
    v = (fm.get("distill_class") or "").strip().lower()
    return "concept" if v == "concept" else "reference"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest engine/tools/tests/test_garden_distill.py::test_stub_class_reads_concept_and_defaults_reference -v`
Expected: PASS

- [ ] **Step 5: Update the kb-schema frontmatter contract**

In `engine/kb-schema/README.md`, inside the ```` ```yaml ```` frontmatter block, add after the `raw_path:` lines and before `legal_status:`:

```yaml
distill_class: concept | reference   # A56, source-type: concept = transferable operational idea
                                      # (deep stub + fan-out synthesis distill); reference = pointer/
                                      # artifact (shallow stub, cheap path). Absent -> reference.
```

- [ ] **Step 6: Commit**

```bash
git add engine/tools/garden_distill.py engine/tools/tests/test_garden_distill.py engine/kb-schema/README.md
git commit -m "A56 Task 1: distill_class spine — stub_class() + kb-schema contract"
```

---

### Task 2: Ingest depth — classification judgment + deep concept-stub template

**Files:**
- Modify: `skills/ingest/SKILL.md` (step 1 "Draft", lines ~18-33)

**Interfaces:**
- Consumes: nothing new (model-judgment prose).
- Produces: the convention that every drafted `type: source` stub carries `distill_class:`, and a `concept` stub carries the deep-capture sections. Task 3/4/6/7 rely on `distill_class` being present on new stubs.

> **Note:** SKILL.md is model-judgment prose, not executable code — its "test" is a structural grep + a re-read, not pytest. This task adds no Python.

- [ ] **Step 1: Add the classification + depth instruction to step 1**

In `skills/ingest/SKILL.md`, in step 1 ("Draft."), immediately after the sentence ending `...don't copy the raw wholesale.` (line ~24), insert:

```markdown

   **A56 — classify + draft to depth (source-type items).** Before drafting a `type: source` stub,
   judge its **`distill_class`** and set it in the stub frontmatter (and mirror it into the queue
   item's `rec_reason`):
   - **`reference`** — a link / artifact / bookmark / entity-fact whose value is the *pointer*.
     Draft today's thin stub (one-line Summary + short Narrative + Open threads). Unchanged.
   - **`concept`** — a *transferable operational idea* ("how to operate off this") whose value is the
     *concept*. Draft a **deep** stub — still `type: source`, still under `wiki/sources/` (funnel
     intact) — carrying these H2 sections so the idea survives to synthesis:
     - `## Core idea` — the transferable concept IN FULL (not a one-liner).
     - `## How to apply` — how one would operate off this in this vault's context.
     - `## Proposed target` — the `knowledge/` page this belongs in (an existing slug, else a
       proposed new `knowledge/<slug>`), plus candidate neighbour pages to touch (the fan-out seed).
     - `## Open threads` — what's unresolved.
   When unsure, prefer `reference` (a mis-called concept wastes synthesis budget; the garden's F2.8
   metric will surface systematic under-classing). Binary only — an entity-fact is `reference`.
```

- [ ] **Step 2: Verify the section landed**

Run: `grep -n "A56 — classify" skills/ingest/SKILL.md && grep -n "Core idea" skills/ingest/SKILL.md`
Expected: both grep hits print (the instruction + the template heading are present).

- [ ] **Step 3: Re-read for consistency**

Re-read `skills/ingest/SKILL.md` step 1 top-to-bottom. Confirm: (a) the `distill_class` set happens at draft time; (b) the `session-record` special-case below it is unaffected (it drafts a journal note, not a source stub — no `distill_class`); (c) the deep sections are H2 (`## `) so Task 6's `extract_checklist` picks them up.

- [ ] **Step 4: Commit**

```bash
git add skills/ingest/SKILL.md
git commit -m "A56 Task 2: ingest classifies distill_class + drafts deep concept stubs"
```

---

### Task 3: Value-weighted throughput — `select_distill_batch()`

**Files:**
- Modify: `engine/tools/garden_distill.py` (add after `stub_class`)
- Test: `engine/tools/tests/test_garden_distill.py`

**Interfaces:**
- Consumes: `stub_class(fm)` (Task 1); stub dicts shaped `{"slug","path","fm"}` from `enumerate_stubs`.
- Produces: `select_distill_batch(stubs: list, cap_k: int) -> tuple[list, list, list]` = `(concept_batch, concept_overflow, reference_stubs)`. Used by the garden SKILL step 4 (Task 5).

- [ ] **Step 1: Write the failing test**

Add to `test_garden_distill.py`:

```python
def _cstub(slug, cls, when="2026-07-01"):
    return {"slug": slug, "path": f"p/{slug}.md",
            "fm": {"distill_class": cls, "last_reconciled": when}}

def test_select_distill_batch_caps_concepts_and_separates_references():
    stubs = [_cstub("c1", "concept", "2026-07-01"),
             _cstub("c2", "concept", "2026-07-03"),
             _cstub("c3", "concept", "2026-07-02"),
             _cstub("r1", "reference"), _cstub("r2", "reference")]
    batch, overflow, refs = gd.select_distill_batch(stubs, cap_k=2)
    assert [s["slug"] for s in batch] == ["c1", "c3"]      # oldest-first by last_reconciled
    assert [s["slug"] for s in overflow] == ["c2"]         # remaining concepts carry forward
    assert sorted(s["slug"] for s in refs) == ["r1", "r2"] # references never compete for the budget

def test_select_distill_batch_cap_zero_defers_all_concepts():
    stubs = [_cstub("c1", "concept"), _cstub("r1", "reference")]
    batch, overflow, refs = gd.select_distill_batch(stubs, cap_k=0)
    assert batch == []
    assert [s["slug"] for s in overflow] == ["c1"]
    assert [s["slug"] for s in refs] == ["r1"]

def test_select_distill_batch_missing_date_sorts_oldest():
    stubs = [_cstub("has_date", "concept", "2026-07-05"),
             {"slug": "no_date", "path": "p", "fm": {"distill_class": "concept"}}]
    batch, overflow, refs = gd.select_distill_batch(stubs, cap_k=1)
    assert [s["slug"] for s in batch] == ["no_date"]   # empty last_reconciled sorts first (most overdue)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest engine/tools/tests/test_garden_distill.py -k select_distill_batch -v`
Expected: FAIL with `AttributeError: ... has no attribute 'select_distill_batch'`

- [ ] **Step 3: Write minimal implementation**

In `garden_distill.py`, add after `stub_class`:

```python
def select_distill_batch(stubs, cap_k):
    """A56 leg 3 — value-weighted nightly distill selection, replacing the '1 stub/night' MVP cap.
    Returns (concept_batch, concept_overflow, reference_stubs):
      concept_batch    — up to cap_k concept-class stubs, oldest-first, for SYNTHESIS-mode distill
      concept_overflow — remaining concept stubs, carried to the next night (never starved by refs)
      reference_stubs  — reference-class stubs for the cheap fold-or-retire path (no synthesis budget)
    Oldest-first = ascending (last_reconciled, slug); an empty last_reconciled sorts first (treated
    as most-overdue). cap_k <= 0 -> no concept distills this run (all concepts overflow)."""
    concept, reference = [], []
    for s in stubs:
        (concept if stub_class(s["fm"]) == "concept" else reference).append(s)
    concept.sort(key=lambda s: (s["fm"].get("last_reconciled") or "", s["slug"]))
    k = max(0, cap_k)
    return concept[:k], concept[k:], reference
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest engine/tools/tests/test_garden_distill.py -k select_distill_batch -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add engine/tools/garden_distill.py engine/tools/tests/test_garden_distill.py
git commit -m "A56 Task 3: select_distill_batch — value-weighted throughput"
```

---

### Task 4: The funnel fence — `assert_noise_retire_allowed()`

**Files:**
- Modify: `engine/tools/garden_distill.py` (add after `select_distill_batch`)
- Test: `engine/tools/tests/test_garden_distill.py`

**Interfaces:**
- Consumes: `stub_class(fm)` (Task 1).
- Produces: `assert_noise_retire_allowed(stub_fm: dict) -> None` (raises `RuntimeError` on a fenced concept stub). Called by the garden de-bloat/prune steps (Task 5) before proposing a stub for noise-retirement.

- [ ] **Step 1: Write the failing test**

Add to `test_garden_distill.py`:

```python
def test_noise_retire_fence_blocks_undistilled_concept():
    try:
        gd.assert_noise_retire_allowed({"distill_class": "concept"})
        assert False, "expected RuntimeError — a concept stub cannot noise-retire un-attempted"
    except RuntimeError as e:
        assert "concept" in str(e).lower()

def test_noise_retire_fence_allows_attempted_concept():
    gd.assert_noise_retire_allowed(
        {"distill_class": "concept", "distill_attempted": "no-durable-concept"})  # no raise

def test_noise_retire_fence_allows_reference_and_legacy():
    gd.assert_noise_retire_allowed({"distill_class": "reference"})  # reference passes
    gd.assert_noise_retire_allowed({})                              # legacy (no class) passes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest engine/tools/tests/test_garden_distill.py -k noise_retire_fence -v`
Expected: FAIL with `AttributeError: ... has no attribute 'assert_noise_retire_allowed'`

- [ ] **Step 3: Write minimal implementation**

In `garden_distill.py`, add after `select_distill_batch`:

```python
def assert_noise_retire_allowed(stub_fm):
    """A56 funnel fence — a `distill_class: concept` stub MUST NOT be retired as noise (de-bloat /
    prune / bulk drain) without an enhanced-distill attempt first. The attempt is recorded either by
    a shipped distill (the normal path uses retire(), which already requires the knowledge target)
    or by an explicit `distill_attempted: no-durable-concept` note the synthesis pass writes when it
    finds nothing durable. Raises RuntimeError if a concept stub lacks that marker. Reference and
    legacy (no distill_class -> reference) stubs pass freely — the fence is opt-in via classification.
    This is distinct from retire(): retire() is the DISTILL retire (knowledge shipped); this guards
    the noise-retire paths that would otherwise bypass synthesis (the H39 leak)."""
    if stub_class(stub_fm) != "concept":
        return
    if (stub_fm.get("distill_attempted") or "").strip().lower() == "no-durable-concept":
        return
    raise RuntimeError(
        "noise-retire refused: a distill_class:concept stub cannot retire as noise without an "
        "enhanced-distill attempt — ship a distill proposal, or set "
        "'distill_attempted: no-durable-concept' after a synthesis pass finds nothing durable.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest engine/tools/tests/test_garden_distill.py -k noise_retire_fence -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add engine/tools/garden_distill.py engine/tools/tests/test_garden_distill.py
git commit -m "A56 Task 4: assert_noise_retire_allowed — the concept funnel fence"
```

---

### Task 5: Garden SKILL rewrite — synthesis-mode distill + concept exemption + budget/fence wiring

**Files:**
- Modify: `skills/garden/SKILL.md` (step 4 lines ~60-81; steps 2-3 lines ~52-59; step 7 line ~126-127)
- Modify: `skills/garden/rulebook/passes-karpathy-wiki.md` (F2.7 line ~74-82, F2.8 line ~84-94)

**Interfaces:**
- Consumes: `garden_distill.select_distill_batch`, `garden_distill.stub_class`, `garden_distill.assert_noise_retire_allowed` (Tasks 1/3/4); `garden_distill.distill_run_metrics` (Task 6).
- Produces: the run-time behaviour that concept stubs get fan-out synthesis + are fenced from noise-retire; reference stubs get the cheap path. Prose only — verified by grep + re-read, not pytest.

- [ ] **Step 1: Rewrite step 4 (Distill) for the two classes + budget**

In `skills/garden/SKILL.md`, replace the step-4 bullet `- **One stub at a time** (MVP scope; no multi-stub cross-merge in one night).` with:

```markdown
   - **Select the batch (A56 leg 3).** Enumerate all `sorted`/present stubs, then call
     `select_distill_batch(stubs, cap_k)` (`cap_k` = the profile's `garden.distill_concept_cap`,
     default 8): distill EVERY returned `concept_batch` stub this run in **synthesis mode** (below);
     leave `concept_overflow` for the next night (oldest-first, never starved by references); handle
     `reference_stubs` on the **cheap path** — the existing shallow fold, or straight retire if the
     stub has no signal. This replaces the old "one stub/night" cap.
   - **Synthesis mode (concept stubs — Karpathy fan-out).** Do NOT bullet-transfer. Read the stub's
     `## Proposed target` page AND its linked neighbours, then INTEGRATE the operational concept into
     the KB's existing conceptual structure — refine the target page's prose, add a subsection, and
     add the cross-links that connect it. A genuine concept distill typically **touches more than one
     page** (the anti-single-summary rule); emit one staging draft per touched page under the same
     distill batch, each a `lane: review` proposal. If synthesis finds NOTHING durable, do not
     propose — instead set `distill_attempted: no-durable-concept` in the stub's frontmatter (a
     `lane: review` staging edit of the stub) so the fence (below) lets it later retire, and note it
     in the run note.
```

- [ ] **Step 2: Keep the existing merge-completeness + provenance bullets, add the fence to retire**

Immediately after the "Retire is the gate's job" bullet in step 4, append:

```markdown
   - **Fence before any noise-retire.** Before proposing ANY stub for retire-as-noise in steps 2-3
     (de-bloat / prune), call `assert_noise_retire_allowed(stub.fm)` — it raises on a
     `distill_class: concept` stub that has neither a shipped/proposed distill nor a
     `distill_attempted: no-durable-concept` marker. A raised fence means: route that stub to
     synthesis mode instead of retiring it. Reference/legacy stubs are unaffected.
```

- [ ] **Step 3: Add the concept exemption to steps 2-3**

In step 2 (De-bloat), after `stub-only pages with no signal — **keep the insight + the links, drop the cruft.**`, insert:

```markdown
   **A56 fence:** a `distill_class: concept` stub is NOT "no signal" cruft — run
   `assert_noise_retire_allowed(fm)`; if it raises, route the stub to step 4 synthesis, never propose
   it for merge/prune as noise.
```

Apply the same one-line fence reminder to step 3 (Prune stale) after `never anything still `awaiting` in the queue.`:

```markdown
   Also never noise-prune a `distill_class: concept` stub that fails `assert_noise_retire_allowed` —
   it goes to step 4 synthesis first (A56).
```

- [ ] **Step 4: Wire the metric line into step 7 (VERIFY)**

In `skills/garden/SKILL.md` step 7, after `Append a context-log line (incl. what residue was swept).`, add:

```markdown
   Also append the **A56 depth tripwire** to the run note / context-log via
   `distill_run_metrics(concept_in, knowledge_pages_touched, reference_retired, fanout_counts)` —
   `fanout_counts` = pages touched per concept distill (a mean of 1.0, or zero knowledge growth
   against `concept_in > 0`, is the depth-insufficient signal = Karpathy F2.8 "undigested source").
```

- [ ] **Step 5: Update the rulebook F2.7/F2.8 to reference the A56 path**

In `skills/garden/rulebook/passes-karpathy-wiki.md`, at the end of **F2.8 — Undigested sources**, replace `**Proposal:** none directly — feeds Step 4 + the run note.` with:

```markdown
**Proposal:** none directly — feeds Step 4 + the run note. **(A56)** The "one stub at a time" cap is
retired: Step 4 now distills the whole `concept`-class batch nightly via `select_distill_batch`, and
`distill_run_metrics` emits the undigested tripwire (mean fan-out per concept distill; 1.0 = this
failure mode). A `concept` stub is fenced from noise-retire until synthesis has been attempted.
```

- [ ] **Step 6: Verify the edits landed**

Run: `grep -n "select_distill_batch\|synthesis mode\|assert_noise_retire_allowed\|distill_run_metrics" skills/garden/SKILL.md`
Expected: hits in step 4 (batch select + synthesis + fence) and step 7 (metrics).

- [ ] **Step 7: Re-read for consistency**

Re-read step 4. Confirm: (a) function names match Tasks 1/3/4/6 exactly (`select_distill_batch`, `stub_class`, `assert_noise_retire_allowed`, `distill_run_metrics`); (b) the merge-completeness self-check and FamilyOffice no-elevation bullets are still present and now scoped to synthesis mode; (c) `garden.distill_concept_cap` default 8 is stated.

- [ ] **Step 8: Commit**

```bash
git add skills/garden/SKILL.md skills/garden/rulebook/passes-karpathy-wiki.md
git commit -m "A56 Task 5: garden step-4 synthesis mode + concept fence + F2.8 metric wiring"
```

---

### Task 6: The depth metric — `distill_run_metrics()`

**Files:**
- Modify: `engine/tools/garden_distill.py` (add after `assert_noise_retire_allowed`)
- Test: `engine/tools/tests/test_garden_distill.py`

**Interfaces:**
- Consumes: nothing (pure formatter).
- Produces: `distill_run_metrics(concept_in: int, knowledge_pages_touched: int, reference_retired: int, fanout_counts: list) -> dict` = `{"mean_fanout": float, "line": str}`. Consumed by garden SKILL step 7 (Task 5) and the backfill CLI (Task 7).

- [ ] **Step 1: Write the failing test**

Add to `test_garden_distill.py`:

```python
def test_distill_run_metrics_mean_and_line():
    m = gd.distill_run_metrics(concept_in=3, knowledge_pages_touched=7,
                               reference_retired=40, fanout_counts=[1, 3, 5])
    assert m["mean_fanout"] == 3.0
    assert "concept_in=3" in m["line"]
    assert "knowledge_touched=7" in m["line"]
    assert "ref_retired=40" in m["line"]
    assert "mean_fanout=3.0" in m["line"]

def test_distill_run_metrics_empty_fanout_is_zero():
    m = gd.distill_run_metrics(0, 0, 0, [])
    assert m["mean_fanout"] == 0.0
    assert "mean_fanout=0.0" in m["line"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest engine/tools/tests/test_garden_distill.py -k distill_run_metrics -v`
Expected: FAIL with `AttributeError: ... has no attribute 'distill_run_metrics'`

- [ ] **Step 3: Write minimal implementation**

In `garden_distill.py`, add after `assert_noise_retire_allowed`:

```python
def distill_run_metrics(concept_in, knowledge_pages_touched, reference_retired, fanout_counts):
    """A56 / Karpathy F2.8 'undigested source' tripwire, as one run-note line. Inputs are this run's
    tallies; fanout_counts = pages touched per concept distill (1 == undigested single-summary).
    Returns {'mean_fanout', 'line'}. A mean fanout of 1.0, or knowledge_pages_touched 0 against
    concept_in > 0, is the depth-insufficient signal."""
    mean = round(sum(fanout_counts) / len(fanout_counts), 2) if fanout_counts else 0.0
    line = (f"distill-depth: concept_in={concept_in} knowledge_touched={knowledge_pages_touched} "
            f"ref_retired={reference_retired} mean_fanout={mean}")
    return {"mean_fanout": mean, "line": line}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest engine/tools/tests/test_garden_distill.py -k distill_run_metrics -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add engine/tools/garden_distill.py engine/tools/tests/test_garden_distill.py
git commit -m "A56 Task 6: distill_run_metrics — F2.8 undigested-source tripwire line"
```

---

### Task 7: Batch reprocessor — `enumerate_archive()` + `tally_classes()` + `backfill` CLI

**Files:**
- Modify: `engine/tools/garden_distill.py` (add helpers + extend `__main__`)
- Test: `engine/tools/tests/test_garden_distill.py`

**Interfaces:**
- Consumes: `stub_class`, `_present`, `_read`, `_frontmatter`, `glob` (all in-module).
- Produces: `enumerate_archive(archive_dir) -> list[{"slug","path","fm","class"}]` and `tally_classes(classified_items) -> dict`; a `backfill <archive_dir>` CLI op. The garden **backfill run** (a one-shot SKILL invocation, not the nightly path) enumerates the corpus, the model RE-classifies each stub (legacy stubs have no `distill_class` → default `reference`; the model overrides after reading), runs synthesis on concept ones, and tallies.

- [ ] **Step 1: Write the failing test**

Add to `test_garden_distill.py`:

```python
def test_enumerate_archive_lists_source_stubs_with_class():
    tmp = tempfile.mkdtemp()
    try:
        adir = os.path.join(tmp, "wiki-sources-retired-2026-07-10")
        _write(os.path.join(adir, "idea.md"),
               "---\ntitle: Idea\ntype: source\ndistill_class: concept\nlinks: []\n---\n\n## Core idea\n- x\n")
        _write(os.path.join(adir, "link.md"),
               "---\ntitle: Link\ntype: source\nlinks: []\n---\n\nbody\n")   # no class -> reference
        _write(os.path.join(adir, "page.md"),
               "---\ntitle: Page\ntype: knowledge\n---\n\nnot a source\n")   # excluded
        got = gd.enumerate_archive(adir)
        slugs = sorted(i["slug"] for i in got)
        assert slugs == ["idea", "link"]                      # knowledge page excluded
        by = {i["slug"]: i["class"] for i in got}
        assert by["idea"] == "concept" and by["link"] == "reference"
    finally:
        shutil.rmtree(tmp)

def test_tally_classes_counts_and_lists_concepts():
    items = [{"slug": "a", "class": "concept"}, {"slug": "b", "class": "reference"},
             {"slug": "c", "class": "concept"}]
    t = gd.tally_classes(items)
    assert t["total"] == 3
    assert t["concept_count"] == 2
    assert t["reference_count"] == 1
    assert t["concept_slugs"] == ["a", "c"]                    # sorted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest engine/tools/tests/test_garden_distill.py -k "enumerate_archive or tally_classes" -v`
Expected: FAIL with `AttributeError: ... has no attribute 'enumerate_archive'`

- [ ] **Step 3: Write minimal implementation**

In `garden_distill.py`, add after `distill_run_metrics`:

```python
def enumerate_archive(archive_dir):
    """A56 backfill — enumerate retired `type: source` stubs in a FLAT archive dir (the H39 corpus at
    raw/archive/wiki-sources-retired-<date>/). Returns [{'slug','path','fm','class'}] — enumerate_stubs
    shape plus the A56 default class (legacy stubs have no distill_class -> 'reference'; the backfill
    RUN's model re-classifies after reading each body). Backfill/validation only — NOT the nightly path."""
    out = []
    for p in sorted(glob.glob(os.path.join(archive_dir, "*.md"))):
        if not _present(p):
            continue
        fm = _frontmatter(_read(p))
        if fm.get("type") == "source":
            out.append({"slug": os.path.splitext(os.path.basename(p))[0], "path": p,
                        "fm": fm, "class": stub_class(fm)})
    return out


def tally_classes(items):
    """Pure tally of a classified stub list (each item carries 'class'): total, per-class counts, and
    the sorted concept slugs (the distill candidates a deeper ingest recovers). Fed the model's
    per-stub class decisions by both the backfill run and the nightly metric."""
    concept = sorted(i["slug"] for i in items if i.get("class") == "concept")
    reference = [i for i in items if i.get("class") != "concept"]
    return {"total": len(items), "concept_count": len(concept),
            "reference_count": len(reference), "concept_slugs": concept}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest engine/tools/tests/test_garden_distill.py -k "enumerate_archive or tally_classes" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Add the `backfill` CLI op**

In `garden_distill.py`, in the `__main__` block, add a branch after the `elif op == "retire"` branch and before the final `else`:

```python
    elif op == "backfill" and len(a) >= 2:
        items = enumerate_archive(a[1])
        for i in items:
            print(f"{i['slug']}\t{i['class']}\t{i['path']}")
        t = tally_classes(items)
        print(f"# tally: total={t['total']} concept={t['concept_count']} "
              f"reference={t['reference_count']} (default class shown; the backfill RUN re-classifies)")
```

- [ ] **Step 6: Verify the CLI op works on a fixture**

Run:
```bash
python - <<'PY'
import os, tempfile, subprocess, sys
tmp = tempfile.mkdtemp()
adir = os.path.join(tmp, "wiki-sources-retired-2026-07-10"); os.makedirs(adir)
open(os.path.join(adir, "idea.md"), "w", encoding="utf-8").write(
    "---\ntitle: Idea\ntype: source\ndistill_class: concept\nlinks: []\n---\n\n## Core idea\n- x\n")
out = subprocess.run([sys.executable, "engine/tools/garden_distill.py", "backfill", adir],
                     capture_output=True, text=True)
print(out.stdout); assert "idea\tconcept" in out.stdout and "# tally:" in out.stdout
print("OK")
PY
```
Expected: prints `idea	concept	...`, a `# tally:` line, then `OK`.

- [ ] **Step 7: Commit**

```bash
git add engine/tools/garden_distill.py engine/tools/tests/test_garden_distill.py
git commit -m "A56 Task 7: backfill mode — enumerate_archive + tally_classes + CLI"
```

---

### Task 8: Full-suite green + backfill dry-run report (the go/no-go evidence)

**Files:**
- No new code — integration + evidence gathering.

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Run the full engine suite**

Run: `python -m pytest engine/tools/tests/ -q`
Expected: all green (the pre-existing suite + the ~12 new A56 tests). If any pre-existing test broke, STOP and fix — a break means a helper changed shared behaviour.

- [ ] **Step 2: Run the backfill enumerate over a real H39 archive**

Pick one real archive dir (resolve the vault as the pipeline does — `<vault>/<live_kb_map[kb]>/raw/archive/wiki-sources-retired-2026-07-10/`; the personal KB has the largest corpus):

Run: `python engine/tools/garden_distill.py backfill "<vault>/<personal-folder>/raw/archive/wiki-sources-retired-2026-07-10"`
Expected: a `slug	class	path` line per archived stub + a `# tally:` line. NOTE: every legacy stub shows `reference` here (they predate `distill_class`) — that is EXPECTED; the report proves the enumerate + default-class path works on the real corpus. The concept re-classification is the model's job in the backfill RUN (next step).

- [ ] **Step 3: Report the numbers in chat + hand off to the backfill run**

Show the tally in chat. Then state clearly: **the deterministic engine (Tasks 1-7) is complete and green; the go/no-go gate is the model-driven backfill RUN** — a garden SKILL invocation in backfill mode that reads each of the 217 stubs, re-classifies (concept vs reference), runs synthesis mode on the concept ones, and enqueues `lane: review` proposals for Seth to review in the gate. That run + Seth's keep/drop verdict is the acceptance evidence; **live nightly wiring is out of scope for this plan until that verdict lands** (per the spec's go/no-go gate and the backlog's `[GATE: human]`).

- [ ] **Step 4: Final commit (if any integration fixes were needed)**

```bash
git add -A engine/ skills/
git commit -m "A56 Task 8: full suite green + backfill enumerate verified on real corpus"
```

---

## Self-Review

**Spec coverage:**
- Spine (`distill_class`) → Task 1 ✅
- Leg 1 ingest depth → Task 2 ✅
- Leg 2 enhanced synthesis distill → Task 5 (prose) ✅
- Leg 3 throughput → Task 3 (`select_distill_batch`) + Task 5 wiring ✅
- Funnel fence → Task 4 (`assert_noise_retire_allowed`) + Task 5 wiring ✅
- Validation batch reprocessor → Task 7 (`enumerate_archive`/`tally_classes`/CLI) + Task 8 ✅
- Signal metric (F2.8) → Task 6 (`distill_run_metrics`) + Task 5 step 4 wiring ✅
- Go/no-go gate (live wiring only after corpus review) → Task 8 step 3 (explicitly out of scope until verdict) ✅

**Placeholder scan:** No "TBD/TODO/handle edge cases" — every code step shows complete code; the one config default (`distill_concept_cap` = 8) is a concrete value. SKILL-prose tasks show the exact insertion text.

**Type consistency:** `stub_class(fm)` returns `"concept"|"reference"` and is called identically in Tasks 3/4/6/7. `select_distill_batch` returns the 3-tuple consumed only by Task 5 prose. `distill_run_metrics` / `enumerate_archive` / `tally_classes` names match between definition (Tasks 6/7) and callers (Task 5 prose, Task 7 CLI). Stub dict shape `{"slug","path","fm"}` (+`"class"` for archive) is consistent with the existing `enumerate_stubs`.

**Scope note:** One subsystem (the distill-depth path) on a shared spine — not decomposable into independent plans. Task 8 is the natural go/no-go checkpoint; the model-driven backfill RUN + live nightly wiring are deliberately deferred past this plan's ship boundary.
