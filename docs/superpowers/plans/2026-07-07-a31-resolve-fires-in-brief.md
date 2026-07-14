# A31 — Make the resolve step fire in the brief — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the brief's resolve step un-skippable — it deterministically reads the A34 warm cache (`sweep.json`), resolves each flagged economic task into a dossier, renders the verdict verbatim, and fails loud (tool-emitted line) if any flagged task went unresolved.

**Architecture:** Two new deterministic tool surfaces (`resolve_brief.py` with `worklist`+`check` ops; `brief_render.render_dossier`) plus two skill-doc rewires (`gather.md` deep-resolve, `SKILL.md` PASS-2 wiring). The tools carry the guarantees; the skill docs invoke them. The model produces evidence data; the engine owns the format and the completeness verdict.

**Tech Stack:** Python 3 stdlib only (no third-party imports in engine tools); pytest via `suite_test.py` (each `test_*.py` runs as a subprocess and must have a `__main__` runner + be non-vacuous); markdown skill docs.

## Global Constraints

- **Stdlib only** in `engine/tools/*.py` — no `yaml`/third-party imports.
- **Test runner:** `python -m pytest engine/tools/tests/` runs each `test_*.py` as a subprocess (`suite_test.py`); every new test file MUST end with the standard `__main__` runner (copy from `test_resolve_sweep.py`) and exit non-zero on failure.
- **Verdicts come only from `resolve_verdict.compute_verdict`** — no code (and no model prose) sets a verdict by fiat. This slice auto-ships **no** economic judgment.
- **Deterministic render / fail-loud:** must-hold formats are emitted by a tool and lifted verbatim; a must-run step is verified by a tool whose output is lifted verbatim (never model-composed).
- **Review tier:** the diff (brief FO economic rendering = Paper-Governs) goes through the **review-gate workflow**; block on confirmed CRITICAL / security-HIGH.

## Shared contracts (referenced by multiple tasks)

**`sweep.json`** (written by A34 `resolve_sweep_task.py`, in `resolve.cache_dir`):
```json
{"stage":"resolve-sweep","source":"notion","task_count":71,"flagged_count":45,
 "content_hash":"…","generated_utc":"…",
 "flagged":[{"id":"t1","title":"Pay property insurance $4,200","reason":"figure",
             "candidates":[{"source":"drive","ref":"file-123","desc":"…","entity":"bayview-flats.md"}]}]}
```

**`verdict`** (from `resolve_verdict.compute_verdict(claim_qty, evidence, paper_sources)`):
```python
{"verdict": "silent"|"papered"|"conflict"|"verbal-only",
 "canonical": str|None,      # e.g. "$4,200 — cited to drive:file-123" (papered) else None
 "conflict": str|None,       # reason (conflict) else None
 "provenance": [str],        # sources, tier-ordered
 "auto_promote": bool}
```

**`dossier`** (the per-task artifact this slice writes to `resolve.cache_dir/<task_id>.json`, and what `render_dossier` consumes):
```python
{"task_id": str, "title": str, "claim_qty": str,
 "verdict": str, "canonical": str|None, "conflict": str|None,
 "provenance": [str], "auto_promote": bool,
 "sweep_source": str}       # provenance of the flag ("notion"/"tasks-file"); NOT used by check
```
Dossier filename in `resolve.cache_dir` is **`<task_id>.json`**. The completeness `check` is **presence-based**: a flagged task is resolved iff `<cache_dir>/<task_id>.json` exists. (Per-task staleness — a changed task whose old dossier lingers — is an explicit NON-GOAL of this slice; the forcing function only needs to catch a wholesale skipped resolve.)

---

## Task 1: `resolve_brief.py` — the `worklist` op

**Files:**
- Create: `engine/tools/resolve_brief.py`
- Test: `engine/tools/tests/test_resolve_brief.py`

**Interfaces:**
- Consumes: `sweep.json` (shared contract).
- Produces: `worklist(sweep_path) -> list[dict]` where each dict is `{"task_id": str, "title": str, "candidates": list[dict]}`; missing/empty/unreadable sweep → `[]`. CLI: `python resolve_brief.py worklist <sweep_path>` prints `json.dumps({"worklist": [...]})`.

- [ ] **Step 1: Write the failing test**

```python
import os, sys, json, tempfile, shutil
_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _TOOLS)
import resolve_brief as rb

def _sweep(d, flagged):
    p = os.path.join(d, "sweep.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"flagged": flagged}, f)
    return p

def test_worklist_enumerates_flagged_tasks_and_candidates():
    d = tempfile.mkdtemp()
    try:
        p = _sweep(d, [{"id": "t1", "title": "Pay insurance $4,200",
                        "candidates": [{"source": "drive", "ref": "file-123"}]},
                       {"id": "t2", "title": "Wire escrow", "candidates": []}])
        wl = rb.worklist(p)
        assert [w["task_id"] for w in wl] == ["t1", "t2"]
        assert wl[0]["title"] == "Pay insurance $4,200"
        assert wl[0]["candidates"][0]["ref"] == "file-123"
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_worklist_missing_or_empty_sweep_is_empty_list():
    assert rb.worklist("/no/such/sweep.json") == []
    d = tempfile.mkdtemp()
    try:
        p = _sweep(d, [])
        assert rb.worklist(p) == []
    finally:
        shutil.rmtree(d, ignore_errors=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python engine/tools/tests/test_resolve_brief.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'resolve_brief'`.

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
"""resolve_brief.py — the brief's deterministic bridge to the A34 warm resolve cache (A31).

Two ops, both stdlib-only:
  worklist <sweep_path>            -> the enumerated flagged economic tasks the brief MUST resolve.
  check <sweep_path> <cache_dir>   -> a tool-emitted, verbatim-lifted line that is loud when any
                                      flagged task has no dossier (the anti-skip forcing function).
The brief lifts both verbatim; neither is model-composed.
"""
import argparse, json, os, sys


def _load_flagged(sweep_path):
    try:
        with open(sweep_path, encoding="utf-8") as f:
            return json.load(f).get("flagged") or []
    except (OSError, json.JSONDecodeError):
        return []


def worklist(sweep_path):
    """sweep.json -> [{task_id, title, candidates}]; [] when absent/empty/unreadable."""
    out = []
    for t in _load_flagged(sweep_path):
        out.append({"task_id": t.get("id"), "title": t.get("title") or "",
                    "candidates": t.get("candidates") or []})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Brief bridge to the resolve warm cache")
    sub = ap.add_subparsers(dest="op", required=True)
    w = sub.add_parser("worklist"); w.add_argument("sweep_path")
    args = ap.parse_args(argv)
    if args.op == "worklist":
        print(json.dumps({"worklist": worklist(args.sweep_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python engine/tools/tests/test_resolve_brief.py`
Expected: `2/2 passed` (add the `__main__` runner block below before running).

Append this runner to the test file (copied from `test_resolve_sweep.py`):

```python
if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print("  ok  " + fn.__name__)
        except Exception:
            failed += 1; print(" FAIL " + fn.__name__); traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
```

- [ ] **Step 5: Commit**

```bash
git add engine/tools/resolve_brief.py engine/tools/tests/test_resolve_brief.py
git commit -m "A31: resolve_brief.py worklist op — enumerate flagged tasks from sweep.json"
```

---

## Task 2: `resolve_brief.py` — the `check` op (the forcing function)

**Files:**
- Modify: `engine/tools/resolve_brief.py`
- Test: `engine/tools/tests/test_resolve_brief.py` (add cases)

**Interfaces:**
- Consumes: `sweep.json`; the dossier files `<cache_dir>/<task_id>.json`.
- Produces: `check(sweep_path, cache_dir) -> {"complete": bool, "missing": [task_id], "line": str}`. `line` is `""` when complete, else the verbatim loud line `"⚠ resolve INCOMPLETE — {k} of {m} flagged economic tasks unresolved: id1, id2"`. CLI: `python resolve_brief.py check <sweep_path> <cache_dir>` prints `line` (or nothing) and exits 0 always (advisory; the brief lifts the line).

- [ ] **Step 1: Write the failing test**

```python
def test_check_complete_when_all_flagged_have_dossiers():
    d = tempfile.mkdtemp()
    try:
        p = _sweep(d, [{"id": "t1", "title": "A"}, {"id": "t2", "title": "B"}])
        cache = os.path.join(d, "cache"); os.makedirs(cache)
        for tid in ("t1", "t2"):
            with open(os.path.join(cache, tid + ".json"), "w", encoding="utf-8") as f:
                json.dump({"task_id": tid, "verdict": "verbal-only"}, f)
        r = rb.check(p, cache)
        assert r["complete"] is True and r["missing"] == [] and r["line"] == ""
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_check_reports_missing_dossiers_with_loud_line():
    d = tempfile.mkdtemp()
    try:
        p = _sweep(d, [{"id": "t1", "title": "A"}, {"id": "t2", "title": "B"}])
        cache = os.path.join(d, "cache"); os.makedirs(cache)
        with open(os.path.join(cache, "t1.json"), "w", encoding="utf-8") as f:
            json.dump({"task_id": "t1"}, f)
        r = rb.check(p, cache)
        assert r["complete"] is False and r["missing"] == ["t2"]
        assert r["line"].startswith("⚠ resolve INCOMPLETE — 1 of 2")
        assert "t2" in r["line"]
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_check_empty_sweep_is_complete_noop():
    d = tempfile.mkdtemp()
    try:
        p = _sweep(d, [])
        r = rb.check(p, os.path.join(d, "cache"))
        assert r["complete"] is True and r["line"] == ""
    finally:
        shutil.rmtree(d, ignore_errors=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python engine/tools/tests/test_resolve_brief.py`
Expected: FAIL — `AttributeError: module 'resolve_brief' has no attribute 'check'`.

- [ ] **Step 3: Write minimal implementation**

Add to `resolve_brief.py`:

```python
def check(sweep_path, cache_dir):
    """Every flagged task must have a dossier (<cache_dir>/<task_id>.json). Presence-based.
    Returns {complete, missing[], line}; line is the verbatim loud output ('' when complete)."""
    flagged = _load_flagged(sweep_path)
    missing = [t.get("id") for t in flagged
               if not os.path.exists(os.path.join(cache_dir, "%s.json" % t.get("id")))]
    if not missing:
        return {"complete": True, "missing": [], "line": ""}
    line = "⚠ resolve INCOMPLETE — %d of %d flagged economic tasks unresolved: %s" % (
        len(missing), len(flagged), ", ".join(str(m) for m in missing))
    return {"complete": False, "missing": missing, "line": line}
```

Extend `main` (add the `check` subparser + dispatch):

```python
    c = sub.add_parser("check"); c.add_argument("sweep_path"); c.add_argument("cache_dir")
    ...
    if args.op == "check":
        r = check(args.sweep_path, args.cache_dir)
        if r["line"]:
            print(r["line"])
        return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python engine/tools/tests/test_resolve_brief.py`
Expected: `5/5 passed`.

- [ ] **Step 5: Commit**

```bash
git add engine/tools/resolve_brief.py engine/tools/tests/test_resolve_brief.py
git commit -m "A31: resolve_brief.py check op — loud verbatim line when a flagged task is unresolved"
```

---

## Task 3: `brief_render.render_dossier` (subsumes A37)

**Files:**
- Modify: `engine/tools/brief_render.py`
- Test: `engine/tools/tests/test_render_dossier.py`

**Interfaces:**
- Consumes: a `dossier` dict (shared contract).
- Produces: `render_dossier(dossier) -> str` — the verbatim verdict card. Grades map: `papered`→🟢, `conflict`→🔴 (held), `verbal-only`→🟠, `silent`→dim line.

- [ ] **Step 1: Write the failing test**

```python
import os, sys
_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _TOOLS)
import brief_render as br

def test_render_papered_shows_green_and_canonical():
    card = br.render_dossier({"title": "Pay insurance $4,200", "verdict": "papered",
                              "canonical": "$4,200 — cited to drive:file-123", "conflict": None})
    assert "Pay insurance $4,200" in card
    assert "🟢" in card and "Papered" in card and "drive:file-123" in card

def test_render_conflict_shows_red_held_and_reason():
    card = br.render_dossier({"title": "Metropolis tax", "verdict": "conflict", "canonical": None,
                              "conflict": "trello says $9,000; paper says $8,200"})
    assert "🔴" in card and "Conflict" in card and "held" in card.lower()
    assert "trello says $9,000; paper says $8,200" in card

def test_render_verbal_only_shows_orange_no_paper():
    card = br.render_dossier({"title": "Vendor invoice", "verdict": "verbal-only",
                              "canonical": None, "conflict": None, "provenance": ["notion"]})
    assert "🟠" in card and "no executed paper" in card.lower()

def test_render_silent_is_dim_line():
    card = br.render_dossier({"title": "X", "verdict": "silent", "canonical": None, "conflict": None})
    assert "silent" in card.lower()

def test_render_unknown_verdict_does_not_crash():
    card = br.render_dossier({"title": "X", "verdict": "weird"})
    assert "X" in card
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python engine/tools/tests/test_render_dossier.py`
Expected: FAIL — `AttributeError: module 'brief_render' has no attribute 'render_dossier'`.

- [ ] **Step 3: Write minimal implementation**

Add to `brief_render.py`:

```python
def render_dossier(dossier):
    """Dossier dict -> verbatim resolve verdict card. The papered/conflict/verbal-only/silent
    distinction is Paper-Governs and MUST NOT drift between renders, so it is emitted here (lifted
    verbatim by the brief), never hand-written in skill prose. The verdict itself comes from
    resolve_verdict.compute_verdict — this only formats it."""
    title = dossier.get("title", "")
    verdict = dossier.get("verdict")
    header = "**%s**" % title
    if verdict == "papered":
        body = "🟢 **Papered** — %s" % (dossier.get("canonical") or "(figure cited)")
    elif verdict == "conflict":
        body = "🔴 **Conflict (held for you)** — %s" % (dossier.get("conflict") or "unresolved discrepancy")
    elif verdict == "verbal-only":
        prov = ", ".join(dossier.get("provenance") or []) or "verbal"
        body = "🟠 **Verbal only — no executed paper** (%s)" % prov
    elif verdict == "silent":
        body = "— *resolve silent (no aligned evidence)* —"
    else:
        body = "— *resolve verdict unavailable* —"
    return header + "\n" + body
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python engine/tools/tests/test_render_dossier.py`
Expected: `5/5 passed` (append the standard `__main__` runner from Task 1 Step 4).

- [ ] **Step 5: Commit**

```bash
git add engine/tools/brief_render.py engine/tools/tests/test_render_dossier.py
git commit -m "A31/A37: brief_render.render_dossier — verbatim papered/conflict/verbal-only/silent card"
```

---

## Task 4: Rewire `gather.md` — worklist-driven deep-resolve that writes dossiers

**Files:**
- Modify: `skills/brief/references/gather.md` (the step-4 resolve block)

**Interfaces:**
- Consumes: `resolve_brief.py worklist`; per-task deep-resolve produces a `dossier` (shared contract) written to `resolve.cache_dir/<task_id>.json`.
- Produces: one dossier JSON per flagged task in `resolve.cache_dir`.

This is a skill-doc (prose) change — verified end-to-end by the acceptance brief run, not a unit test. Replace the buried "run resolve_sweep… write the dossier" prose with worklist-driven, enumerated work.

- [ ] **Step 1: Replace the step-4 resolve block** in `gather.md` with:

```markdown
4. **Resolve — economic FO tasks (A31, warm-cache driven).** The overnight sweep (`aios-resolve-sweep`,
   A34) has already flagged the economic tasks + candidate governing docs. Do NOT re-run the sweep —
   READ its worklist and resolve each item:
   `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/resolve_brief.py" worklist "<env_root>/state/resolve-cache/sweep.json"`
   → `{worklist: [{task_id, title, candidates[]}]}`. For EACH worklist item, in parallel (one
   `deep_model` sub-agent per task — dispatching-parallel-agents): (a) for each candidate, read the
   source doc and build a typed evidence row `{source, ref, says, value, qty, tier, executed}`
   (`tier` derived from `source`: drive→`paper`, notion→`operational`, trello→`verbal`; `executed`
   true only for an executed Drive doc); (b) SELECT the governing doc for the claim's quantity;
   (c) run `resolve_verdict.py` over the per-claim `evidence[]` — NEVER set the verdict yourself;
   (d) WRITE the dossier to `<resolve.cache_dir>/<task_id>.json` with fields
   `{task_id, title, claim_qty, verdict, canonical, conflict, provenance, auto_promote, sweep_source}`
   (verdict/canonical/conflict/provenance/auto_promote copied from the `resolve_verdict` result).
   If the entity has no crosswalk or the candidates are empty, fall back to semantic search and, on a
   hit, propose adding the link back to the entity page **via `gate`** (self-heal; the brief never
   writes). **Every worklist item MUST end with a dossier file — the completeness check (PASS 2)
   verifies this and the brief fails loud if one is missing.**
```

- [ ] **Step 2: Verify the referenced fields match the tools**

Confirm the dossier fields listed match Task 2's `check` (keys on `<task_id>.json`) and Task 3's
`render_dossier` inputs (`title`, `verdict`, `canonical`, `conflict`, `provenance`). Run:
`grep -n "task_id\|verdict\|canonical\|provenance" skills/brief/references/gather.md`
Expected: the dossier field list is present and matches the contract.

- [ ] **Step 3: Commit**

```bash
git add skills/brief/references/gather.md
git commit -m "A31: gather.md resolve step reads the sweep worklist + writes per-task dossiers"
```

---

## Task 5: Wire the completeness check + dossier render into the brief PASS 2

**Files:**
- Modify: `skills/brief/SKILL.md` (PASS 2 render flow)

**Interfaces:**
- Consumes: `resolve_brief.py check`; `brief_render.render_dossier` (via the render step); the dossier files from Task 4.
- Produces: the brief body contains a **Resolve** section — one `render_dossier` card per flagged task — and, when `check` is non-empty, its verbatim `⚠ resolve INCOMPLETE …` line.

Skill-doc change — verified by the acceptance brief run.

- [ ] **Step 1: Add the resolve render + check to PASS 2**, after the station walk render and before the brief is declared done:

```markdown
**Resolve section (A31 — after the walk render, before done).** For each dossier file in
`<resolve.cache_dir>` matching a flagged task, lift its card VERBATIM:
`brief_render.render_dossier(<dossier>)` — do NOT hand-write the papered/conflict/verbal-only line
(the format is the engine's). Then run the completeness check and lift its output VERBATIM:
`python "${CLAUDE_PLUGIN_ROOT}/engine/tools/resolve_brief.py" check "<env_root>/state/resolve-cache/sweep.json" "<env_root>/state/resolve-cache"`
— if it prints a `⚠ resolve INCOMPLETE …` line, that line MUST appear in the brief body (it means a
flagged economic task went unresolved — never suppress or reword it). No line = resolve complete.
```

- [ ] **Step 2: Verify wiring references are correct**

Run: `grep -n "render_dossier\|resolve_brief.py check\|resolve INCOMPLETE" skills/brief/SKILL.md`
Expected: all three references present in the PASS-2 resolve section.

- [ ] **Step 3: Commit**

```bash
git add skills/brief/SKILL.md
git commit -m "A31: brief PASS 2 lifts dossier cards + the verbatim resolve-incomplete line"
```

---

## Task 6: Full-suite green + end-to-end acceptance

**Files:** none (verification task).

- [ ] **Step 1: Run the whole suite**

Run: `python -m pytest engine/tools/tests/ -q`
Expected: all files pass (30 prior + `test_resolve_brief.py` + `test_render_dossier.py`).

- [ ] **Step 2: End-to-end — resolve fires from the warm cache**

Seed a `sweep.json` with one flagged task in a scratch `resolve.cache_dir`, write a matching dossier
(as the brief would), then show:
- `resolve_brief.py worklist <sweep>` enumerates the task;
- `render_dossier` on the written dossier prints the verdict card;
- `resolve_brief.py check <sweep> <cache>` prints nothing (complete);
- delete the dossier and re-run `check` → it prints the `⚠ resolve INCOMPLETE …` line naming the task.
Show each command + output in chat.

- [ ] **Step 3: Review gate**

Stage the diff and run the **review-gate workflow** (`project`, `base HEAD`, context = this plan).
PASS requires zero CONFIRMED CRITICAL / security-HIGH. Loop any blocker back to a fix pass.

- [ ] **Step 4: Commit any review fixes, then tick A31 acceptance items in BACKLOG.**

```bash
git add -A && git commit -m "A31: resolve fires in the brief — suite green + acceptance shown"
```

---

## Self-Review

**Spec coverage:**
- §Design 1 (concrete worklist) → Task 1 + Task 4. ✓
- §Design 2 (deterministic dossier render, subsumes A37) → Task 3. ✓
- §Design 3 (completeness check, tool-emitted verbatim line) → Task 2 + Task 5. ✓
- §Components 1–4 → Tasks 1/2 (tool), 3 (render), 4 (gather wiring), 5 (PASS-2 wiring). ✓
- §Acceptance → Task 6 (suite, e2e fire, incomplete-line, review gate). ✓
- §Boundaries: no verdict set by code (verdicts only from `resolve_verdict`, Task 4 step (c)); A35/A38 out of scope (not in any task). ✓

**Placeholder scan:** no TBD/TODO; every code step shows complete code. ✓

**Type consistency:** `worklist` returns `{task_id,title,candidates}` (Task 1) consumed by gather (Task 4); dossier keys `{task_id,title,claim_qty,verdict,canonical,conflict,provenance,auto_promote,sweep_source}` written in Task 4, read by `check` via `<task_id>.json` presence (Task 2) and by `render_dossier` fields `title/verdict/canonical/conflict/provenance` (Task 3) — consistent. `check` returns `{complete,missing,line}` (Task 2) lifted in Task 5. ✓

**Non-goal noted:** per-task dossier staleness (changed task, lingering old dossier) is out of scope — the check is presence-based and only needs to catch a wholesale skip.
