<!-- sanitize:allow-file — worked examples use synthetic/anonymized ids (A79) -->
# Settle Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the daily brief a reliable precompute plus a Stage-0 "settle" pass that detects task completions across all evidence sources and writes Notion current — closing the operational write-loop that only manual brief decisions close today.

**Architecture:** Fold a reconciler into the existing `aios-brief-cache` precompute. A deterministic script (`settle_reconcile.py`) auto-heals writes that a prior brief decision intended but that never landed in Notion; the precompute model proposes *inferred* completions (with cited evidence) that wait for a one-click confirm at the desk. A new `settle` block in `brief-cache.json` carries both; a new `brief_render.py settle` op renders the Stage-0 panel; confirms flip Notion via the existing `notion_writeback.py`.

**Tech Stack:** Python 3 (engine tools, stdlib only — no new deps), pytest (`engine/tools/tests/`), Markdown skill/task bodies, JSON task manifest, Windows Task Scheduler runner.

## Global Constraints

- **Fact-free tools.** No env_root, ids, or paths hardcoded in engine tools — they arrive as args (the profile/runner supplies them). Copy the `resolve_sweep_task.py` shape: `run(env_root, ...)` + `--env-root` + a hermetic `--tasks-file`/`--no-context-log` override for tests.
- **Notion writes go through `notion_writeback.py` only** — never ad-hoc MCP or raw writes. `flip` targets only `{status, select, checkbox, date}`; economic/number/relation fields refuse by type (exit 3). This fence is load-bearing; do not weaken it.
- **Fail-loud boundary.** The deterministic auto-heal (a decision already made, write didn't land) may write in the background, fix-then-tell. Inferred completions NEVER auto-write — they wait for at-desk confirm.
- **Queue/ledger read discipline.** Read the queue via `queue_tx.py`, the ledger via `brief_session.py`; never hand-parse `state/queue.json` or `state/brief-session.json`.
- **Deterministic render.** Cards/panels are emitted by `brief_render.py` and lifted verbatim — never hand-composed in skill prose.
- **Native git only.** Commit each task from a native session; never invoke git from the sandbox.
- **Tests:** run from repo root as `pytest engine/tools/tests/test_<name>.py -v`. Tools import siblings by bare module name; `engine/tools/tests/conftest.py` puts `engine/tools/` on `sys.path`.

---

## Phase A — Unmask the silent failure (do first)

### Task A1: Runner propagates the inner exit code

**Files:**
- Modify: the Windows task runner that wraps `claude -p` and writes `state/task-logs/<task>/last-run.log`. **First locate it** — `grep -rl "last-run.log" Projects/aios/deploy Scripts` (it is either `aios/deploy/windows/*` → AIOS repo, or native `Scripts/` → env-ops). Route the commit to whichever repo owns it.

**Interfaces:**
- Produces: Task Scheduler `LastTaskResult` reflects the inner `claude -p` exit code (non-zero when the model run failed), instead of always `0x0`.

- [ ] **Step 1: Locate the runner and read its exit handling**

Run: `grep -rn "last-run.log\|LastTaskResult\|exit" Projects/aios/deploy/windows Scripts 2>/dev/null`
Expected: find the wrapper (`.bat`/`.vbs`/`.ps1`) that runs `claude -p` and currently swallows the code. Confirm today's evidence: `state/task-logs/aios-brief-cache/last-run.log` shows `2026-07-08 … exit=1 Error: Reached max turns (80)` while Task Scheduler reported `0x0`.

- [ ] **Step 2: Capture and propagate the inner exit code**

Edit the wrapper so the process exit code equals `claude -p`'s code. Pattern (batch):
```bat
claude -p ... 1>>"%LOGFILE%" 2>&1
set RC=%ERRORLEVEL%
echo %DATE%T%TIME%  exit=%RC% >> "%LOGFILE%"
exit /b %RC%
```
If the runner is `.vbs` shelling out, capture `oExec.ExitCode` (or the `Run(..., True)` return) and `WScript.Quit` with it. The rule: the scheduled task must exit non-zero when the model run failed.

- [ ] **Step 3: Manually verify propagation**

Run: force a failure (temporarily set the task's `max_turns` to `1` in the manifest and re-register, or invoke the wrapper directly with a prompt that cannot finish), then:
`powershell "Get-ScheduledTaskInfo -TaskName 'AIOS aios-brief-cache' | Select LastTaskResult"`
Expected: `LastTaskResult` is non-zero (not `0`). Restore `max_turns` afterward.

- [ ] **Step 4: Commit**

```bash
git add <runner-path>
git commit -m "fix(runner): propagate inner claude -p exit code to Task Scheduler

A failed model task reported 0x0, masking the 2026-07-08 brief-cache
max-turns failure. The scheduled task now exits with the inner code."
```

### Task A2: Freshness-alarm tool

A run that writes nothing leaves no trace. This tool gives it one: after `aios-brief-cache`, assert the headline advanced; if not, emit a `brief` anomaly the pipeline-health line surfaces.

**Files:**
- Create: `engine/tools/brief_freshness_check.py`
- Test: `engine/tools/tests/test_brief_freshness_check.py`

**Interfaces:**
- Produces: `stale_since(headline_path, run_start_epoch) -> bool` (True when the file's mtime did NOT advance past `run_start_epoch`); CLI `brief_freshness_check.py --env-root <r> --run-start <epoch>` emits a `brief` anomaly context-log line when stale, exits 0 either way (an alarm, never a hard fail).

- [ ] **Step 1: Write the failing test**

```python
# engine/tools/tests/test_brief_freshness_check.py
import os, time
import brief_freshness_check as fc

def test_stale_when_mtime_not_advanced(tmp_path):
    hp = tmp_path / "brief-headline.md"
    hp.write_text("old", encoding="utf-8")
    old = time.time() - 3600
    os.utime(hp, (old, old))
    assert fc.stale_since(str(hp), run_start_epoch=time.time()) is True

def test_fresh_when_mtime_advanced(tmp_path):
    hp = tmp_path / "brief-headline.md"
    run_start = time.time() - 10
    hp.write_text("new", encoding="utf-8")   # written after run_start
    assert fc.stale_since(str(hp), run_start_epoch=run_start) is False

def test_missing_file_is_stale(tmp_path):
    assert fc.stale_since(str(tmp_path / "nope.md"), run_start_epoch=time.time()) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest engine/tools/tests/test_brief_freshness_check.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brief_freshness_check'`.

- [ ] **Step 3: Write minimal implementation**

```python
# engine/tools/brief_freshness_check.py
#!/usr/bin/env python3
"""Alarm when aios-brief-cache ran but did not refresh the headline (silent-failure guard)."""
import argparse, os, sys, time
import context_log

STAGE = "brief"

def stale_since(headline_path, run_start_epoch):
    """True if the headline file is missing or its mtime did not advance past run_start_epoch."""
    try:
        return os.path.getmtime(headline_path) <= run_start_epoch
    except OSError:
        return True

def main(argv=None):
    ap = argparse.ArgumentParser(description="Alarm if the brief precompute wrote no fresh headline.")
    ap.add_argument("--env-root", required=True)
    ap.add_argument("--run-start", type=float, required=True, help="epoch seconds when the precompute started")
    ap.add_argument("--no-context-log", action="store_true")
    args = ap.parse_args(argv)
    headline = os.path.join(args.env_root, "state", "brief-headline.md")
    stale = stale_since(headline, args.run_start)
    if stale and not args.no_context_log:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "stage": STAGE,
               "skill": "aios-brief-cache", "anomaly": "precompute wrote no fresh headline",
               "result": "stale"}
        try:
            context_log.emit(rec, os.path.join(args.env_root, "state", "context-log.jsonl"))
        except Exception:
            pass
    print("stale" if stale else "fresh")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest engine/tools/tests/test_brief_freshness_check.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Wire the runner to call it**

In the Task A1 runner, capture the start epoch before `claude -p` and, after it, call the check (so a no-write run alarms):
```bat
for /f %%t in ('powershell -c "[int](Get-Date -UFormat %%s)"') do set START=%%t
claude -p ...   & set RC=%ERRORLEVEL%
python "%PLUGIN_ROOT%\engine\tools\brief_freshness_check.py" --env-root "%ENV_ROOT%" --run-start %START%
```
(Only for the `aios-brief-cache` task.)

- [ ] **Step 6: Commit**

```bash
git add engine/tools/brief_freshness_check.py engine/tools/tests/test_brief_freshness_check.py <runner-path>
git commit -m "feat(brief): alarm when precompute writes no fresh headline"
```

---

## Phase B — Reliable live precompute

### Task B1: Enable the tasks and raise the turn budget

**Files:**
- Modify: `deploy/tasks.manifest.json` — the `aios-brief-cache` and `aios-resolve-sweep` entries.

**Interfaces:**
- Produces: both tasks `enabled: true` (matching their live Task Scheduler registration); `aios-brief-cache` `max_turns: 150`.

- [ ] **Step 1: Edit the manifest entries**

In `deploy/tasks.manifest.json`:
- `aios-brief-cache`: change `"max_turns": 80` → `"max_turns": 150` and `"enabled": false` → `"enabled": true`.
- `aios-resolve-sweep`: change `"enabled": false` → `"enabled": true`.

- [ ] **Step 2: Verify the manifest still parses and any manifest test passes**

Run: `python -c "import json; json.load(open('deploy/tasks.manifest.json'))" && echo OK`
Expected: `OK`.
Run: `ls engine/tools/tests | grep -i manifest && pytest engine/tools/tests/*manifest* -v 2>/dev/null || echo "no manifest test"`
Expected: existing manifest test passes, or "no manifest test".

- [ ] **Step 3: Commit**

```bash
git add deploy/tasks.manifest.json
git commit -m "chore(tasks): enable brief-cache + resolve-sweep, raise brief max_turns 80->150

Both ran live in Task Scheduler while the manifest still said enabled:false;
brief-cache blew its 80-turn budget on 2026-07-08 (and the settle pass adds work)."
```

### Task B2: Confirm the precompute's Notion leg reads live

The `brief-cache` body already instructs `notion_gather.py` first (A18). `resolve-sweep` proved the token works headless (71 tasks, 2026-07-08). This task verifies the live path is actually taken — no code change unless it isn't.

**Files:**
- Read: `deploy/tasks/brief-cache.md` (the Headless-Notion section)
- Read: `profile/connectors.yaml` (token + writable ids), env/Credential Manager `AIOS_NOTION_TOKEN`

- [ ] **Step 1: Confirm the token resolves headless**

Run: `python engine/tools/notion_gather.py tasks --status-exclude Done --db <a tasks_db collection:// id from profile/domains.yaml> | python -c "import sys,json; d=json.load(sys.stdin); print([s['ok'] for s in d['sources']])"`
Expected: `[True]` (live). If `False`/exit 2 → the token isn't configured for this shell; fix `AIOS_NOTION_TOKEN` (env-ops) before relying on `notion_live:true`.

- [ ] **Step 2: Confirm the body degrades honestly, not silently**

Read `deploy/tasks/brief-cache.md`; verify the Headless-Notion steps set `source_counts.notion_live` from the per-source `ok` and carry `notion_carried_from` on degrade. If the body lacks the per-source check described in Phase C's cache contract, note it — Task C5 rewrites this body anyway.

- [ ] **Step 3: No commit unless a fix was needed** (verification task; fold any doc fix into C5).

---

## Phase C — Settle reconciler + Notion write closure

### Task C1: `settle_reconcile.py` — deterministic auto-heal

Detects writes a prior brief decision *intended* (`executed` + a `notion_write` intent from Task C4) but that have no matching changelog receipt, and flips them via `notion_writeback`.

**Files:**
- Create: `engine/tools/settle_reconcile.py`
- Test: `engine/tools/tests/test_settle_reconcile.py`

**Interfaces:**
- Consumes: ledger decisions carrying `{"executed": true, "notion_write": {"page_id","field","to"}}` (Task C4); changelog rows `{page_id, field, new, ts}` (from `notion-changelog.jsonl`).
- Produces: `find_unlanded_writes(decisions, changelog_rows) -> list[{item_id, title, page_id, field, to}]`; CLI `settle_reconcile.py --env-root <r> [--dry-run] [--writable <id> ...] [--no-context-log]` that flips each unlanded write and prints JSON `{auto_healed:[{item_id,title,page_id,field,to,receipt}], unlanded_found:int}`.

- [ ] **Step 1: Write the failing test (pure diff)**

```python
# engine/tools/tests/test_settle_reconcile.py
import settle_reconcile as sr

DECISIONS = [
    {"item_id": "OI-901", "title": "Pay tax", "executed": True,
     "notion_write": {"page_id": "p1", "field": "Status", "to": "Done"}, "ts": "2026-07-07T10:00:00Z"},
    {"item_id": "OI-902", "title": "Landed one", "executed": True,
     "notion_write": {"page_id": "p2", "field": "Status", "to": "Done"}, "ts": "2026-07-07T10:00:00Z"},
    {"item_id": "OI-903", "title": "No notion intent", "executed": True, "ts": "2026-07-07T10:00:00Z"},
    {"item_id": "OI-904", "title": "Not executed", "executed": False,
     "notion_write": {"page_id": "p4", "field": "Status", "to": "Done"}, "ts": "2026-07-07T10:00:00Z"},
]
CHANGELOG = [
    {"page_id": "p2", "field": "Status", "new": "Done", "ts": "2026-07-07T10:00:05Z"},  # OI-902 landed
]

def test_finds_only_unlanded_intended_executed_writes():
    out = sr.find_unlanded_writes(DECISIONS, CHANGELOG)
    ids = {r["item_id"] for r in out}
    assert ids == {"OI-901"}                 # OI-902 landed; OI-903 no intent; OI-904 not executed
    assert out[0] == {"item_id": "OI-901", "title": "Pay tax",
                      "page_id": "p1", "field": "Status", "to": "Done"}

def test_landed_requires_matching_value():
    cl = [{"page_id": "p1", "field": "Status", "new": "In Progress", "ts": "2026-07-07T11:00:00Z"}]
    out = sr.find_unlanded_writes([DECISIONS[0]], cl)   # wrote a DIFFERENT value -> still unlanded
    assert [r["item_id"] for r in out] == ["OI-901"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest engine/tools/tests/test_settle_reconcile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'settle_reconcile'`.

- [ ] **Step 3: Write the pure diff + CLI (mirror `resolve_sweep_task.py`)**

```python
# engine/tools/settle_reconcile.py
#!/usr/bin/env python3
"""settle_reconcile.py — deterministic auto-heal of brief decisions whose Notion write never landed.

A prior brief decision may record executed=True with an intended notion_write, yet the flip may
have failed to land (no changelog receipt). Those have a KNOWN target, so we replay them
(fix-then-tell). Inferred completions are NOT handled here — they wait for at-desk confirm.
"""
import argparse, json, os, subprocess, sys, time
import brief_session, context_log

STAGE = "settle"

def find_unlanded_writes(decisions, changelog_rows):
    """Executed decisions with a notion_write intent that has no matching (page_id, field, new) receipt."""
    landed = {(r.get("page_id"), r.get("field"), r.get("new")) for r in changelog_rows}
    out = []
    for d in decisions:
        if not d.get("executed"):
            continue
        nw = d.get("notion_write")
        if not nw:
            continue
        key = (nw.get("page_id"), nw.get("field"), nw.get("to"))
        if key not in landed:
            out.append({"item_id": d.get("item_id"), "title": d.get("title"),
                        "page_id": nw.get("page_id"), "field": nw.get("field"), "to": nw.get("to")})
    return out

def _plugin_root():
    return os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

def _flip(env_root, row, writable, change_log):
    cmd = [sys.executable, os.path.join(_plugin_root(), "engine", "tools", "notion_writeback.py"),
           "flip", "--page", row["page_id"], "--field", row["field"], "--to", row["to"],
           "--change-log", change_log, "--by", "aios-settle", "--run-id", time.strftime("%Y-%m-%d")]
    for w in writable:
        cmd += ["--writable", w]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return {"rc": p.returncode, "out": p.stdout.strip(), "err": p.stderr.strip()}

def run(env_root, decisions=None, changelog_rows=None, writable=None, dry_run=False):
    state = os.path.join(env_root, "state")
    if decisions is None:
        ledger = brief_session.load(os.path.join(state, "brief-session.json")) or {}
        decisions = ledger.get("decisions", [])
    if changelog_rows is None:
        cl = os.path.join(state, "notion-changelog.jsonl")
        changelog_rows = []
        if os.path.exists(cl):
            with open(cl, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        changelog_rows.append(json.loads(line))
    unlanded = find_unlanded_writes(decisions, changelog_rows)
    healed = []
    for row in unlanded:
        if dry_run:
            healed.append({**row, "receipt": None, "dry_run": True})
        else:
            res = _flip(env_root, row, writable or [], os.path.join(state, "notion-changelog.jsonl"))
            if res["rc"] == 0:
                healed.append({**row, "receipt": res["out"]})
    return {"auto_healed": healed, "unlanded_found": len(unlanded)}

def main(argv=None):
    ap = argparse.ArgumentParser(description="Deterministic settle auto-heal (unlanded brief writes).")
    ap.add_argument("--env-root", required=True)
    ap.add_argument("--writable", action="append", default=[])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-context-log", action="store_true")
    args = ap.parse_args(argv)
    res = run(args.env_root, writable=args.writable, dry_run=args.dry_run)
    if not args.no_context_log:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "stage": STAGE,
               "skill": "aios-settle", "healed": len(res["auto_healed"]),
               "unlanded_found": res["unlanded_found"]}
        try:
            context_log.emit(rec, os.path.join(args.env_root, "state", "context-log.jsonl"))
        except Exception:
            pass
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest engine/tools/tests/test_settle_reconcile.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Add a dry-run integration test (offline, no Notion)**

```python
def test_run_dry_run_offline(tmp_path):
    import os, json
    state = tmp_path / "state"; state.mkdir()
    (state / "brief-session.json").write_text(json.dumps({"decisions": DECISIONS}), encoding="utf-8")
    (state / "notion-changelog.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in CHANGELOG), encoding="utf-8")
    res = sr.run(str(tmp_path), dry_run=True)
    assert res["unlanded_found"] == 1
    assert res["auto_healed"][0]["item_id"] == "OI-901"
    assert res["auto_healed"][0]["dry_run"] is True
```
Run: `pytest engine/tools/tests/test_settle_reconcile.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add engine/tools/settle_reconcile.py engine/tools/tests/test_settle_reconcile.py
git commit -m "feat(settle): deterministic auto-heal of unlanded brief Notion writes"
```

### Task C2: `validate_cache` accepts the `settle` block

**Files:**
- Modify: `engine/tools/brief_session.py` — `validate_cache` (currently ends ~line 417)
- Test: `engine/tools/tests/test_brief_session.py` (append)

**Interfaces:**
- Consumes: a `brief-cache.json` dict that MAY carry `settle: {auto_healed:[...], candidates:[...]}`.
- Produces: `validate_cache` errors when `settle` is present but malformed (candidate missing `task_id`/`title`/`proposed_transition`, or a transition outside `{done, in_progress, due_rolled}`); absent `settle` stays valid (back-compat).

- [ ] **Step 1: Write the failing test**

```python
# append to engine/tools/tests/test_brief_session.py
import brief_session as bs

_MIN = {  # a minimal valid cache: adjust keys to match existing valid fixtures in this file
    "station_counts": {"system": 0, "personal": 0, "familyoffice": 0, "dev": 0},
    "stations": {"system": [], "personal": [], "familyoffice": [], "dev": []},
}

def test_settle_absent_is_valid():
    ok, errs = bs.validate_cache(dict(_MIN))
    assert ok, errs

def test_settle_valid_block_ok():
    c = dict(_MIN)
    c["settle"] = {"auto_healed": [], "candidates": [
        {"task_id": "OI-909", "title": "Ship page", "proposed_transition": "in_progress",
         "evidence": [{"source": "git", "ref": "abc123", "quote": "..."}], "confidence": "high", "domain": "dev"}]}
    ok, errs = bs.validate_cache(c)
    assert ok, errs

def test_settle_bad_transition_rejected():
    c = dict(_MIN)
    c["settle"] = {"candidates": [{"task_id": "X", "title": "Y", "proposed_transition": "cancelled"}]}
    ok, errs = bs.validate_cache(c)
    assert not ok
    assert any("proposed_transition" in e for e in errs)

def test_settle_candidate_missing_field_rejected():
    c = dict(_MIN)
    c["settle"] = {"candidates": [{"title": "no id", "proposed_transition": "done"}]}
    ok, errs = bs.validate_cache(c)
    assert not ok
    assert any("task_id" in e for e in errs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest engine/tools/tests/test_brief_session.py -k settle -v`
Expected: FAIL (bad-transition / missing-field cases pass validation because the block is unchecked).

- [ ] **Step 3: Add the settle check inside `validate_cache` (before `ok = len(errors) == 0`)**

```python
    # settle block (optional; when present, candidates must be well-formed)
    SETTLE_TRANSITIONS = {"done", "in_progress", "due_rolled"}
    settle = cache_obj.get("settle")
    if settle is not None:
        if not isinstance(settle, dict):
            errors.append("settle must be a dict")
        else:
            for idx, cand in enumerate(settle.get("candidates", []) or []):
                pfx = f"settle.candidates[{idx}]"
                if not isinstance(cand, dict):
                    errors.append(f"{pfx}: must be a dict"); continue
                for req in ("task_id", "title", "proposed_transition"):
                    if not cand.get(req):
                        errors.append(f"{pfx}: missing {req!r}")
                tr = cand.get("proposed_transition")
                if tr is not None and tr not in SETTLE_TRANSITIONS:
                    errors.append(f"{pfx}: proposed_transition {tr!r} not in {sorted(SETTLE_TRANSITIONS)}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest engine/tools/tests/test_brief_session.py -k settle -v && pytest engine/tools/tests/test_brief_session.py -v`
Expected: PASS (new settle tests pass; the whole file still green).

- [ ] **Step 5: Commit**

```bash
git add engine/tools/brief_session.py engine/tools/tests/test_brief_session.py
git commit -m "feat(brief): validate_cache accepts+checks the settle block"
```

### Task C3: `brief_render.py settle` op

**Files:**
- Modify: `engine/tools/brief_render.py` (add `render_settle`; wire into `main`, ~line 181)
- Test: `engine/tools/tests/test_brief_render.py` (append)

**Interfaces:**
- Consumes: a cache dict's `settle` block.
- Produces: `render_settle(cache) -> str` — a `✅ Healed N …` summary line per auto-heal, then candidates grouped by `proposed_transition` with a count header and sample titles; CLI op `brief_render.py settle <cache.json>` prints it.

- [ ] **Step 1: Write the failing test**

```python
# append to engine/tools/tests/test_brief_render.py
import brief_render as br

def test_render_settle_groups_and_heals():
    cache = {"settle": {
        "auto_healed": [{"item_id": "OI-901", "title": "Pay tax", "to": "Done"}],
        "candidates": [
            {"task_id": "S1", "title": "SEAMS 1065", "proposed_transition": "in_progress"},
            {"task_id": "S2", "title": "Ship page", "proposed_transition": "done"},
            {"task_id": "S3", "title": "Call vendor", "proposed_transition": "done"},
        ]}}
    out = br.render_settle(cache)
    assert "Healed" in out and "Pay tax" in out          # auto-heal reported
    assert "2× → done" in out                            # two 'done' candidates grouped
    assert "SEAMS 1065" in out                            # candidate surfaced

def test_render_settle_empty_is_clear():
    out = br.render_settle({"settle": {"auto_healed": [], "candidates": []}})
    assert "clear" in out.lower() or "nothing to settle" in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest engine/tools/tests/test_brief_render.py -k settle -v`
Expected: FAIL — `AttributeError: module 'brief_render' has no attribute 'render_settle'`.

- [ ] **Step 3: Implement `render_settle` and wire `main`**

```python
def render_settle(cache):
    s = (cache or {}).get("settle") or {}
    healed = s.get("auto_healed") or []
    cands = s.get("candidates") or []
    if not healed and not cands:
        return "**Stage 0 — Settle:** clear ✓ — nothing to settle."
    lines = ["**Stage 0 — Settle**", ""]
    for h in healed:
        lines.append(f"✅ Healed: {h.get('title')} → {h.get('to')}")
    if healed:
        lines.append("")
    groups = {}
    for c in cands:
        groups.setdefault(c.get("proposed_transition"), []).append(c.get("title"))
    for tr, titles in groups.items():
        sample = ", ".join(f'"{t}"' for t in titles[:3])
        lines.append(f"▸ {len(titles)}× → {tr}   e.g. {sample}    [Confirm all] [Expand]")
    return "\n".join(lines)
```
In `main`, add a branch mirroring the existing ops (e.g. alongside `station`/`overview`):
```python
    elif args.op == "settle":
        print(render_settle(_load(args.cache)))   # _load = however main reads the cache json today
```
(Match the exact arg/dispatch style already in `main` — read the existing `station` branch and copy its shape, including how it loads the cache file.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest engine/tools/tests/test_brief_render.py -k settle -v && pytest engine/tools/tests/test_brief_render.py -v`
Expected: PASS (new tests pass; file still green).

- [ ] **Step 5: Commit**

```bash
git add engine/tools/brief_render.py engine/tools/tests/test_brief_render.py
git commit -m "feat(brief): render_settle op — Stage-0 auto-heal + grouped candidate panel"
```

### Task C4: `record_decision` carries Notion write-intent + accepts the `settle` station

Closes the deterministic loop: when a brief decision implies a Notion write, the ledger records *what* was written so C1 can detect a miss. Also lets a `settle`-station decision be recorded.

**Files:**
- Modify: `engine/tools/brief_session.py` — `record_decision` (~line 177)
- Test: `engine/tools/tests/test_brief_session.py` (append)

**Interfaces:**
- Consumes: an optional `notion_write={"page_id","field","to"}` on a decision.
- Produces: `record_decision(..., notion_write=None)` stores the intent on the decision entry; a walk whose `station_order` includes `"settle"` accepts `record_decision(..., station="settle", ...)`.

- [ ] **Step 1: Write the failing test**

```python
def test_record_decision_stores_notion_write_intent(tmp_path):
    sp = str(tmp_path / "walk.json")
    bs.new_walk(sp, "w1", ["settle", "system"], {"settle": 1, "system": 0})
    bs.record_decision(sp, "OI-901", "Pay tax", "settle", "system", "flip Status=Done",
                       executed=True, notion_write={"page_id": "p1", "field": "Status", "to": "Done"})
    led = bs.load(sp)
    d = led["decisions"][0]
    assert d["station"] == "settle"
    assert d["notion_write"] == {"page_id": "p1", "field": "Status", "to": "Done"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest engine/tools/tests/test_brief_session.py -k notion_write -v`
Expected: FAIL — `TypeError: record_decision() got an unexpected keyword argument 'notion_write'`.

- [ ] **Step 3: Add the parameter (signature + entry + CLI)**

In `record_decision`, extend the signature and entry:
```python
def record_decision(state_path, item_id, title, station, choice, action,
                    executed, thread=None, notion_write=None):
    ...
    if thread is not None:
        entry["thread"] = thread
    if notion_write is not None:
        entry["notion_write"] = notion_write
```
Then find `record_decision`'s argparse subparser in `main` and add:
```python
    rd.add_argument("--notion-write", default=None,
                    help='JSON {"page_id","field","to"} — records the intended Notion flip')
```
and where it calls the function, parse it:
```python
    nw = json.loads(args.notion_write) if getattr(args, "notion_write", None) else None
    record_decision(..., notion_write=nw)
```
(No change needed for the `settle` station — `new_walk` builds `stations` from `station_order`, and `record_decision` already validates `station in ledger["stations"]`, so `"settle"` works once it's in the order.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest engine/tools/tests/test_brief_session.py -v`
Expected: PASS (new test passes; file still green).

- [ ] **Step 5: Commit**

```bash
git add engine/tools/brief_session.py engine/tools/tests/test_brief_session.py
git commit -m "feat(brief): record_decision records Notion write-intent; settle station"
```

### Task C5: Precompute body — run the reconcile pass, emit the `settle` block

**Files:**
- Modify: `deploy/tasks/brief-cache.md` (the precompute prompt body)
- Modify: `skills/brief/references/gather.md` (the `## Cache contract` section — document the `settle` block)

**Interfaces:**
- Consumes: `settle_reconcile.py` (C1), the cache contract (C2).
- Produces: the precompute writes a `settle` block into `brief-cache.json` (auto-healed from the script; inferred candidates from the model with cited evidence) and a `N settled · M to confirm` headline chip.

- [ ] **Step 1: Add the reconcile pass to the body**

In `deploy/tasks/brief-cache.md`, after the gather and before the cache write, add a `# Settle reconcile` section:
- Run the deterministic auto-heal (it flips + reports): `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/settle_reconcile.py" --env-root "<env_root>" --writable <each notion.write.writable id>` — capture its `auto_healed[]`.
- Build **inferred candidates**: for each open Notion task (from the live `notion_gather` leg), look for completion evidence in the day's session-capture records (`raw/sessions/`), recent git commits (read-only `git log` across the repos), the walk-ledger, and Drive/dataroom signals. Emit a candidate ONLY with a concrete cited anchor: `{task_id, title, proposed_transition (done|in_progress|due_rolled), evidence:[{source,ref,quote}], confidence, domain}`. **Never flip inferred candidates here** — they wait for at-desk confirm.
- Write both into `brief-cache.json` under `settle: {auto_healed, candidates}`; add the `N settled · M to confirm` chip to `headline_bubbles`.

- [ ] **Step 2: Document the block in the cache contract**

In `skills/brief/references/gather.md` `## Cache contract`, add the `settle` block shape (mirror the spec's JSON) and note `validate_cache` now checks it.

- [ ] **Step 3: Validate a hand-authored sample cache passes**

Add `settle` to `engine/tools/tests/fixtures/brief-cache.sample.json` and run:
Run: `python -c "import json,sys; sys.path.insert(0,'engine/tools'); import brief_session as b; ok,e=b.validate_cache(json.load(open('engine/tools/tests/fixtures/brief-cache.sample.json'))); print(ok,e)"`
Expected: `True []`.

- [ ] **Step 4: Commit**

```bash
git add deploy/tasks/brief-cache.md skills/brief/references/gather.md engine/tools/tests/fixtures/brief-cache.sample.json
git commit -m "feat(brief): precompute runs settle reconcile + emits settle block"
```

### Task C6: Brief SKILL — Stage-0 settle station (render + act) + walk order

**Files:**
- Modify: `skills/brief/SKILL.md` (walk order, a `# Stage 0 — Settle` section, and the write-back step)

**Interfaces:**
- Consumes: `brief_render.py settle` (C3), `record_decision --notion-write` (C4), `notion_writeback.py flip`.
- Produces: the at-desk brief renders Stage 0 first, confirms flip Notion and are recorded with write-intent.

- [ ] **Step 1: Prepend `settle` to every walk order**

In `skills/brief/SKILL.md` `# Scope` (the `--order`/`--seed` station tokens) and `# Stationed walk` (station order), prepend `settle`: root becomes `settle,kb,system,personal,familyoffice,dev`; FO `settle,kb,familyoffice`; Personal `settle,kb,personal`; Dev `settle,kb,system,dev`. An empty settle station renders "nothing to settle."

- [ ] **Step 2: Add the `# Stage 0 — Settle` section**

Document: read the cache's `settle` block; render it by lifting `brief_render.py settle <cache>` **verbatim** (never hand-compose); auto-heals are already done (report the `✅ Healed` lines); for candidates, offer per-group **Confirm all** and per-row **Confirm / Adjust / Skip**. On **Confirm** → `notion_writeback.py flip --page <id> --field Status --to <transition-mapped value>` (map `done→Done`, `in_progress→In Progress`; `due_rolled→` a `Due` date flip), then `record_decision … settle … --notion-write '{"page_id","field","to"}'` (so the deterministic pass can verify it landed next run). When cleared, `advance` to the KB station. Economic content still refuses by type — surface such a task as a normal card, not a settle.

- [ ] **Step 3: Verify the render op is callable end-to-end**

Run: `python engine/tools/brief_render.py settle engine/tools/tests/fixtures/brief-cache.sample.json`
Expected: prints the Stage-0 panel (auto-heal line(s) + grouped candidates) with no traceback.

- [ ] **Step 4: Commit**

```bash
git add skills/brief/SKILL.md
git commit -m "feat(brief): Stage-0 settle station — render, confirm, write-back, walk order"
```

---

## Cleanup

### Task D1: Fix the gate SKILL's misleading header

**Files:**
- Modify: `skills/gate/SKILL.md` (line 8)

- [ ] **Step 1: Correct the claim**

`ship.py` writes the vault + revert pointer + queue flip, never Notion (`gate-auto` says "nothing under Notion/Drive"). Change line 8 from "durable wiki/Notion truth" to "durable **wiki** truth".

- [ ] **Step 2: Verify no other line claims gate writes Notion**

Run: `grep -ni "notion" skills/gate/SKILL.md`
Expected: no remaining line asserting the gate writes Notion task state (references to *holding* economic items for approval are fine).

- [ ] **Step 3: Commit**

```bash
git add skills/gate/SKILL.md
git commit -m "docs(gate): header says wiki truth — ship.py never writes Notion"
```

---

## Final verification (after all tasks)

- [ ] Run the full tool test suite: `pytest engine/tools/tests/ -v` — expected: all green.
- [ ] Render smoke test: `python engine/tools/brief_render.py settle engine/tools/tests/fixtures/brief-cache.sample.json` — expected: Stage-0 panel.
- [ ] Dry-run reconcile: `python engine/tools/settle_reconcile.py --env-root <env_root> --dry-run --no-context-log` — expected: JSON `{auto_healed, unlanded_found}`, no writes.
- [ ] Confirm the manifest is enabled and parses: `python -c "import json; m=json.load(open('deploy/tasks.manifest.json')); print([t['id'] for t in m['tasks'] if t['id'] in ('aios-brief-cache','aios-resolve-sweep') and t['enabled']])"` — expected: both ids.
- [ ] **Morning-after (manual, next day):** `brief-headline.md` mtime advanced overnight; the brief opens with a Stage-0 settle pass; after confirming a candidate, `notion-changelog.jsonl` shows the flip and the next `resolve-sweep` reports a non-zero delta.

## Self-review notes (spec coverage)

- Spec §4 Phase A → Tasks A1, A2. Phase B → B1, B2. Phase C reconciler → C1; cache contract → C2; render → C3; write-intent + settle station accept → C4; precompute pass → C5; Stage-0 render/act → C6. §5 safety → enforced by `notion_writeback` fences (unchanged) + the fail-loud boundary (C1 deterministic-only, C5 "never flip inferred here"). §6 tests → A2, C1, C2, C3, C4 have unit tests; §7 cleanups → D1 (gate header) + B1 (manifest flags). §8 YAGNI honored (no source-hooks, no gate→Notion, no vector matching).
- Open build-time verification (flagged in-task, not gaps): A1/A2 runner location (aios vs Scripts); B2 token presence; C3 `main`-dispatch exact shape; C5 inferred-matching is model behavior (only its cache-contract output is unit-checkable).
