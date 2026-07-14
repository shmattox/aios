# A12 — Declare & Optionally Wire the Git Dependency — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the docs from claiming AIOS commits to git (it doesn't — undo is file snapshots), and give a user who *wants* history an opt-in, instruct-don't-automate setup path — with a test that locks in "capture→gate needs no git."

**Architecture:** Three independent changes. (1) Soften two over-claiming prose spots + add one README safety-model line. (2) Add a Phase-4 `[y/N]` question and a Phase-5 instruct-don't-automate prompt to the setup skill (reusing the `new-business-unit` convention — print paste-ready `git init` commands, optional opt-in `run now?`, idempotency/git-absent guards). (3) A hermetic standalone guard test that drives capture→sort→ingest→ship→undo in a **non-git** temp install under a git-blocking PATH stub, proving the runtime uses zero git.

**Tech Stack:** Python 3 (stdlib only), standalone-script test convention (`PASS/FAIL` + `check()` + `sys.exit`), markdown skills/docs. Real helpers exercised: `queue_tx`, `ship`, `rewind`.

## Global Constraints

- **Engine runtime gets ZERO new git calls.** The setup `run now?` is the *only* place any git command is ever invoked, and only on explicit opt-in. (Spec §Non-goals.)
- **Instruct, don't automate.** Setup prints the git-init commands as the user's native step; it does not silently run git. Matches `_tools/new-business-unit/SKILL.md:103`. (Spec §Ecosystem-check.)
- **No owned committer, no scheduled task, no remote/push automation, no commit cadence.** Sync is BYO.
- **Init location = the vault root.** `state/` stays untracked runtime; undo is `rewind.py` regardless. (Spec §Decisions.)
- **Undo stays file-snapshot** (`ship.py` revert pointer / `rewind.py` snapshot) — unchanged.
- **Tests are standalone scripts**, run per file as `python engine/tools/tests/test_<name>.py` (exit 0 = green). NOT pytest. Follow the `test_a54_capture_id_collision.py` shape.
- Commit after each task. Repo root for all paths: `Projects/aios/`.

---

### Task 1: Truth-in-docs prose fixes

Docs-only. No runtime behavior changes; verified by grepping the over-claims gone and the honest text present. One task because a reviewer gates the "does the prose now tell the truth" question as a unit.

**Files:**
- Modify: `skills/gate/SKILL.md:72-73`
- Modify: `engine/pipeline/PIPELINE.md:63`
- Modify: `README.md` (Safety-model section, after line 80)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing code-facing. Task 3's assertions are what make these claims true; this task makes the docs *match* Task 3.

- [ ] **Step 1: Soften the gate SKILL `git add` instruction**

In `skills/gate/SKILL.md`, the retire clause currently ends (lines 71-73):

```
     `retire` refuses (mutates nothing) if the knowledge target is absent (ship-first invariant), is
     atomic (verify-before-move), and never hard-deletes. `git add` the moved husk + relinked files
     with the ship. **Without this clause the husk never archives — the distill loop stays half-open.**
```

Replace the sentence `` `git add` the moved husk + relinked files with the ship. `` with:

```
     atomic (verify-before-move), and never hard-deletes. The husk move is revertible via
     `rewind.py undo-ship` (file-snapshot undo — not git); if your vault is git-tracked, also commit
     the moved husk + relinked files with the ship. **Without this clause the husk never archives — the distill loop stays half-open.**
```

- [ ] **Step 2: Soften the PIPELINE.md "shipping is a git commit" claim**

In `engine/pipeline/PIPELINE.md`, line 62-63 currently reads:

```
(`source:self`, `lane:review`, `conflict_key:` = the skill file); {{ENTITY_NAME}} approves, shipping
is a git commit, `revert {id}` undoes a bad self-edit.
```

Replace `` {{ENTITY_NAME}} approves, shipping is a git commit, `revert {id}` undoes a bad self-edit. `` with:

```
(`source:self`, `lane:review`, `conflict_key:` = the skill file); {{ENTITY_NAME}} approves; the ship
records a revertible unit (a `rewind.py` snapshot / revert pointer — not a git commit; git is an
optional history layer), and `revert {id}` undoes a bad self-edit.
```

- [ ] **Step 3: Add the optional-git line to the README safety model**

In `README.md`, immediately after line 80 (the `**Paper-Governs hook.** …` paragraph) and before the `---` on line 82, insert a blank line then:

```
**Undo needs no git.** Every ship records a revertible unit as a file snapshot (`rewind.py` / a revert pointer); `revert` undoes it. AIOS runs fully on a non-git vault — git is an optional history + sync layer you wire yourself (setup offers to instruct it).
```

- [ ] **Step 4: Verify the over-claims are gone and the honest text is present**

Run (from `Projects/aios/`):

```bash
grep -rn "shipping is a git commit" engine/pipeline/PIPELINE.md; echo "exit: $?"
grep -rn '`git add` the moved husk' skills/gate/SKILL.md; echo "exit: $?"
grep -c "Undo needs no git" README.md
grep -c "revertible via" skills/gate/SKILL.md
grep -c "records a revertible unit" engine/pipeline/PIPELINE.md
```

Expected: the first two greps print **nothing** and `exit: 1` (over-claims removed); the three counts each print `1` (honest text present).

- [ ] **Step 5: Commit**

```bash
git add skills/gate/SKILL.md engine/pipeline/PIPELINE.md README.md
git commit -m "A12: docs no longer claim git-backed undo — it's file-snapshot (rewind.py)"
```

---

### Task 2: Setup skill — Phase-4 question + Phase-5 instruct-don't-automate prompt

Skill-procedure edit (the setup skill is run by an agent, not executable code), so verification is grep-for-anchors + read-through — the same way every other setup phase is specified. The runtime "no git needed" guarantee is Task 3's job; this task only adds the opt-in onboarding prompt.

**Files:**
- Modify: `skills/setup/SKILL.md` (Phase 4, after line 146; Phase 5, after line 174)

**Interfaces:**
- Consumes: nothing.
- Produces: a documented opt-in question (`Q7`) whose yes/no result Phase 5 reads. No code contract.

- [ ] **Step 1: Add the Phase-4 question**

In `skills/setup/SKILL.md`, Phase 4 currently ends with item `6.` (Capture routing) at line 146. Add item 7 immediately after it:

```
7. **Optional git history (safety default: NOT required).** Ask: "Track your vault in git for
   history + sync? [y/N]" (default **N**). AIOS needs no git to run — undo is a file snapshot
   (`rewind.py`), not a commit. A *yes* only means Phase 5 shows you the `git init` commands (and
   offers to run them); *no* skips it silently. Record the y/n for Phase 5.
```

- [ ] **Step 2: Add the Phase-5 instruct-don't-automate prompt**

In `skills/setup/SKILL.md`, Phase 5, after the KB-scaffold bullet that ends at line 174 (`… add a `vault.live_kb_map` entry.`) and before the `- **Generate `<env_root>/CLAUDE.md`** …` bullet (line 176), insert this bullet:

````
- **Optional git history (only if Q7 = yes) — instruct, don't automate (matches `new-business-unit`).**
  Run this *after* the vault/profile/KB scaffold exists so a baseline commit would capture a complete
  state. Print the exact commands as the user's native step:
  ```
  git init <vault_root>
  git -C <vault_root> add -A
  git -C <vault_root> commit -m "AIOS baseline"
  ```
  Tell them: history is on once they run it; commit as they go or wire their own cadence; for sync,
  add a remote and push (BYO); **AIOS commits nothing for them.** Then offer `Run these now? [y/N]`
  and execute the three commands **only** on an explicit yes. Guards, checked before running:
  - `git -C <vault_root> rev-parse --is-inside-work-tree` already succeeds → **skip** (already
    tracked; no re-init, no clobber), report it.
  - `git` absent (Phase 0 tool detect) → don't offer to run; print "install git, then run the
    commands above." **Never fail setup** — the engine runs without git.
  - Q7 = no, or `Run now?` = no → print the commands and move on; execute nothing.
  This `Run now?` is the ONLY place setup ever invokes git, and only on explicit opt-in.
````

- [ ] **Step 3: Verify the anchors are present and correctly placed**

Run (from `Projects/aios/`):

```bash
grep -n "Track your vault in git for" skills/setup/SKILL.md
grep -n "instruct, don't automate" skills/setup/SKILL.md
grep -n "Run these now?" skills/setup/SKILL.md
grep -n "ONLY place setup ever invokes git" skills/setup/SKILL.md
```

Expected: the first match is inside Phase 4 (line number between the Phase-4 header ~126 and the Phase-5 header ~148); the other three are inside Phase 5 (after ~174). Read the two inserted blocks once to confirm they flow with the surrounding phase prose.

- [ ] **Step 4: Commit**

```bash
git add skills/setup/SKILL.md
git commit -m "A12: setup offers opt-in git history — instruct-don't-automate (new-business-unit convention)"
```

---

### Task 3: Non-git guard test (TDD)

The runtime deliverable. A hermetic standalone test that drives capture→sort→ingest→**real** `ship.ship()`→**real** `rewind.undo_ship()` in a temp install that is **not** a git repo, under a PATH-shadowed `git` stub that records any invocation. Green today (the engine calls no git); it *bites* if a future change adds a git dependency to the ship/undo path.

**Files:**
- Create: `engine/tools/tests/test_a12_non_git_vault.py`

**Interfaces:**
- Consumes: `queue_tx._apply_items(live, items, op)`, `queue_tx.load(live)`; `ship.ship(queue_path, vault_root, kb_map, cid, approved_by, revert_dir, human_approved=False)`; `rewind.undo_ship(queue_path, cid, vault_root, revert_dir, to_stage="awaiting", kb_map=None)`. (Signatures verified against `ship.py:145` / `rewind.py:226`.)
- Produces: a suite test file; no importable API.

- [ ] **Step 1: Write the failing/guard test**

Create `engine/tools/tests/test_a12_non_git_vault.py`:

```python
#!/usr/bin/env python3
"""A12 — capture->gate completes on a NON-GIT vault, with zero git dependency.

The engine's undo is file-based (ship.py revert pointer + rewind.py snapshot), NOT git — no engine
tool shells out to git. This guard drives the real chain (capture -> sort -> ingest -> ship ->
undo-ship) in a temp install that is NOT a git repo, under a PATH-shadowed `git` stub that records
any invocation. It is green today; it BITES if a future change makes the ship/undo path call git.

Standalone; run: python tools/tests/test_a12_non_git_vault.py"""
import json, os, sys, time, tempfile, shutil

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../engine/tools
sys.path.insert(0, HARNESS)
import queue_tx, ship, rewind

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ok  " if cond else " FAIL ") + name)

def _now(): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

KB = "dev"
KB_MAP = {"dev": "dev"}
CID = "a12-nogit-note"
CK = "dev/wiki/knowledge/a12-nogit-note.md"           # <kb>/wiki/... shaped (ship._resolve_facts)
DRAFT_REL = "dev/wiki/staging/a12-nogit-note.md"      # draft_path, relative to vault

_saved_path = os.environ.get("PATH")
_saved_marker = os.environ.get("GIT_STUB_MARKER")
d = tempfile.mkdtemp(prefix="a12_nogit_")
try:
    # ── a `git` stub on PATH that records any call and exits non-zero (the WIRED guard) ──
    gitdir = os.path.join(d, "gitstub"); os.makedirs(gitdir)
    marker = os.path.join(d, "git-was-called")
    if os.name == "nt":
        with open(os.path.join(gitdir, "git.cmd"), "w", encoding="ascii") as f:
            f.write("@echo off\r\n>>\"%GIT_STUB_MARKER%\" echo called\r\nexit /b 2\r\n")
    else:
        gp = os.path.join(gitdir, "git")
        with open(gp, "w", encoding="ascii") as f:
            f.write('#!/bin/sh\necho called >> "$GIT_STUB_MARKER"\nexit 2\n')
        os.chmod(gp, 0o755)
    os.environ["GIT_STUB_MARKER"] = marker
    os.environ["PATH"] = gitdir + os.pathsep + (_saved_path or "")

    # ── a NON-GIT temp install ──
    install = os.path.join(d, "install")
    state, vault = os.path.join(install, "state"), os.path.join(install, "vault")
    revert_dir, raw_dir = os.path.join(state, "revert"), os.path.join(install, "raw", "inbox")
    for p in (state, vault, revert_dir, raw_dir):
        os.makedirs(p, exist_ok=True)
    live = os.path.join(state, "queue.json")
    items_file = live + ".items"
    check("setup: install has NO .git before the run",
          not os.path.exists(os.path.join(install, ".git")) and not os.path.exists(os.path.join(vault, ".git")))

    raw = os.path.join(raw_dir, CID + ".md")
    with open(raw, "w", encoding="utf-8") as f:
        f.write(f"# {CID}\n\nsynthetic non-git raw.\n")

    # ── STAGE 1: capture (add captured) ──
    cap = [{"id": CID, "source": "bookmark", "stage": "captured", "payload_path": raw,
            "captured_utc": _now(), "history": [{"ts": _now(), "stage": "captured"}]}]
    json.dump(cap, open(items_file, "w", encoding="utf-8"), indent=2)
    queue_tx._apply_items(live, queue_tx._read_items(items_file), "add")

    # ── STAGE 2: sort (assign kb/conflict_key/lane) ──
    by = {it["id"]: it for it in queue_tx.load(live)["queue"]}
    it = dict(by[CID]); it.update(stage="sorted", kb=KB, conflict_key=CK, lane="auto-ship")
    it["history"] = it.get("history", []) + [{"ts": _now(), "stage": "sorted"}]
    json.dump([it], open(items_file, "w", encoding="utf-8"), indent=2)
    queue_tx._apply_items(live, queue_tx._read_items(items_file), "update")

    # ── STAGE 3: ingest (write staging draft, advance to awaiting) ──
    sp = os.path.join(vault, DRAFT_REL.replace("/", os.sep)); os.makedirs(os.path.dirname(sp), exist_ok=True)
    with open(sp, "w", encoding="utf-8") as f:
        f.write("# a12-nogit-note\n\nDraft distilled from the non-git raw.\n")
    by = {it["id"]: it for it in queue_tx.load(live)["queue"]}
    it = dict(by[CID]); it.update(stage="awaiting", first_drafted_utc=_now(),
                                  recommended="approve", rec_reason="dev entity, reversible",
                                  draft_path=DRAFT_REL)
    it["history"] = it.get("history", []) + [{"ts": _now(), "stage": "awaiting"}]
    json.dump([it], open(items_file, "w", encoding="utf-8"), indent=2)
    queue_tx._apply_items(live, queue_tx._read_items(items_file), "update")

    # ── STAGE 4: gate SHIP — the real ship.py path ──
    ship.ship(live, vault, KB_MAP, CID, "auto-ship", revert_dir)
    target = os.path.join(vault, "dev", "wiki", "knowledge", "a12-nogit-note.md")
    pointer = os.path.join(revert_dir, CID + ".json")
    by = {it["id"]: it for it in queue_tx.load(live)["queue"]}
    check("ship: item advanced to 'shipped'", by[CID]["stage"] == "shipped")
    check("ship: canonical vault file written", os.path.isfile(target))
    check("ship: a file-based revert pointer exists (not a git commit)", os.path.isfile(pointer))
    check("ship: install still has NO .git after shipping",
          not os.path.exists(os.path.join(install, ".git")) and not os.path.exists(os.path.join(vault, ".git")))

    # ── UNDO — the real rewind.py path proves undo is file-based, not git ──
    rewind.undo_ship(live, CID, vault, revert_dir, to_stage="awaiting", kb_map=KB_MAP)
    by = {it["id"]: it for it in queue_tx.load(live)["queue"]}
    check("undo-ship: canonical vault file removed (file-based undo)", not os.path.exists(target))
    check("undo-ship: item back to 'awaiting'", by[CID]["stage"] == "awaiting")
    check("undo-ship: staging husk restored (re-shippable)", os.path.isfile(sp))

    # ── THE WIRED ASSERTION: no git was ever invoked across the whole chain ──
    check("guard: `git` was NEVER invoked by the engine (capture->gate->undo is git-free)",
          not os.path.exists(marker))
finally:
    if _saved_path is not None: os.environ["PATH"] = _saved_path
    if _saved_marker is None: os.environ.pop("GIT_STUB_MARKER", None)
    else: os.environ["GIT_STUB_MARKER"] = _saved_marker
    shutil.rmtree(d, ignore_errors=True)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
sys.exit(1 if FAIL else 0)
```

- [ ] **Step 2: Run it — expect GREEN (the behavior already holds)**

Run (from `Projects/aios/`):

```bash
python engine/tools/tests/test_a12_non_git_vault.py
```

Expected: `N passed, 0 failed`, exit 0. (This is a guard/characterization test — the engine already uses no git, so it passes on first write.)

- [ ] **Step 3: Prove the guard BITES (temporary regression)**

Confirm the test would catch a git dependency. Temporarily add a git call to the ship path — in `engine/tools/ship.py`, inside `ship()` right after line 145 (`def ship(...):` → its first body line), insert:

```python
    import subprocess; subprocess.run(["git", "--version"], capture_output=True)
```

Run the test again:

```bash
python engine/tools/tests/test_a12_non_git_vault.py
```

Expected: **FAIL** on `guard: `git` was NEVER invoked …` (the stub wrote the marker), exit 1. This proves the guard is wired, not vacuous.

- [ ] **Step 4: Revert the temporary regression and re-run GREEN**

Remove the two-statement line added in Step 3 from `engine/tools/ship.py`. Then:

```bash
git diff --stat engine/tools/ship.py   # expect: NO changes (fully reverted)
python engine/tools/tests/test_a12_non_git_vault.py
```

Expected: `git diff --stat` shows no change to `ship.py`; the test prints `N passed, 0 failed`, exit 0.

- [ ] **Step 5: Run the full suite for no regressions**

Run every standalone test file (from `Projects/aios/`, using the repo's actual runner):

```bash
for f in engine/tools/tests/test_*.py; do python "$f" >/dev/null 2>&1 && echo "ok  $f" || echo "FAIL $f"; done
```

Expected: `ok` for every file, including `test_a12_non_git_vault.py`; no `FAIL` lines.

- [ ] **Step 6: Commit**

```bash
git add engine/tools/tests/test_a12_non_git_vault.py
git commit -m "A12: guard test — capture->gate->undo completes on a non-git vault, zero git calls"
```

---

## Self-Review

**Spec coverage:**
- Truth-in-docs fixes (spec §1): Task 1 (gate SKILL:72, PIPELINE.md:63, README safety line). ✔
- Setup Phase-4 question + Phase-5 instruct prompt + idempotency/git-absent guards (spec §2, §3): Task 2. ✔
- Non-git guard test (spec §4): Task 3, exercising real `ship`/`rewind`. ✔
- Decisions — instruct-don't-automate, vault-root init, no owned committer (spec §Decisions): Global Constraints + Task 2 prose. ✔
- Ecosystem-check reuse of `new-business-unit` (spec §Ecosystem-check): Global Constraints + Task 2 references it. ✔
- Acceptance bullets (spec §Acceptance): Task 1 Step 4 (diffs), Task 2 Step 3 (prompt present), Task 3 Steps 2-5 (yes/no/absent/already-repo paths documented in the skill; the non-git cycle green). ✔ Note: the yes-path "vault becomes a repo" is an agent-run skill step, verified by the documented commands, not a Python test — consistent with setup being skill-procedure, not code.

**Placeholder scan:** No TBD/TODO; every code/prose change shows exact old→new text and exact commands with expected output. ✔

**Type consistency:** `ship.ship(queue_path, vault_root, kb_map, cid, approved_by, revert_dir, human_approved=False)` and `rewind.undo_ship(queue_path, cid, vault_root, revert_dir, to_stage, snap_dir, kb_map)` used exactly as defined in `ship.py:145` / `rewind.py:226`. `conflict_key` is `<kb>/wiki/...`-shaped per `_resolve_facts`; `draft_path` is vault-relative; `kb_map={"dev":"dev"}` maps the kb segment. ✔
