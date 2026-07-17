# Brief Freshness + Actionability (A93) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The brief stops trusting a morning cache all day — event-based staleness, auto-clearing of completed deferrals, an enforced live held panel, a visible movement line, and delta-gated health lines.

**Architecture:** Every change extends an existing engine tool (`brief_session.py`, `brief_render.py`, `pipeline_health.py`) plus prose wiring in `skills/brief/SKILL.md` and `skills/brief/references/gather.md`. No new files except tests. Spec: `docs/superpowers/specs/2026-07-17-brief-freshness-actionability-design.md`.

**Tech Stack:** Python 3 stdlib only (json, time, calendar, hashlib), pytest.

## Global Constraints

- Tools stay **fact-free and offline-testable**: no profile reads, no network calls inside the tools; the caller passes paths/values (e.g. the Notion watermark arrives as `--notion-watermark <iso>`, never queried by the tool).
- All state writes go through `brief_session._atomic_write` (write → re-read → verify → retry ×3).
- Timestamps are ISO-8601 `YYYY-MM-DDTHH:MM:SSZ` UTC; parse with the existing `calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))` pattern.
- Renderer output is lifted verbatim by the skill (Invariant 2) — every new line format is emitted by the engine, never composed in prose.
- Surgical diffs: match existing style (no argparse — these tools hand-parse `sys.argv`; keep that).
- Run the suite from `Projects/aios`: `python -m pytest -q` must exit 0 after every task.
- Commit after every task with a real message; the repo is public — no instance names (people, holdings, counterparties) in code, tests, fixtures, or messages.

---

### Task 1: Event-based staleness in `cache_status` (spec §1)

**Files:**
- Modify: `engine/tools/brief_session.py:602-629` (`cache_status`) and the `cache-status` CLI arm at `:911-932`
- Test: `engine/tools/tests/test_brief_session.py` (append)

**Interfaces:**
- Consumes: existing `cache_status(cache_path, max_age_min, notion_enabled, session_has_notion, now_epoch)` and `_read_json`.
- Produces: `cache_status(..., session_path=None, changelog_path=None, notion_watermark=None)` returning the existing dict plus `"signals": {name: epoch}` and `"event_stale": bool`; module-level helper `_iso_epoch(s) -> float` (0.0 on unparseable). Later tasks and the SKILL rely on the CLI flags `--session <path>`, `--changelog <path>`, `--notion-watermark <iso>`.

- [ ] **Step 1: Write the failing tests** (append to `engine/tools/tests/test_brief_session.py`; follow the file's existing import style — it imports the module from `engine/tools`):

```python
def _write_cache(tmp_path, generated_utc):
    p = tmp_path / "brief-cache.json"
    p.write_text(json.dumps({"generated_utc": generated_utc,
                             "source_counts": {"notion_live": True}}), encoding="utf-8")
    return str(p)


def test_cache_status_stale_when_walk_ledger_newer(tmp_path):
    cache = _write_cache(tmp_path, "2026-07-17T11:00:00Z")
    sess = tmp_path / "brief-session.json"
    sess.write_text(json.dumps({"updated_utc": "2026-07-17T15:39:00Z"}), encoding="utf-8")
    st = brief_session.cache_status(cache, now_epoch=brief_session._iso_epoch("2026-07-17T16:00:00Z"),
                                    session_path=str(sess))
    assert st["event_stale"] is True
    assert st["status"] == "stale"
    assert st["signals"]["walk_ledger"] > 0


def test_cache_status_fresh_when_walk_ledger_older(tmp_path):
    cache = _write_cache(tmp_path, "2026-07-17T11:00:00Z")
    sess = tmp_path / "brief-session.json"
    sess.write_text(json.dumps({"updated_utc": "2026-07-17T10:00:00Z"}), encoding="utf-8")
    st = brief_session.cache_status(cache, now_epoch=brief_session._iso_epoch("2026-07-17T12:00:00Z"),
                                    session_path=str(sess))
    assert st["event_stale"] is False
    assert st["status"] == "fresh"


def test_cache_status_stale_when_changelog_newer(tmp_path):
    cache = _write_cache(tmp_path, "2026-07-17T11:00:00Z")
    log = tmp_path / "notion-changelog.jsonl"
    log.write_text('{"ts": "2026-07-17T10:00:00Z"}\n{"ts": "2026-07-17T14:31:00Z"}\n',
                   encoding="utf-8")
    st = brief_session.cache_status(cache, now_epoch=brief_session._iso_epoch("2026-07-17T15:00:00Z"),
                                    changelog_path=str(log))
    assert st["status"] == "stale"


def test_cache_status_stale_when_notion_watermark_newer(tmp_path):
    cache = _write_cache(tmp_path, "2026-07-17T11:00:00Z")
    st = brief_session.cache_status(cache, now_epoch=brief_session._iso_epoch("2026-07-17T15:00:00Z"),
                                    notion_watermark="2026-07-17T14:05:00Z")
    assert st["status"] == "stale"


def test_cache_status_age_backstop_still_fires(tmp_path):
    cache = _write_cache(tmp_path, "2026-07-16T11:00:00Z")   # >720 min old, no signals
    st = brief_session.cache_status(cache, now_epoch=brief_session._iso_epoch("2026-07-17T15:00:00Z"))
    assert st["status"] == "stale"


def test_cache_status_missing_signal_files_are_quiet(tmp_path):
    cache = _write_cache(tmp_path, "2026-07-17T11:00:00Z")
    st = brief_session.cache_status(cache, now_epoch=brief_session._iso_epoch("2026-07-17T12:00:00Z"),
                                    session_path=str(tmp_path / "absent.json"),
                                    changelog_path=str(tmp_path / "absent.jsonl"))
    assert st["status"] == "fresh"
```

- [ ] **Step 2: Run to verify they fail** — `python -m pytest engine/tools/tests/test_brief_session.py -q -k cache_status` → FAIL (`_iso_epoch` not defined / unexpected keyword `session_path`).

- [ ] **Step 3: Implement.** In `brief_session.py`, extract the timestamp parse (currently inline at `:615-619`) into a module helper next to `_utcnow`, add the two signal readers, and extend `cache_status`:

```python
def _iso_epoch(s):
    """ISO-8601 'YYYY-MM-DDTHH:MM:SSZ' -> epoch seconds; 0.0 on absent/unparseable."""
    try:
        import calendar
        return float(calendar.timegm(time.strptime((s or "")[:19], "%Y-%m-%dT%H:%M:%S")))
    except Exception:
        return 0.0


def _signal_epoch_session(session_path):
    """Newest walk-ledger activity: brief-session.json updated_utc (0.0 if absent)."""
    obj = _read_json(session_path) if session_path else None
    return _iso_epoch(obj.get("updated_utc")) if isinstance(obj, dict) else 0.0


def _signal_epoch_changelog(changelog_path):
    """ts of the LAST parseable row of notion-changelog.jsonl (0.0 if absent/empty)."""
    if not changelog_path or not os.path.exists(changelog_path):
        return 0.0
    best = 0.0
    try:
        with open(changelog_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    best = max(best, _iso_epoch(json.loads(line).get("ts")))
                except Exception:
                    continue
    except OSError:
        return 0.0
    return best
```

In `cache_status`, add the parameters `session_path=None, changelog_path=None, notion_watermark=None`; replace the inline `gen_epoch` parse with `gen_epoch = _iso_epoch(gen)`; after the existing `degraded` computation insert:

```python
    signals = {
        "walk_ledger": _signal_epoch_session(session_path),
        "changelog": _signal_epoch_changelog(changelog_path),
        "notion_watermark": _iso_epoch(notion_watermark),
    }
    event_stale = gen_epoch > 0 and any(e > gen_epoch for e in signals.values())
```

and change the verdict line to:

```python
    status = ("stale" if (not age_ok or event_stale)
              else ("degraded" if degraded else "fresh"))
```

Add `"signals": signals, "event_stale": event_stale` to the returned dict. In the CLI arm (`op == "cache-status"`), pass the three new values: `session_path=_val("--session")`, `changelog_path=_val("--changelog")`, `notion_watermark=_val("--notion-watermark")`, and update the usage comment. Update the module docstring's op list if it documents cache-status flags.

- [ ] **Step 4: Run** `python -m pytest engine/tools/tests/test_brief_session.py -q` → all PASS (new + existing; existing cache-status tests must still pass — the no-signal path is behavior-identical).
- [ ] **Step 5: Commit** — `git add engine/tools/brief_session.py engine/tools/tests/test_brief_session.py` · inspect `git diff --cached --stat` as its own call · `git commit -m "A93 §1: event-based staleness signals in cache-status (ledger/changelog/watermark beat the timer)"`

---

### Task 2: Carryover-deferral revalidation (spec §2, auto-clear)

**Files:**
- Modify: `engine/tools/brief_session.py:130-178` (`new_walk`, `resume_or_new`) + the `resume_or_new` CLI arm at `:800-823`
- Modify: `engine/tools/brief_render.py` (new `render_auto_cleared` + a `auto-cleared` op in `main`)
- Test: `engine/tools/tests/test_brief_session.py`, `engine/tools/tests/test_brief_render.py` (append)

**Interfaces:**
- Consumes: Task-agnostic; uses existing `new_walk` / `resume_or_new` / `load`.
- Produces: `resume_or_new(state_path, walk_id, station_order, stations_seed, done_item_ids=None)`; new-walk ledgers carry `"auto_cleared": [deferral dicts]`; `brief_render.render_auto_cleared(ledger) -> str` emitting one `✅ auto-cleared: {title} (completed since deferral)` line per entry (empty string when none). CLI: `resume_or_new ... [--done-ids id1,id2]` and `brief_render.py auto-cleared <brief-session.json>`.

- [ ] **Step 1: Write the failing tests**

```python
# test_brief_session.py
def test_resume_or_new_drops_done_carryover(tmp_path):
    state = str(tmp_path / "brief-session.json")
    led = brief_session.new_walk(state, "w1", ["settle", "kb"], {"kb": 1})
    brief_session.record_deferral(state, "T-DONE", "Done task", "kb", "timing", "2026-07-18")
    brief_session.record_deferral(state, "T-OPEN", "Still open", "kb", "timing", "2026-07-18")
    led = brief_session.load(state)
    led["status"] = "complete"
    brief_session._atomic_write(state, led)
    mode, new_led = brief_session.resume_or_new(state, "w2", ["settle", "kb"], {"kb": 0},
                                                done_item_ids={"T-DONE"})
    assert mode == "new"
    carried = [d["item_id"] for d in new_led["deferrals"]]
    assert carried == ["T-OPEN"]
    assert [d["item_id"] for d in new_led["auto_cleared"]] == ["T-DONE"]

# test_brief_render.py
def test_render_auto_cleared():
    ledger = {"auto_cleared": [{"item_id": "T1", "title": "Transfer the widget"}]}
    out = brief_render.render_auto_cleared(ledger)
    assert out == "✅ auto-cleared: Transfer the widget (completed since deferral)"
    assert brief_render.render_auto_cleared({"auto_cleared": []}) == ""
    assert brief_render.render_auto_cleared({}) == ""
```

- [ ] **Step 2: Run to verify they fail** — unexpected keyword `done_item_ids` / no attribute `render_auto_cleared`.
- [ ] **Step 3: Implement.** `new_walk` gains `auto_cleared=None` and stores `ledger["auto_cleared"] = list(auto_cleared) if auto_cleared else []`. `resume_or_new` gains `done_item_ids=None`; its carryover loop becomes:

```python
    carryover, auto_cleared = [], []
    done = set(done_item_ids or ())
    if isinstance(existing, dict):
        for d in existing.get("deferrals", []):
            if d.get("resurface") != "next-walk":
                continue
            (auto_cleared if d.get("item_id") in done else carryover).append(d)
    ledger = new_walk(state_path, walk_id, station_order, stations_seed,
                      carryover_deferrals=carryover, auto_cleared=auto_cleared)
```

CLI arm `resume_or_new`: parse `--done-ids` (comma-split into a set) alongside `--order`/`--seed` and pass it. In `brief_render.py`:

```python
def render_auto_cleared(ledger):
    """One '✅ auto-cleared' line per deferral dropped because its task completed. Lifted verbatim."""
    rows = (ledger or {}).get("auto_cleared") or []
    return "\n".join(
        f"✅ auto-cleared: {r.get('title') or r.get('item_id')} (completed since deferral)"
        for r in rows)
```

and add the op to `main` following the existing op pattern: `auto-cleared <brief-session.json>` → `print(render_auto_cleared(_load(path) or {}))`.

- [ ] **Step 4: Run** `python -m pytest engine/tools/tests/test_brief_session.py engine/tools/tests/test_brief_render.py -q` → PASS.
- [ ] **Step 5: Commit** — stage the four files, inspect `--cached --stat` separately, `git commit -m "A93 §2: done-task carryover deferrals auto-clear instead of re-rendering"`

---

### Task 3: Live held-panel assertion + as-of stamp (spec §2)

**Files:**
- Modify: `engine/tools/brief_session.py:358-` (`validate_cache`) + its CLI arm at `:875-909`
- Modify: `engine/tools/brief_render.py` (`render_card` at `:75`, `render_station` at `:251`, `render_card_by_id` at `:256`)
- Test: `engine/tools/tests/test_brief_session.py`, `engine/tools/tests/test_brief_render.py`

**Interfaces:**
- Consumes: `validate_cache(cache_obj, required_domains, standup)` returning `(ok, errors)`.
- Produces: `validate_cache(..., live_awaiting=None)` — when an int, appends error `held panel stale: cache holds {n}, live queue has {m} — re-gather` on mismatch. CLI flag `--live-awaiting N` (the SKILL passes the count from the `queue_tx.py select --stage awaiting` call it already makes). `render_card(item, display_map=None, as_of=None)`: when `as_of` is set and the item lacks `verified_this_run: true`, the card's header line gains the suffix `  ·  as of {as_of}`; `render_station`/`render_card_by_id` read `cache.get("generated_utc")` and pass it through.

- [ ] **Step 1: Write the failing tests**

```python
# test_brief_session.py — build the minimal cache dict the existing validate_cache tests use
# (copy the fixture-shape from the nearest existing validate_cache test in this file).
def test_validate_cache_held_parity(minimal_valid_cache):
    cache = minimal_valid_cache          # has held: [] per the existing fixture shape
    cache["held"] = [{"id": "h1"}, {"id": "h2"}]
    ok, errs = brief_session.validate_cache(cache, required_domains=["dev"], live_awaiting=2)
    assert ok
    ok, errs = brief_session.validate_cache(cache, required_domains=["dev"], live_awaiting=0)
    assert not ok and any("held panel stale" in e for e in errs)

# test_brief_render.py
def test_render_card_as_of_stamp():
    item = {"id": "i1", "title": "T", "domain": "dev",
            "urgency": "u", "claude_voice": {"text": "c"}}
    out = brief_render.render_card(item, as_of="2026-07-17T11:02:22Z")
    assert "as of 2026-07-17T11:02:22Z" in out.splitlines()[0]
    live = dict(item, verified_this_run=True)
    assert "as of" not in brief_render.render_card(live, as_of="2026-07-17T11:02:22Z")
```

(Adapt the two item/cache literals to the exact minimal shapes the existing tests in each file construct — reuse their helpers/fixtures if present rather than inventing a parallel shape.)

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.** In `validate_cache`, add parameter `live_awaiting=None`; before the final `return ok, errors`:

```python
    if live_awaiting is not None:
        held_n = len(cache_obj.get("held") or [])
        if int(live_awaiting) != held_n:
            errors.append(f"held panel stale: cache holds {held_n}, "
                          f"live queue has {int(live_awaiting)} — re-gather")
```

(ensure `ok` is derived from `errors` after this — match how the function currently computes it). CLI arm: parse optional `--live-awaiting N` and pass `live_awaiting=int(...)`. In `brief_render.py`, `render_card` gains `as_of=None`; where it builds the header (title + domain) line, append `f"  ·  as of {as_of}"` when `as_of and not item.get("verified_this_run")`. `render_station` and `render_card_by_id` pass `as_of=cache.get("generated_utc")`.

- [ ] **Step 4: Run both test files** → PASS; also `python -m pytest -q` (render goldens elsewhere may pin card output — if `test_brief_render.py` or `suite_test.py` goldens break, the stamp is additive-only on the header line: update ONLY goldens whose diff is exactly the new suffix).
- [ ] **Step 5: Commit** — `"A93 §2: validate_cache live-held parity + honest as-of stamp on cache-sourced cards"`

---

### Task 4: Movement line (spec §3)

**Files:**
- Modify: `engine/tools/brief_render.py` (new `render_movement`, `_all_item_ids`; `render_overview` at `:163` + `render_overview_row` at `:118` gain the `↑ now in Act` tag; new `movement` op in `main`)
- Test: `engine/tools/tests/test_brief_render.py`

**Interfaces:**
- Consumes: `_act_rows(cache)`, `_station_items(cache, station)` (both exist).
- Produces: `render_movement(prev_cache, cur_cache) -> str` — `""` on zero delta, else one line `✅ {N} cleared since last brief — {up to 5 titles, comma-joined}` with ` (+{K} more)` past 5. `render_overview(cache, limit=None, prev_act_ids=None)`; `render_overview_row(item, display_map=None, new_in_act=False)` appends `  ·  ↑ now in Act` to its header line. CLI: `brief_render.py movement <prev.json> <cur.json>`. The gather (Task 6) copies the incumbent cache to `state/brief-cache.prev.json` before overwriting.

- [ ] **Step 1: Write the failing tests**

```python
def _mini_cache(ids_titles, act_ids=()):
    """Minimal cache: one 'dev' station; item shape copied from this file's existing helpers."""
    items = [{"id": i, "title": t, "domain": "dev", "urgency": "u",
              "claude_voice": {"text": "c"}} for i, t in ids_titles]
    return {"generated_utc": "2026-07-17T11:00:00Z",
            "stations": {"dev": items},
            "act": [i for i in items if i["id"] in set(act_ids)]}

def test_render_movement_cleared_line():
    prev = _mini_cache([("a", "Alpha"), ("b", "Beta"), ("c", "Gamma")])
    cur = _mini_cache([("c", "Gamma")])
    out = brief_render.render_movement(prev, cur)
    assert out == "✅ 2 cleared since last brief — Alpha, Beta"

def test_render_movement_zero_delta_is_empty():
    prev = _mini_cache([("a", "Alpha")])
    assert brief_render.render_movement(prev, prev) == ""

def test_render_movement_collapses_past_five():
    prev = _mini_cache([(f"i{n}", f"T{n}") for n in range(7)])
    cur = _mini_cache([])
    out = brief_render.render_movement(prev, cur)
    assert out.startswith("✅ 7 cleared since last brief — ")
    assert "(+2 more)" in out

def test_overview_tags_new_in_act():
    cur = _mini_cache([("a", "Alpha"), ("b", "Beta")], act_ids=("a", "b"))
    out = brief_render.render_overview(cur, prev_act_ids={"a"})
    assert "↑ now in Act" in out
    lines_with_tag = [l for l in out.splitlines() if "↑ now in Act" in l]
    assert len(lines_with_tag) == 1 and "Beta" in lines_with_tag[0]
```

(Match `_mini_cache`'s item dict to the exact minimal item shape `render_overview_row` needs — copy from this test file's existing fixtures; the `act`/`stations` keys must mirror what `_act_rows`/`_station_items` read.)

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.**

```python
def _all_item_ids(cache):
    """Ordered {id: title} over every station item + act row (first occurrence wins)."""
    seen = {}
    for station in (cache.get("stations") or {}):
        for it in _station_items(cache, station):
            seen.setdefault(it.get("id"), it.get("title") or it.get("id"))
    for it in _act_rows(cache):
        seen.setdefault(it.get("id"), it.get("title") or it.get("id"))
    seen.pop(None, None)
    return seen


def render_movement(prev_cache, cur_cache):
    """'✅ N cleared since last brief — …' from a prev-vs-current cache diff; '' on zero delta.
    Lifted verbatim (Invariant 2) — the movement line is engine-emitted, never hand-typed."""
    prev = _all_item_ids(prev_cache or {})
    cur = _all_item_ids(cur_cache or {})
    cleared = [t for i, t in prev.items() if i not in cur]
    if not cleared:
        return ""
    shown = ", ".join(cleared[:5])
    extra = f" (+{len(cleared) - 5} more)" if len(cleared) > 5 else ""
    return f"✅ {len(cleared)} cleared since last brief — {shown}{extra}"
```

`render_overview_row(item, display_map=None, new_in_act=False)`: append `"  ·  ↑ now in Act"` to the header line it builds (the first line of its return) when `new_in_act`. `render_overview(cache, limit=None, prev_act_ids=None)`: for each act row, pass `new_in_act=(prev_act_ids is not None and it.get("id") not in prev_act_ids)`. `main` gains op `movement <prev.json> <cur.json>` → `print(render_movement(_load(a1) or {}, _load(a2) or {}))`.

- [ ] **Step 4: Run** the render tests + full suite → PASS.
- [ ] **Step 5: Commit** — `"A93 §3: movement line (cleared-since-last-brief + now-in-Act tag) from prev-cache diff"`

---

### Task 5: Delta-gated health lines + anomaly detail (spec §4)

**Files:**
- Modify: `engine/tools/brief_render.py` (new `health_gate` + `health-gate` op)
- Modify: `engine/tools/pipeline_health.py` (new `list_anomalies(path, hours, now)` + `--list-anomalies` flag)
- Test: `engine/tools/tests/test_brief_render.py`, `engine/tools/tests/test_pipeline_health.py`

**Interfaces:**
- Consumes: `brief_session._atomic_write` (import inside the function to avoid a module cycle); `pipeline_health._parse_ts` and its 30h-window record scan (see `render` at `:42-95` — records carry `stage`, a ts field, and `anomalies` as a list of strings or an int).
- Produces: `health_gate(cache_path, key, line, update=True) -> str` — returns `line` if its sha1 differs from `cache["health_fingerprints"][key]` (writing the new fingerprint when `update`), else `""`. `list_anomalies(path, hours=30, now=None) -> list[str]` of `"{ts} · {stage} · {text}"` rows (int-count records render `"{ts} · {stage} · {n} anomalies (no detail recorded)"`). CLI: `brief_render.py health-gate <cache.json> <key> <line…>` (prints line or nothing) and `pipeline_health.py --path P --list-anomalies`.

- [ ] **Step 1: Write the failing tests**

```python
# test_brief_render.py
def test_health_gate_first_render_then_silence(tmp_path):
    cache = tmp_path / "brief-cache.json"
    cache.write_text(json.dumps({"generated_utc": "2026-07-17T11:00:00Z"}), encoding="utf-8")
    line = "⚙️ Pipeline (last 30h): 10 runs · ⚠ 10 anomalies"
    assert brief_render.health_gate(str(cache), "pipeline", line) == line   # first: renders
    assert brief_render.health_gate(str(cache), "pipeline", line) == ""     # unchanged: silent
    changed = line.replace("10 anomalies", "11 anomalies")
    assert brief_render.health_gate(str(cache), "pipeline", changed) == changed

# test_pipeline_health.py
def test_list_anomalies_detail_rows(tmp_path):
    log = tmp_path / "context-log.jsonl"
    rows = [
        {"ts": "2026-07-17T06:00:00Z", "stage": "session-capture",
         "anomalies": ["marker gap on 3 bundles"]},
        {"ts": "2026-07-17T06:05:00Z", "stage": "capture-router", "anomalies": 2},
        {"ts": "2026-07-10T06:00:00Z", "stage": "old-stage", "anomalies": ["outside window"]},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = pipeline_health.list_anomalies(str(log), hours=30,
                                         now=pipeline_health._parse_ts("2026-07-17T12:00:00Z"))
    assert out == [
        "2026-07-17T06:00:00Z · session-capture · marker gap on 3 bundles",
        "2026-07-17T06:05:00Z · capture-router · 2 anomalies (no detail recorded)",
    ]
```

(Match the record's ts field name and `now` plumbing to what `render()` actually uses — read `render` first; if `_parse_ts` returns a datetime rather than an epoch, mirror that in the test.)

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.**

```python
# brief_render.py
def health_gate(cache_path, key, line, update=True):
    """Delta-gate a masthead health line: render only when its content changed since the last
    brief (fingerprints live in the cache JSON under health_fingerprints). Steady-state = ''."""
    import hashlib
    from brief_session import _atomic_write
    obj = _load(cache_path) or {}
    fp = hashlib.sha1((line or "").strip().encode("utf-8")).hexdigest()
    fps = obj.setdefault("health_fingerprints", {})
    if fps.get(key) == fp:
        return ""
    if update:
        fps[key] = fp
        _atomic_write(cache_path, obj)
    return line
```

(If `brief_render.py` imports its siblings differently — check the top of the file — follow its existing import pattern for `brief_session`.) Add op `health-gate <cache.json> <key> <line…>` to `main`: join the remaining argv as the line, print the result. In `pipeline_health.py`, factor the record-window scan loop from `render` (path → parsed records within `hours` of `now`) into a shared helper if trivial, then:

```python
def list_anomalies(path, hours=30, now=None):
    """Per-record anomaly detail rows for the brief's expand affordance."""
    out = []
    for rec in _records_in_window(path, hours=hours, now=now):   # the factored scan
        a = rec.get("anomalies")
        if not a:
            continue
        ts, stage = rec.get("ts", "?"), rec.get("stage", "?")
        if isinstance(a, list):
            out.extend(f"{ts} · {stage} · {x}" for x in a)
        else:
            out.append(f"{ts} · {stage} · {a} anomalies (no detail recorded)")
    return out
```

Wire `--list-anomalies` in `main` to print one row per line.

- [ ] **Step 4: Run** both test files + full suite → PASS.
- [ ] **Step 5: Commit** — `"A93 §4: delta-gated health lines (fingerprints in cache) + per-anomaly detail op"`

---

### Task 6: Skill/gather prose wiring + suite + close

**Files:**
- Modify: `skills/brief/SKILL.md` (Render-flow step 2, VERIFY step, masthead health lines)
- Modify: `skills/brief/references/gather.md` (`## Cache contract`)
- Modify: `BACKLOG.md` (tick nothing yet — the item closes at review; just verify A93's acceptance bullets all map to shipped behavior)

**Interfaces:** consumes every CLI produced in Tasks 1–5; produces no code.

- [ ] **Step 1: SKILL.md — Render flow step 2:** extend the `cache-status` invocation with the new flags so the tested boolean sees the signals:

```
python "${CLAUDE_PLUGIN_ROOT}/engine/tools/brief_session.py" cache-status "<env_root>/state/brief-cache.json" \
  --max-age-min <profile brief.max_age_min, default 720> \
  --session "<env_root>/state/brief-session.json" \
  --changelog "<env_root>/state/notion-changelog.jsonl" \
  [--notion-watermark <max last_edited_time from one cheap allowlisted-DB query, when Notion is reachable>] \
  [--notion-enabled] [--session-has-notion] ...
```

and state: the timer is now only the backstop — a walk decision, a write-back receipt, or a Notion edit after `generated_utc` makes the cache stale (the 2026-07-17 incident class).
- [ ] **Step 2: SKILL.md — walk seeding:** where `resume_or_new` is invoked, add `--done-ids` built from the fresh gather's Done/absent task ids, and instruct lifting `brief_render.py auto-cleared <brief-session.json>` verbatim at the top of the walk. **Header:** after the count chips, lift `brief_render.py movement <prev> <cur>` verbatim (omit when empty); each health line now renders through `brief_render.py health-gate <cache> <key> <line>` (omit when empty — steady-state is silence); the anomalies line, when it renders, offers "show the N anomalies" via `pipeline_health.py --list-anomalies`. **VERIFY step:** `validate_cache` gains `--live-awaiting <count from the queue_tx select already run for the held panel>`. **Citation honesty:** add one sentence to `# Real titles`/render rules: "queried live {date}" may appear only for facts queried in THIS run; cache-sourced cards carry the engine's `as of {generated_utc}` stamp (emitted by `render_card`, never hand-typed).
- [ ] **Step 3: gather.md — `## Cache contract`:** before overwriting `brief-cache.json`, copy the incumbent to `brief-cache.prev.json` (same dir, atomic); document `health_fingerprints` as an engine-owned key the gather must carry forward unchanged (health-gate owns writes to it); note the `movement` op consumes prev vs new.
- [ ] **Step 4: Full suite + drift gates:** `python -m pytest -q` → exit 0 (show the summary). `git add` the two skill docs, inspect `--cached --stat` separately, commit `"A93 §1-4 wiring: SKILL/gather prose reads the new signals, movement, health-gate, live-held assert"` (driftcheck validates the SKILL.md paths).
- [ ] **Step 5: Fresh-context review** (the A93 acceptance line): dispatch a fresh-context review subagent (not the builder) over the full A93 diff; zero CRITICAL reported in chat; fix-and-re-review on any CRITICAL. Then push (`git pull --rebase` first).

---

## Self-review (run against the spec)

- Spec §1 → Task 1 (three signals + backstop, offline unit tests). §2 → Tasks 2 (deferral auto-clear) + 3 (live-held assert, as-of stamp). §3 → Task 4 (+ prev-copy in Task 6 Step 3). §4 → Task 5 (+ masthead wiring in Task 6; A91's extension executes under A91, not here). Acceptance bullets all covered.
- The Notion watermark stays caller-supplied (fact-free tool) — the one cheap query lives in SKILL prose (Task 6 Step 1), matching the spec's "the gatherer passes the value".
- Type consistency: `cache_status` signature, `resume_or_new(done_item_ids)`, `validate_cache(live_awaiting)`, `render_movement(prev, cur)`, `health_gate(cache_path, key, line)` are each defined once and consumed by name in Task 6 only.
