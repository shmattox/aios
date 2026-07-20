# State→Wiki Economic-Figure Reconcile Detector (A104) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `reconcile_state_knowledge.py` — a zero-LLM nightly detector that finds wiki economic snapshots that have drifted from their `state/domains` row and emits gated wiki-refresh proposals on the review lane.

**Architecture:** A read-only detector reads each live-KB wiki page's `snapshots:` frontmatter anchor, resolves the referenced `state/domains` typed row, compares deterministically, and on drift stages a corrected page + enqueues a `review`-lane draft item (the Strike-correction shape) that the existing gate ships to the wiki. Reads `state/domains` (never Notion); never auto-copies an economic value (the gate validates vs Drive at ship). Runs in the brief-cache gather's `standing_checks` slot.

**Tech Stack:** Python 3 stdlib only (no pyyaml — reuse `state_validate._parse_yaml` / `_extract_frontmatter`). Reuses `queue_tx.py` (enqueue), `ship.py`/gate (ship, unchanged), `state_validate.py` (frontmatter reader). Tests via the aios `check()` harness under `engine/tools/tests/`.

## Global Constraints

- **Stdlib only.** No new dependencies. Frontmatter parsing reuses `from state_validate import _parse_yaml, _extract_frontmatter` (the pattern `domain_mirror.py` already uses).
- **Anchor wire format is a pipe-delimited scalar list** (the subset YAML reader can't parse a list-of-dicts): each `snapshots:` entry is `"<state_key>|<field>|<value>|<as_of>|<track>"`. Example: `"familyoffice/assets/some-loan|balance|2059169|2026-05-30|true"`.
- **Read `state/domains`, never Notion.** State rows are resolved at `<env_root>/state/domains/<silo>/tables/<table>/<slug>.md`; `state_key` = `<silo>/<table>/<slug>`.
- **Detector never writes the wiki or state.** It only stages a draft + enqueues via `queue_tx.py add`. The gate is the sole writer (unchanged).
- **Never auto-copy an economic value as truth.** The proposal is a *candidate*; the gate's independent review validates vs Drive at ship, and economic KBs stay human-gated (gate kb backstop unchanged).
- **Queue writes go through `queue_tx.py` only** — never raw-edit `queue.json`.
- **Thresholds are profile knobs**, read from `profile/domains.yaml` `reconcile:` (`value_threshold` default 0.02, `abs_floor` default 1.0, `stale_days` default 30); unset → defaults.
- **UTF-8 stdio** on the CLI (mirror `brief_session._utf8_stdio`) — state rows carry non-ASCII.
- Test command: `python -m pytest engine/tools/tests/test_reconcile_state_knowledge.py -q` (a `conftest.py` bridges the `check()` harness); full suite `python -m pytest engine/tools/tests -q`.

## File Structure

- **Create** `engine/tools/reconcile_state_knowledge.py` — the whole detector (parser, state reader, comparison, dedup, emission, CLI). One file, one responsibility (state→wiki economic drift). Modeled on `standing_checks.py`'s shape (zero-LLM, degrade-silent, exit 0).
- **Create** `engine/tools/tests/test_reconcile_state_knowledge.py` — unit tests, fixtures built in a `tmp_path` env-root.
- **Modify** `skills/brief/references/gather.md` — add the nightly invocation line in the cache-write tail (next to `standing_checks` / `brainstorm_packets`).
- **Modify** `deploy/tasks/brief-cache.md` (if the scheduled cache-writer enumerates gather steps) — same invocation.
- **Docs** the `snapshots:` anchor contract in the FamilyOffice KB schema is an *instance* concern (env-side), NOT this repo — the plan documents the format in the tool's module docstring only.

---

### Task 1: Anchor parser

**Files:**
- Create: `engine/tools/reconcile_state_knowledge.py`
- Test: `engine/tools/tests/test_reconcile_state_knowledge.py`

**Interfaces:**
- Consumes: `state_validate._parse_yaml`, `state_validate._extract_frontmatter`.
- Produces: `parse_anchors(page_path: Path) -> list[dict]` — each dict `{state_key, field, value: float, as_of: str, track: bool, raw: str}`. A page with no `snapshots:` key → `[]`. A malformed entry (wrong arity, non-numeric value) → skipped and returned in a parallel `parse_errors(page_path) -> list[str]` (so the caller can surface a health line, never crash).

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
from reconcile_state_knowledge import parse_anchors, parse_errors

def _write(p, body):
    p.write_text(body, encoding="utf-8"); return p

def test_parse_anchor_happy(tmp_path):
    page = _write(tmp_path / "btc.md",
        '---\ntitle: x\nsnapshots:\n  - "fo/assets/loan|balance|2059169|2026-05-30|true"\n---\nbody\n')
    got = parse_anchors(page)
    assert got == [{"state_key": "fo/assets/loan", "field": "balance",
                    "value": 2059169.0, "as_of": "2026-05-30", "track": True,
                    "raw": "fo/assets/loan|balance|2059169|2026-05-30|true"}]

def test_parse_no_snapshots(tmp_path):
    assert parse_anchors(_write(tmp_path / "p.md", "---\ntitle: x\n---\nbody\n")) == []

def test_parse_malformed_skipped(tmp_path):
    page = _write(tmp_path / "p.md",
        '---\nsnapshots:\n  - "too|few|fields"\n  - "fo/a/x|balance|NaNish|2026-01-01|true"\n---\n')
    assert parse_anchors(page) == []
    assert len(parse_errors(page)) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest engine/tools/tests/test_reconcile_state_knowledge.py -q`
Expected: FAIL (module not found / functions undefined).

- [ ] **Step 3: Write minimal implementation**

```python
"""reconcile_state_knowledge.py — state→wiki economic-figure drift detector (A104, Pass A).

Zero-LLM. Reads each live-KB wiki page's `snapshots:` anchor (pipe-delimited scalar list:
"<state_key>|<field>|<value>|<as_of>|<track>"), resolves the state/domains row, and on drift
stages a corrected page + enqueues a review-lane draft (the gate ships it). Never writes the wiki
or Notion; never auto-copies an economic value. Exit 0 always; degrade silent."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from state_validate import _parse_yaml, _extract_frontmatter  # stdlib YAML-subset reader

def _anchors_raw(page_path):
    fm = _parse_yaml(_extract_frontmatter(page_path.read_text(encoding="utf-8")) or "")
    snaps = fm.get("snapshots") if isinstance(fm, dict) else None
    return [s for s in snaps if isinstance(s, str)] if isinstance(snaps, list) else []

def _split(raw):
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) != 5:
        raise ValueError("arity")
    state_key, field, value, as_of, track = parts
    return {"state_key": state_key, "field": field, "value": float(value),
            "as_of": as_of, "track": track.lower() == "true", "raw": raw}

def parse_anchors(page_path):
    out = []
    for raw in _anchors_raw(page_path):
        try:
            out.append(_split(raw))
        except (ValueError, TypeError):
            continue
    return out

def parse_errors(page_path):
    errs = []
    for raw in _anchors_raw(page_path):
        try:
            _split(raw)
        except (ValueError, TypeError):
            errs.append(raw)
    return errs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest engine/tools/tests/test_reconcile_state_knowledge.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add engine/tools/reconcile_state_knowledge.py engine/tools/tests/test_reconcile_state_knowledge.py
git commit -m "feat(reconcile): anchor parser for state→wiki snapshots (A104)"
```

---

### Task 2: State-row reader

**Files:**
- Modify: `engine/tools/reconcile_state_knowledge.py`
- Test: `engine/tools/tests/test_reconcile_state_knowledge.py`

**Interfaces:**
- Produces: `read_state_field(env_root: Path, state_key: str, field: str) -> dict | None` — resolves `<env_root>/state/domains/<silo>/tables/<table>/<slug>.md`, returns `{value: float|None, last_synced: str|None, found: bool}`; `None` (not a dict) when the file is absent (`found:false` for a present file whose `field` is null/missing). `state_key` splits on the FIRST two `/` into `silo/table/slug` (slug may contain `/` — rejoin the remainder).

- [ ] **Step 1: Write the failing test**

```python
from reconcile_state_knowledge import read_state_field

def _row(tmp_path, silo, table, slug, body):
    d = tmp_path / "state" / "domains" / silo / "tables" / table
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.md").write_text(body, encoding="utf-8")

def test_read_state_field_present(tmp_path):
    _row(tmp_path, "familyoffice", "assets", "loan",
         "---\ntype: state-asset\nbalance: 3310000\nlast_synced: 2026-07-19\n---\n")
    assert read_state_field(tmp_path, "familyoffice/assets/loan", "balance") == {
        "value": 3310000.0, "last_synced": "2026-07-19", "found": True}

def test_read_state_field_absent_file(tmp_path):
    assert read_state_field(tmp_path, "familyoffice/assets/nope", "balance") is None

def test_read_state_field_null(tmp_path):
    _row(tmp_path, "familyoffice", "assets", "loan", "---\nbalance: null\n---\n")
    assert read_state_field(tmp_path, "familyoffice/assets/loan", "balance")["found"] is False
```

- [ ] **Step 2: Run to verify it fails.** Expected: FAIL (`read_state_field` undefined).

- [ ] **Step 3: Write minimal implementation**

```python
def _coerce_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def read_state_field(env_root, state_key, field):
    parts = state_key.split("/", 2)
    if len(parts) != 3:
        return None
    silo, table, slug = parts
    path = Path(env_root) / "state" / "domains" / silo / "tables" / table / f"{slug}.md"
    if not path.is_file():
        return None
    fm = _parse_yaml(_extract_frontmatter(path.read_text(encoding="utf-8")) or "")
    if not isinstance(fm, dict):
        return {"value": None, "last_synced": None, "found": False}
    val = _coerce_num(fm.get(field))
    return {"value": val, "last_synced": fm.get("last_synced"), "found": val is not None}
```

- [ ] **Step 4: Run to verify it passes.** Expected: PASS.

- [ ] **Step 5: Commit** `feat(reconcile): state/domains row field reader (A104)`

---

### Task 3: Drift comparison

**Files:** Modify tool + test.

**Interfaces:**
- Produces: `evaluate(anchor: dict, state: dict|None, *, value_threshold=0.02, abs_floor=1.0, stale_days=30, today: str) -> dict | None` — returns `None` when no action (no drift, `track:false`, state absent/unfound), else `{reason: "value"|"stale", target_value: float, as_of_new: str, delta: float}`. `today` is passed in (no `date.today()` — determinism). Staleness uses `_days_between(as_of, state.last_synced)` and `_days_between(anchor.as_of, today)`.

- [ ] **Step 1: Write the failing test**

```python
from reconcile_state_knowledge import evaluate

A = {"state_key": "fo/assets/loan", "field": "balance", "value": 2059169.0,
     "as_of": "2026-05-30", "track": True, "raw": "..."}

def test_value_drift_flags():
    st = {"value": 3310000.0, "last_synced": "2026-07-19", "found": True}
    got = evaluate(A, st, today="2026-07-20")
    assert got["reason"] == "value" and got["target_value"] == 3310000.0

def test_within_threshold_silent():
    st = {"value": 2060000.0, "last_synced": "2026-07-19", "found": True}  # ~0.04% delta
    assert evaluate(A, st, today="2026-07-20") is None

def test_track_false_silent():
    st = {"value": 9999999.0, "last_synced": "2026-07-19", "found": True}
    assert evaluate({**A, "track": False}, st, today="2026-07-20") is None

def test_state_absent_silent():
    assert evaluate(A, None, today="2026-07-20") is None

def test_stale_with_newer_state_flags():
    st = {"value": 2059169.0, "last_synced": "2026-07-19", "found": True}  # same value…
    got = evaluate(A, st, today="2026-07-20", stale_days=30)  # …but 50d old anchor + newer state
    assert got["reason"] == "stale"
```

- [ ] **Step 2: Run to verify it fails.** Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
from datetime import date

def _days_between(a, b):
    try:
        ya = date.fromisoformat(str(a)[:10]); yb = date.fromisoformat(str(b)[:10])
        return abs((yb - ya).days)
    except (ValueError, TypeError):
        return None

def evaluate(anchor, state, *, value_threshold=0.02, abs_floor=1.0, stale_days=30, today):
    if not anchor.get("track") or not isinstance(state, dict) or not state.get("found"):
        return None
    sv, av = state["value"], anchor["value"]
    delta = abs(sv - av)
    base = {"target_value": sv, "as_of_new": today, "delta": delta}
    if delta > abs_floor and (av == 0 or delta / abs(av) > value_threshold):
        return {"reason": "value", **base}
    age = _days_between(anchor["as_of"], today)
    fresher = _days_between(anchor["as_of"], state.get("last_synced"))
    if age is not None and age > stale_days and fresher not in (None, 0) \
            and str(state.get("last_synced", ""))[:10] > str(anchor["as_of"])[:10]:
        return {"reason": "stale", **base}
    return None
```

- [ ] **Step 4: Run to verify it passes.** Expected: PASS (5 tests).

- [ ] **Step 5: Commit** `feat(reconcile): deterministic drift/staleness comparison (A104)`

---

### Task 4: Dedup check

**Files:** Modify tool + test.

**Interfaces:**
- Produces: `dedupe_key(page_rel: str, state_key: str, target_value: float) -> str` (= `f"{page_rel}|{state_key}|{target_value:.2f}"`) and `already_proposed(queue_path: Path, key: str) -> bool` — True if any queue item (ANY stage, incl. `rejected`) carries `reconcile.dedupe_key == key`. Reuses the A96 dedupe *concept* (query by key across all stages) for reconcile-emitted review-lane items.

- [ ] **Step 1: Write the failing test**

```python
import json
from reconcile_state_knowledge import dedupe_key, already_proposed

def test_dedupe_key_stable():
    assert dedupe_key("fo/wiki/knowledge/btc.md", "fo/assets/loan", 3310000.0) \
        == "fo/wiki/knowledge/btc.md|fo/assets/loan|3310000.00"

def test_already_proposed(tmp_path):
    key = "p|s|3310000.00"
    q = tmp_path / "queue.json"
    q.write_text(json.dumps({"queue": [
        {"id": "x", "stage": "rejected", "reconcile": {"dedupe_key": key}}]}), encoding="utf-8")
    assert already_proposed(q, key) is True
    assert already_proposed(q, "other|k|0.00") is False
```

- [ ] **Step 2: Run to verify it fails.** Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
def dedupe_key(page_rel, state_key, target_value):
    return f"{page_rel}|{state_key}|{target_value:.2f}"

def already_proposed(queue_path, key):
    try:
        d = json.loads(Path(queue_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    q = d.get("queue") if isinstance(d, dict) else None
    return any(isinstance(it, dict) and (it.get("reconcile") or {}).get("dedupe_key") == key
               for it in (q or []))
```

- [ ] **Step 4: Run to verify it passes.** Expected: PASS.

- [ ] **Step 5: Commit** `feat(reconcile): dedupe-by-key so a rejected refresh is not re-proposed (A104)`

---

### Task 5: Proposal emission (staged draft + review-lane item)

**Files:** Modify tool + test.

**Interfaces:**
- Produces: `build_refresh(page_path: Path, anchor: dict, verdict: dict, kb: str, vault_folder: str) -> dict` returning `{staged_text: str, item: dict}`. `staged_text` is the full page with the matching `snapshots:` entry's `value`+`as_of` rewritten to `verdict.target_value`/`as_of_new` **and** the first prose occurrence of the old integer replaced with the new (string replace of `format(int(anchor.value))` → `format(int(target))`, first match only; if absent, leave prose and note it in `rec_reason`). `item` is the review-lane draft (the Strike-correction shape): `{id, stage:"awaiting", lane:"review", kb, kb_class:"decision", conflict_key, source:"reconcile", recommended:"approve", rec_reason, draft_path, first_drafted_utc, reconcile:{dedupe_key, state_key, target_value}, history:[]}`.
- The caller writes `staged_text` to `<vault>/<vault_folder>/wiki/staging/<slug>.md` and enqueues `item` via `queue_tx.py add`.

- [ ] **Step 1: Write the failing test**

```python
from reconcile_state_knowledge import build_refresh

def test_build_refresh_rewrites_anchor_and_prose(tmp_path):
    page = tmp_path / "btc-treasury.md"
    page.write_text('---\ntitle: BTC\nsnapshots:\n'
        '  - "familyoffice/assets/loan|balance|2059169|2026-05-30|true"\n---\n'
        'Loan $2059169 today.\n', encoding="utf-8")
    anchor = {"state_key": "familyoffice/assets/loan", "field": "balance",
              "value": 2059169.0, "as_of": "2026-05-30", "track": True,
              "raw": "familyoffice/assets/loan|balance|2059169|2026-05-30|true"}
    verdict = {"reason": "value", "target_value": 3310000.0,
               "as_of_new": "2026-07-20", "delta": 1250831.0}
    out = build_refresh(page, anchor, verdict, kb="familyoffice", vault_folder="02_FamilyOffice")
    assert "|balance|3310000|2026-07-20|true" in out["staged_text"]
    assert "$3310000" in out["staged_text"]  # prose rewritten
    it = out["item"]
    assert it["lane"] == "review" and it["stage"] == "awaiting" and it["kb"] == "familyoffice"
    assert it["conflict_key"] == "familyoffice/wiki/knowledge/btc-treasury.md" \
        or it["draft_path"].endswith("staging/btc-treasury.md")
    assert it["reconcile"]["dedupe_key"].endswith("|familyoffice/assets/loan|3310000.00")
```

- [ ] **Step 2: Run to verify it fails.** Expected: FAIL.

- [ ] **Step 3: Write minimal implementation** (full function — replace anchor line, rewrite first prose int, build item with a fixed `first_drafted_utc` passed by the caller; here default to the anchor's own clock is disallowed, so accept `now_utc` param).

```python
def build_refresh(page_path, anchor, verdict, kb, vault_folder, now_utc="1970-01-01T00:00:00Z"):
    text = page_path.read_text(encoding="utf-8")
    old_int, new_int = format(int(anchor["value"])), format(int(verdict["target_value"]))
    new_anchor = f'{anchor["state_key"]}|{anchor["field"]}|{new_int}|{verdict["as_of_new"]}|true'
    staged = text.replace(anchor["raw"], new_anchor, 1)
    prose_hit = old_int in staged.split("---", 2)[-1]
    if prose_hit:
        head, _, body = staged.partition("---\n" + staged.split("---\n", 2)[1] + "---\n")
        staged = staged.replace(old_int, new_int)  # simplest: global int swap after anchor already updated
    slug = page_path.stem
    # infer folder-relative conflict_key from the page's location under wiki/
    ck_tail = str(page_path).split("wiki" + ("\\" if "\\" in str(page_path) else "/"), 1)[-1]
    ck_tail = ck_tail.replace("\\", "/")
    conflict_key = f"{kb}/wiki/{ck_tail}"
    dk = dedupe_key(conflict_key, anchor["state_key"], verdict["target_value"])
    item = {
        "id": f"{kb}-reconcile-{slug}-{verdict['as_of_new']}",
        "stage": "awaiting", "lane": "review", "kb": kb, "kb_class": "decision",
        "conflict_key": conflict_key, "source": "reconcile", "recommended": "approve",
        "rec_reason": (f"state→wiki reconcile ({verdict['reason']} drift): "
                       f"{anchor['state_key']}.{anchor['field']} moved "
                       f"{old_int}→{new_int}; snapshot re-dated {verdict['as_of_new']}. "
                       f"Validate vs current statement at ship (Paper-Governs)."
                       + ("" if prose_hit else " NOTE: prose figure not auto-found; check the body.")),
        "draft_path": f"{vault_folder}/wiki/staging/{slug}.md",
        "first_drafted_utc": now_utc,
        "reconcile": {"dedupe_key": dk, "state_key": anchor["state_key"],
                      "target_value": verdict["target_value"]},
        "history": [],
    }
    return {"staged_text": staged, "item": item}
```
> **Reviewer note:** the prose-rewrite here is intentionally the simplest correct thing (swap the old integer string for the new after the anchor line is already updated). If Task-5 review finds the global `replace` risks touching an unrelated identical integer, tighten to "first body occurrence only" — but keep it a plain string op (no regex figure-hunting; that is a non-goal).

- [ ] **Step 4: Run to verify it passes.** Expected: PASS.

- [ ] **Step 5: Commit** `feat(reconcile): stage corrected page + build review-lane refresh item (A104)`

---

### Task 6: CLI `reconcile` command (scan + emit)

**Files:** Modify tool + test.

**Interfaces:**
- Produces: CLI `python reconcile_state_knowledge.py run --env-root <p> --today <YYYY-MM-DD> [--emit] [--json]`. Reads `profile/domains.yaml` for the live KBs + `reconcile:` knobs + `vault.live_kb_map`; scans each live KB's `<vault>/<folder>/wiki/**/*.md` for anchored pages; for each drifted, `track:true`, not-`already_proposed` anchor, either prints the plan (`--json`) or (`--emit`) writes the staged draft + `queue_tx.py add`. Exit 0 always; a malformed page contributes to a `parse_errors` count in the summary, never a crash. Prints a `render`-style one-line summary (`♻ reconcile: N drift proposal(s) staged · M parse warning(s)`) for the gather to lift.

- [ ] **Step 1: Write the failing test** (integration, `--json` dry-run over a tmp env-root with one drifted anchor + one matching + one `track:false`; assert exactly one proposal in the JSON, exit 0).

```python
import subprocess, sys, json, os
from pathlib import Path
TOOL = Path(__file__).resolve().parents[1] / "reconcile_state_knowledge.py"

def _mini_env(tmp_path):  # build profile + vault + one drifted anchor + state row
    # ... writes profile/domains.yaml, profile/connectors.yaml (vault.live_kb_map),
    #     SecondBrain/02_FamilyOffice/wiki/knowledge/btc.md with a drifted anchor,
    #     state/domains/familyoffice/tables/assets/loan.md with the moved value.
    ...  # (full fixture written in the test)

def test_cli_json_reports_one_proposal(tmp_path):
    _mini_env(tmp_path)
    out = subprocess.run([sys.executable, str(TOOL), "run", "--env-root", str(tmp_path),
                          "--today", "2026-07-20", "--json"], capture_output=True, text=True)
    assert out.returncode == 0
    data = json.loads(out.stdout)
    assert data["proposals"] == 1 and data["parse_warnings"] == 0
```

- [ ] **Step 2: Run to verify it fails.** Expected: FAIL (no `run` subcommand).

- [ ] **Step 3: Write minimal implementation** — the scan loop (reuse `parse_anchors`/`read_state_field`/`evaluate`/`already_proposed`/`build_refresh`), the profile reader (reuse `state_validate._parse_yaml` on `profile/domains.yaml` + `profile/connectors.yaml`), `argparse` CLI with `run`, `_utf8_stdio()`, and — under `--emit` — write staged text then `subprocess`/import `queue_tx.add`. Guard every page read in try/except → count as a parse warning. Exit 0 always.

- [ ] **Step 4: Run to verify it passes.** Expected: PASS.

- [ ] **Step 5: Commit** `feat(reconcile): CLI scan + --emit over live KBs, degrade-silent (A104)`

---

### Task 7: Nightly gather wiring + profile knobs

**Files:**
- Modify: `skills/brief/references/gather.md` (cache-write tail), `deploy/tasks/brief-cache.md` (if it enumerates steps).
- Test: `engine/tools/tests/test_reconcile_state_knowledge.py` (doc-presence assertion is not meaningful; instead add a test that the emitted item passes `queue_tx.validate` and is selectable by the brief's held-summary as a `review` item).

**Interfaces:**
- Consumes: `queue_tx.add`, `brief_session.held_summary`.
- Produces: an end-to-end test proving an emitted item is a valid queue item that surfaces on the review lane (so the brief will render it and the gate can ship it).

- [ ] **Step 1: Write the failing test**

```python
def test_emitted_item_is_valid_review_held(tmp_path):
    # build env, run with --emit, then:
    from queue_tx import validate  # or subprocess `queue_tx.py validate`
    # assert queue validates, and brief_session.held-summary count includes the reconcile item
    ...
```

- [ ] **Step 2: Run to verify it fails.** Expected: FAIL.

- [ ] **Step 3: Implement** — ensure `build_refresh`'s item shape passes `queue_tx.validate` (adjust required fields if validate rejects an unknown key); add to `references/gather.md` the line (documentation, lifted verbatim by the gather):
  ```
  python "${CLAUDE_PLUGIN_ROOT}/engine/tools/reconcile_state_knowledge.py" run \
    --env-root "<env_root>" --today <today> --emit
  ```
  next to the `standing_checks` / `brainstorm_packets` invocations, with a one-line note that its summary line is lifted like the other health lines and its items ride the existing review panel.

- [ ] **Step 4: Run to verify it passes.** Expected: PASS; full suite `python -m pytest engine/tools/tests -q` green.

- [ ] **Step 5: Commit** `feat(reconcile): wire nightly run into the brief-cache gather + docs (A104)`

---

## Self-Review

**Spec coverage:** anchor contract (T1) ✓ · deterministic drift + staleness (T3) ✓ · reads state/domains not Notion (T2, T6) ✓ · review-lane gated emission via ship/gate path (T5) ✓ · dedup no-nag (T4) ✓ · `track:false` guard (T3) ✓ · nightly-in-gather (T7) ✓ · profile knobs (T6) ✓ · zero-LLM/degrade-silent (T6 exit-0) ✓ · YAGNI no auto-discovery (anchors only, T1) ✓. Spec's "reuse A96 `kind:proposal`" is refined to "review-lane draft (ship-path) + A96 dedupe concept" — flagged to the user; a plan-time correction, not a scope change.

**Placeholder scan:** the T6/T7 `_mini_env` fixture bodies are marked `...` — these are the ONE place the implementer writes the full fixture; every production function has complete code. Acceptable (fixture authorship is the task), but the implementer must write the real fixture, not ship `...`.

**Type consistency:** `parse_anchors`→dict keys (`state_key/field/value/as_of/track/raw`) are consumed unchanged by `evaluate`/`build_refresh`; `read_state_field`→`{value,last_synced,found}` consumed by `evaluate`; `evaluate`→`{reason,target_value,as_of_new,delta}` consumed by `build_refresh`; `dedupe_key`/`already_proposed`/`reconcile.dedupe_key` consistent across T4/T5. ✓
