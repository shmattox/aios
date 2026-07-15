# A73 Gate + Factory Acceptance Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Instrument acceptance outcomes ($/accepted-result) for both loops — gate decisions read from queue terminal items, factory drain survival read from backlog Done-lines vs git reverts — per spec `docs/superpowers/specs/2026-07-15-a73-gate-acceptance-metrics-design.md`.

**Architecture:** Two deterministic read-only collectors + a 5-line write-path normalization. aios gets `engine/tools/gate_metrics.py` (reader over `state/queue.json`) and one render extension; the env repo gets a `collect_acceptance()` extension in `Scripts/factory-gate/factory_standup.py`. No new storage, no new scheduled task (H62 pattern: env collects, aios renders, model lifts verbatim).

**Tech Stack:** Python stdlib only (both repos). pytest. Two git repos: `Projects/aios` (tasks 1–3, 5) and `Documents/Claude` env root (task 4).

## Global Constraints

- **Fact-free engine (aios):** no instance names in `Projects/aios` code/tests — the decider classifier must NOT hardcode `seth`; classification rule: `approved_by` ∈ {`auto-ship`, `auto-ship-scheduled`} → auto/scheduled, any OTHER non-empty value → `human` (per gate SKILL: `--approved-by <auto-ship | the approver | auto-ship-scheduled>`), absent → `unknown`.
- **No wall-clock in collectors:** `--today YYYY-MM-DD` is always injected (matches `factory_standup.py`).
- **Documented extraction method (spec §Ecosystem-check note):** history values are read as *the most recent history entry CARRYING the key* (reverse scan) — one method, tested.
- **Deterministic render:** fixed-format lines; missing inputs render a loud "unavailable" line, never silent zeros (no-silent-caps).
- **Collector contract (env leg):** `factory_standup.py` stays exit-0; failures become surfaced fields.
- **Known-red fence:** the aios suite has a pre-existing A72 failure (`test_domain_mirror.py` FO-drift). Gate on new/touched tests green + no NEW failures; never "fix" or skip the A72 test in this plan.
- **UTF-8 stdio:** new CLIs reconfigure stdout/stderr like `factory_standup.py:8-10`.
- Commit messages end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: `gate_metrics.py` core — classify + rollup

**Files:**
- Create: `Projects/aios/engine/tools/gate_metrics.py`
- Test: `Projects/aios/engine/tools/tests/test_gate_metrics.py`

**Interfaces:**
- Consumes: `queue_tx.load(path)` → `{"queue": [...]}` (`engine/tools/queue_tx.py:228`).
- Produces (Task 2/4/5 rely on): `decider_class(item) -> str`, `outcome(item) -> str`, `agreement(item) -> str`, `terminal_date(item) -> str|None`, `rollup(items, today) -> dict` with keys `windows.{all,30d,7d}` each `{totals:{accepted,rejected,reverted}, deciders:{human,auto,scheduled,unknown}, agreement:{agree,override,hold,na}, override_ids:[...], by_kb_lane:{"<kb>|<lane>":{accepted,rejected,reverted}}, unknown_ts:int, n:int}`.

- [ ] **Step 1: Write the failing tests**

```python
# Projects/aios/engine/tools/tests/test_gate_metrics.py
import json, os, subprocess, sys
import pytest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import gate_metrics as gm  # noqa: E402


def _item(stage="shipped", history=None, recommended="approve", kb="demo", lane="auto-ship"):
    return {"id": "x", "stage": stage, "recommended": recommended, "kb": kb, "lane": lane,
            "history": history if history is not None else []}


# --- decider_class: documented extraction = most recent history entry CARRYING the key ---

def test_decider_prefers_normalized_decided_by():
    it = _item(history=[{"ts": "t1", "stage": "shipped", "approved_by": "auto-ship",
                         "decided_by": "human"}])
    assert gm.decider_class(it) == "human"

def test_decider_reverse_scan_takes_most_recent_carrying_entry():
    it = _item(history=[{"ts": "t1", "stage": "shipped", "approved_by": "auto-ship"},
                        {"ts": "t2", "stage": "reverted"},          # no key — skipped
                        {"ts": "t3", "stage": "shipped", "approved_by": "a-person"}])
    assert gm.decider_class(it) == "human"                          # t3 wins, t2 skipped

def test_decider_legacy_vocabulary_fact_free():
    # any non-auto value is a named human approver — NO instance-name prefixes in the engine
    for raw, want in [("auto-ship", "auto"), ("auto-ship-scheduled", "scheduled"),
                      ("a-person", "human"), ("A-Person-batch-hygiene", "human"),
                      ("someone-brief-2026-07-08", "human")]:
        it = _item(history=[{"ts": "t", "stage": "shipped", "approved_by": raw}])
        assert gm.decider_class(it) == want, raw

def test_decider_missing_is_unknown_never_dropped():
    assert gm.decider_class(_item(history=[{"ts": "t", "stage": "shipped"}])) == "unknown"


# --- outcome / agreement matrix ---

def test_outcome_mapping():
    assert gm.outcome(_item(stage="shipped")) == "accepted"
    assert gm.outcome(_item(stage="rejected")) == "rejected"
    assert gm.outcome(_item(stage="reverted")) == "reverted"

@pytest.mark.parametrize("stage,rec,want", [
    ("shipped", "approve", "agree"), ("shipped", "reject", "override"),
    ("rejected", "reject", "agree"), ("rejected", "approve", "override"),
    ("shipped", "hold", "hold"), ("rejected", "hold", "hold"),
    ("shipped", None, "na"), ("reverted", "approve", "na"),   # reverted excluded from agreement
])
def test_agreement_matrix(stage, rec, want):
    assert gm.agreement(_item(stage=stage, recommended=rec)) == want


# --- terminal_date + windowing ---

def test_terminal_date_from_terminal_history_entry():
    it = _item(history=[{"ts": "2026-07-01T05:00:00Z", "stage": "awaiting"},
                        {"ts": "2026-07-03T05:00:00Z", "stage": "shipped", "approved_by": "auto-ship"}])
    assert gm.terminal_date(it) == "2026-07-03"

def test_terminal_date_missing_goes_unknown_ts_bucket():
    r = gm.rollup([_item(history=[])], today="2026-07-15")
    assert r["windows"]["all"]["unknown_ts"] == 1
    assert r["windows"]["all"]["n"] == 1          # still counted all-time
    assert r["windows"]["30d"]["n"] == 0          # but never inside a dated window

def test_rollup_windows_and_by_kb_lane():
    old = _item(history=[{"ts": "2026-01-01T00:00:00Z", "stage": "shipped", "approved_by": "auto-ship"}])
    new = _item(stage="rejected", recommended="approve", kb="fo", lane="review",
                history=[{"ts": "2026-07-14T00:00:00Z", "stage": "rejected", "reason": "r",
                          "decided_by": "human"}])
    r = gm.rollup([old, new], today="2026-07-15")
    assert r["windows"]["all"]["totals"] == {"accepted": 1, "rejected": 1, "reverted": 0}
    assert r["windows"]["7d"]["totals"] == {"accepted": 0, "rejected": 1, "reverted": 0}
    assert r["windows"]["7d"]["deciders"]["human"] == 1
    assert r["windows"]["7d"]["agreement"]["override"] == 1
    assert r["windows"]["7d"]["override_ids"] == ["x"]
    assert r["windows"]["7d"]["by_kb_lane"] == {"fo|review": {"accepted": 0, "rejected": 1, "reverted": 0}}

def test_rollup_ignores_non_terminal_stages():
    r = gm.rollup([_item(stage="awaiting"), _item(stage="sorted")], today="2026-07-15")
    assert r["windows"]["all"]["n"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Projects/aios && python -m pytest engine/tools/tests/test_gate_metrics.py -q`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'gate_metrics'`

- [ ] **Step 3: Write the implementation**

```python
# Projects/aios/engine/tools/gate_metrics.py
#!/usr/bin/env python3
"""gate_metrics.py — A73: read-only acceptance metrics over queue terminal items.

Reads state/queue.json via queue_tx.load (never hand-parsed) and rolls up, per window
(all / 30d / 7d keyed on the injected --today):
  outcome (accepted / rejected / reverted)  x  decider class (human / auto / scheduled / unknown)
  x  recommendation agreement (agree / override / hold / na)  x  (kb, lane).

EXTRACTION METHOD (load-bearing, method-sensitive — spec 2026-07-15 §Ecosystem-check):
history values are read as the MOST RECENT history entry CARRYING the key (reverse scan,
entries without the key are skipped). One method, tested; do not add variants.

Fact-free: the decider classifier hardcodes no person names — approved_by is either one of
the two auto constants or the approver's name (gate SKILL contract), so any other non-empty
value classifies `human`. Read-only; fail-soft `render` (loud "unavailable", never zeros).
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import queue_tx  # noqa: E402

TERMINAL = ("shipped", "rejected", "reverted")
_OUTCOME = {"shipped": "accepted", "rejected": "rejected", "reverted": "reverted"}
WINDOWS = (("all", None), ("30d", 30), ("7d", 7))
OVERRIDE_ID_CAP = 20  # rendered list cap; the count is never capped


def _hist_value(item, key):
    for h in reversed(item.get("history", []) or []):
        if key in h:
            return h[key]
    return None


def decider_class(item):
    v = _hist_value(item, "decided_by")
    if v in ("human", "auto", "scheduled"):
        return v
    raw = _hist_value(item, "approved_by")
    if raw is None or not str(raw).strip():
        return "unknown"
    r = str(raw).strip().lower()
    if r == "auto-ship-scheduled":
        return "scheduled"
    if r == "auto-ship":
        return "auto"
    return "human"


def outcome(item):
    return _OUTCOME.get(item.get("stage"), "")


def agreement(item):
    out = outcome(item)
    if out == "reverted" or out == "":
        return "na"
    rec = item.get("recommended")
    if rec == "hold":
        return "hold"
    if rec not in ("approve", "reject"):
        return "na"
    hit = (rec == "approve" and out == "accepted") or (rec == "reject" and out == "rejected")
    return "agree" if hit else "override"


def terminal_date(item):
    for h in reversed(item.get("history", []) or []):
        if h.get("stage") in TERMINAL and h.get("ts"):
            return str(h["ts"])[:10]
    for h in reversed(item.get("history", []) or []):
        if h.get("ts"):
            return str(h["ts"])[:10]
    return None


def _empty_window():
    return {"n": 0, "unknown_ts": 0,
            "totals": {"accepted": 0, "rejected": 0, "reverted": 0},
            "deciders": {"human": 0, "auto": 0, "scheduled": 0, "unknown": 0},
            "agreement": {"agree": 0, "override": 0, "hold": 0, "na": 0},
            "override_ids": [], "by_kb_lane": {}}


def _days_ago(today, d):
    try:
        ty, tm, td = (int(x) for x in today.split("-"))
        y, m, dd = (int(x) for x in d.split("-"))
        return (date(ty, tm, td) - date(y, m, dd)).days
    except (ValueError, TypeError):
        return None


def rollup(items, today):
    wins = {name: _empty_window() for name, _ in WINDOWS}
    for it in items:
        out = outcome(it)
        if not out:
            continue
        tdate = terminal_date(it)
        age = _days_ago(today, tdate) if tdate else None
        for name, span in WINDOWS:
            w = wins[name]
            if span is None:
                if age is None:
                    w["unknown_ts"] += 1
                elif age < 0:
                    continue  # future-dated: excluded from every window, visible in `all` only via unknown_ts? No — count it.
            else:
                if age is None or age < 0 or age > span:
                    continue
            w["n"] += 1
            w["totals"][out] += 1
            w["deciders"][decider_class(it)] += 1
            agr = agreement(it)
            w["agreement"][agr] += 1
            if agr == "override" and len(w["override_ids"]) < OVERRIDE_ID_CAP:
                w["override_ids"].append(it.get("id", "?"))
            key = f"{it.get('kb') or '?'}|{it.get('lane') or '?'}"
            cell = w["by_kb_lane"].setdefault(key, {"accepted": 0, "rejected": 0, "reverted": 0})
            cell[out] += 1
    return {"generated": today, "windows": wins}


def report(queue_path, today):
    data = queue_tx.load(queue_path)
    return rollup(data.get("queue", []), today)
```

Note the awkward branch marked in `rollup` — clean it before committing: the `all` window counts every terminal item (unknown/future ts included; unknown also increments `unknown_ts`), dated windows require `0 <= age <= span`. Final form of the loop body's window check:

```python
        for name, span in WINDOWS:
            w = wins[name]
            if span is None:
                if age is None:
                    w["unknown_ts"] += 1
            else:
                if age is None or age < 0 or age > span:
                    continue
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Projects/aios && python -m pytest engine/tools/tests/test_gate_metrics.py -q`
Expected: all PASS

- [ ] **Step 5: Commit (aios repo)**

```bash
cd Projects/aios
git add engine/tools/gate_metrics.py engine/tools/tests/test_gate_metrics.py
git commit -m "feat(A73): gate_metrics core — outcome/decider/agreement rollups over queue terminal items"
```

---

### Task 2: `gate_metrics.py` CLI — `report` + deterministic `render`

**Files:**
- Modify: `Projects/aios/engine/tools/gate_metrics.py` (append CLI)
- Test: `Projects/aios/engine/tools/tests/test_gate_metrics.py` (append)

**Interfaces:**
- Consumes: Task 1's `report(queue_path, today)`.
- Produces: CLI `python gate_metrics.py report --queue Q --today YYYY-MM-DD [--out PATH]` (prints JSON; `--out` also writes it — Task 4's env leg reads that file) and `render --queue Q --today YYYY-MM-DD` (fixed-format lines Task 5's brief lifts verbatim). Missing/invalid queue → `render` prints `📊 Gate acceptance: metrics unavailable (<why>)` and exits 0; `report` exits 1 (a machine consumer must not get half-data).

- [ ] **Step 1: Write the failing tests**

```python
# append to Projects/aios/engine/tools/tests/test_gate_metrics.py

def _mkqueue(tmp_path, items):
    p = tmp_path / "queue.json"
    p.write_text(json.dumps({"queue": items}), encoding="utf-8")
    return str(p)

def _run(argv):
    return subprocess.run([sys.executable, os.path.join(TOOLS, "gate_metrics.py")] + argv,
                          capture_output=True, text=True)

def test_cli_report_writes_out_and_prints_json(tmp_path):
    q = _mkqueue(tmp_path, [_item(history=[{"ts": "2026-07-14T00:00:00Z", "stage": "shipped",
                                            "approved_by": "auto-ship"}])])
    out = str(tmp_path / "gate-metrics.json")
    r = _run(["report", "--queue", q, "--today", "2026-07-15", "--out", out])
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["windows"]["7d"]["totals"]["accepted"] == 1
    assert json.load(open(out, encoding="utf-8")) == payload

def test_cli_render_fixed_format(tmp_path):
    q = _mkqueue(tmp_path, [
        _item(history=[{"ts": "2026-07-14T00:00:00Z", "stage": "shipped", "approved_by": "auto-ship"}]),
        _item(stage="rejected", recommended="approve",
              history=[{"ts": "2026-07-13T00:00:00Z", "stage": "rejected", "decided_by": "human"}]),
    ])
    r = _run(["render", "--queue", q, "--today", "2026-07-15"])
    assert r.returncode == 0
    assert "📊 Gate acceptance (30d): 50% accepted (n=2: 1 ship / 1 reject / 0 revert)" in r.stdout
    assert "human 1 / auto 1 / sched 0 / unk 0" in r.stdout
    assert "overrides (30d): 1 — x" in r.stdout

def test_cli_render_missing_queue_is_loud_not_zeros(tmp_path):
    r = _run(["render", "--queue", str(tmp_path / "absent.json"), "--today", "2026-07-15"])
    assert r.returncode == 0
    assert "metrics unavailable" in r.stdout
    assert "0%" not in r.stdout

def test_cli_report_missing_queue_exits_nonzero(tmp_path):
    r = _run(["report", "--queue", str(tmp_path / "absent.json"), "--today", "2026-07-15"])
    assert r.returncode == 1
```

Note: `queue_tx.load` treats a missing file as a legitimately-empty store (`queue_tx.py:229-230`) — the CLI must therefore check `os.path.isfile(queue_path)` itself to distinguish "absent" (unavailable) from "empty" (n=0 is a true zero).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Projects/aios && python -m pytest engine/tools/tests/test_gate_metrics.py -q`
Expected: the 4 new tests FAIL (no CLI yet); Task-1 tests still PASS

- [ ] **Step 3: Append the CLI implementation**

```python
# append to Projects/aios/engine/tools/gate_metrics.py

def _render_lines(rep):
    w = rep["windows"]["30d"]
    n = w["n"]
    if n == 0:
        head = "📊 Gate acceptance (30d): no terminal decisions in window"
    else:
        t = w["totals"]
        pct = round(100 * t["accepted"] / n)
        d = w["deciders"]
        head = (f"📊 Gate acceptance (30d): {pct}% accepted "
                f"(n={n}: {t['accepted']} ship / {t['rejected']} reject / {t['reverted']} revert) · "
                f"human {d['human']} / auto {d['auto']} / sched {d['scheduled']} / unk {d['unknown']}")
    lines = [head]
    ov = w["agreement"]["override"]
    if ov:
        ids = ", ".join(w["override_ids"])
        more = "" if ov <= len(w["override_ids"]) else f" (+{ov - len(w['override_ids'])} more)"
        lines.append(f"   recommendation overrides (30d): {ov} — {ids}{more}")
    if w["unknown_ts"] or rep["windows"]["all"]["unknown_ts"]:
        lines.append(f"   ℹ {rep['windows']['all']['unknown_ts']} decisions lack a terminal timestamp (all-time bucket only)")
    return lines


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="gate_metrics.py",
                                 description="A73 read-only gate acceptance metrics.")
    sub = ap.add_subparsers(dest="op", required=True)
    for name in ("report", "render"):
        p = sub.add_parser(name)
        p.add_argument("--queue", required=True)
        p.add_argument("--today", required=True, help="YYYY-MM-DD (injected; no wall-clock)")
        if name == "report":
            p.add_argument("--out", default=None, help="also write the JSON here (Task-4 env leg reads it)")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.queue):
        if args.op == "render":
            print("📊 Gate acceptance: metrics unavailable (queue not found)")
            return 0
        print(json.dumps({"error": f"queue not found: {args.queue}"}), file=sys.stderr)
        return 1
    rep = report(args.queue, args.today)
    if args.op == "report":
        text = json.dumps(rep, indent=2, ensure_ascii=False)
        if args.out:
            d = os.path.dirname(args.out)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text)
        print(text)
    else:
        print("\n".join(_render_lines(rep)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Projects/aios && python -m pytest engine/tools/tests/test_gate_metrics.py -q`
Expected: all PASS

- [ ] **Step 5: Commit (aios repo)**

```bash
cd Projects/aios
git add engine/tools/gate_metrics.py engine/tools/tests/test_gate_metrics.py
git commit -m "feat(A73): gate_metrics CLI — report --out + deterministic render, loud-unavailable"
```

---

### Task 3: `ship.py` writes normalized `decided_by` at flip

**Files:**
- Modify: `Projects/aios/engine/tools/ship.py:196` (ship flip), `ship.py:210-217` (reject), `ship.py:249-255` (reject CLI)
- Modify: `Projects/aios/skills/gate/SKILL.md` (one line, see Step 3)
- Test: `Projects/aios/engine/tools/tests/test_gate_metrics.py` (append — keeps A73 assertions in one file)

**Interfaces:**
- Consumes: existing `ship(..., human_approved=False)` and `reject(queue_path, cid, reason)`.
- Produces: every new terminal history entry carries `decided_by` ∈ {`human`,`auto`,`scheduled`}; `reject` gains `decided_by="auto"` keyword + `--decided-by {human,auto}` CLI flag. Existing history is NEVER rewritten.

- [ ] **Step 1: Write the failing tests**

```python
# append to Projects/aios/engine/tools/tests/test_gate_metrics.py

def test_ship_derive_decided_by():
    import ship as shiptool
    assert shiptool._derive_decided_by("auto-ship", False) == "auto"
    assert shiptool._derive_decided_by("auto-ship-scheduled", False) == "scheduled"
    assert shiptool._derive_decided_by("a-person", True) == "human"
    assert shiptool._derive_decided_by("a-person", False) == "human"  # named approver => human even w/o flag

def test_reject_records_decided_by(tmp_path):
    q = _mkqueue(tmp_path, [{"id": "r1", "stage": "awaiting", "lane": "review",
                             "recommended": "reject", "kb": "demo", "history": []}])
    import ship as shiptool
    shiptool.reject(q, "r1", "no draft", decided_by="human")
    item = json.load(open(q, encoding="utf-8"))["queue"][0]
    assert item["stage"] == "rejected"
    assert item["history"][-1]["decided_by"] == "human"
    assert item["history"][-1]["reason"] == "no draft"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Projects/aios && python -m pytest engine/tools/tests/test_gate_metrics.py -q -k "decided_by or derive"`
Expected: FAIL with `AttributeError: module 'ship' has no attribute '_derive_decided_by'`

- [ ] **Step 3: Implement**

In `ship.py`, add above `_flip` (near line 139):

```python
def _derive_decided_by(approved_by, human_approved):
    """A73 normalized decider stamp. approved_by is either an auto constant or the approver's
    name (gate SKILL contract) — any named approver is a human decision even on a non-review
    lane. Free-text approved_by stays for audit; history is never rewritten."""
    if human_approved:
        return "human"
    if approved_by == "auto-ship-scheduled":
        return "scheduled"
    if approved_by == "auto-ship":
        return "auto"
    return "human"
```

Change the ship flip at line 196:

```python
    _flip(queue_path, item, "shipped",
          {"approved_by": approved_by,
           "decided_by": _derive_decided_by(approved_by, human_approved)})
```

Change `reject` (line 210) signature + flip:

```python
def reject(queue_path, cid, reason, decided_by="auto"):
    item = _find_item(queue_path, cid)
    if item.get("stage") in ("shipped", "reverted"):
        _die(f"id {cid!r} is at terminal stage {item.get('stage')!r} — rejecting it would orphan "
             f"its vault file; use `rewind.py undo-ship` first")
    _flip(queue_path, item, "rejected", {"reason": reason, "decided_by": decided_by})
    print(json.dumps({"ok": True, "id": cid, "stage": "rejected", "reason": reason},
                     ensure_ascii=False))
```

CLI (line 249-250 block):

```python
    pj = sub.add_parser("reject"); common(pj, vault=False)
    pj.add_argument("--reason", required=True)
    pj.add_argument("--decided-by", choices=("human", "auto"), default="auto",
                    help="A73: who decided this reject (manual gate passes human)")
```

and the dispatch (line 253-255):

```python
    if args.op == "reject":
        reject(args.queue, args.id, args.reason, decided_by=args.decided_by)
        return 0
```

In `skills/gate/SKILL.md`, in the step-5 invocation block, extend the reject example line:

```
   python "${CLAUDE_PLUGIN_ROOT}/engine/tools/ship.py" reject --queue "<env_root>/state/queue.json" \
     --id <id> --reason "<the BLOCK reason>" --decided-by <human if a person rejected | auto for a review BLOCK>
```

- [ ] **Step 4: Run tests — new ones pass, no regressions in touched suites**

Run: `cd Projects/aios && python -m pytest engine/tools/tests/test_gate_metrics.py engine/tools/tests/test_ship.py -q`
Expected: all PASS (test_ship's existing reject tests use positional args — unchanged default keeps them green)

- [ ] **Step 5: Commit (aios repo)**

```bash
cd Projects/aios
git add engine/tools/ship.py engine/tools/tests/test_gate_metrics.py skills/gate/SKILL.md
git commit -m "feat(A73): normalized decided_by stamped at ship/reject flip"
```

---

### Task 4: `factory_standup.py` — `collect_acceptance` (env repo)

**Files:**
- Modify: `Scripts/factory-gate/factory_standup.py` (new function + wire into `collect()` at line 105-107, new `main` flag)
- Test: `Scripts/factory-gate/tests/test_factory_standup.py` (append)

**Interfaces:**
- Consumes: `fzg.discover_backlogs(root)`, `cg.parse_items(text)` (status/body/criteria — same as `collect()`), the H62 spend ledgers (`state/factory/spend-<date>.json`, `state/task-logs/<id>/spend-<date>.json`), and (optional) the gate-metrics JSON written by Task 2 (`state/factory/gate-metrics.json`).
- Produces: `collect()` output gains `"acceptance": {"window_days": 30, "factory": {"accepted": n, "reverted": n, "unknown_sha": n, "spend_usd": x, "usd_per_accepted": x|None, "reverted_ids": [...]}, "gate": {...from gate-metrics 30d totals + usd...} | {"note": "..."}}` — Task 5's renderer reads exactly this.

- [ ] **Step 1: Write the failing tests**

```python
# append to Scripts/factory-gate/tests/test_factory_standup.py
# (follow the file's existing fixture style for building a fake root; the essentials:)

def _mk_repo_with_backlog(tmp_path, name, done_lines, reverts=()):
    """Create <root>/<name> as a REAL git repo with a BACKLOG.md whose ## Done holds done_lines;
    make one commit per entry; then `git revert` the SHAs named by index in `reverts`."""
    import subprocess
    repo = tmp_path / name; repo.mkdir()
    def g(*a): subprocess.run(["git", "-C", str(repo)] + list(a), check=True, capture_output=True)
    g("init"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
    shas = []
    for i, _ in enumerate(done_lines):
        (repo / f"f{i}.txt").write_text(str(i))
        g("add", "."); g("commit", "-m", f"work {i}")
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, check=True)
        shas.append(out.stdout.strip())
    for i in reverts:
        g("revert", "--no-edit", shas[i])
    body = "\n".join(l.format(*shas) for l in done_lines)
    (repo / "BACKLOG.md").write_text(
        "# B\n\n## Open (order is priority)\n\n## Done\n" + body + "\n", encoding="utf-8")
    return repo, shas

def test_acceptance_counts_reverted_and_unknown(tmp_path, monkeypatch):
    import factory_standup as fs
    repo, shas = _mk_repo_with_backlog(tmp_path, "proj", [
        "- [x] **X1** — thing one (✅ 2026-07-14, {0} — VETO)",
        "- [x] **X2** — thing two (✅ 2026-07-13, {1} — VETO)",
        "- [x] **X3** — no sha here (✅ 2026-07-12, <see other git> — VETO)",
    ], reverts=(1,))
    monkeypatch.setattr(fs.fzg, "discover_backlogs", lambda root: [str(repo / "BACKLOG.md")])
    acc = fs.collect_acceptance(str(tmp_path), "2026-07-15", window_days=30)
    f = acc["factory"]
    assert f["accepted"] == 1 and f["reverted"] == 1 and f["unknown_sha"] == 1
    assert f["reverted_ids"] == ["X2"]

def test_acceptance_window_excludes_old_done_lines(tmp_path, monkeypatch):
    import factory_standup as fs
    repo, shas = _mk_repo_with_backlog(tmp_path, "proj", [
        "- [x] **X1** — old (✅ 2026-01-01, {0} — VETO)",
    ])
    monkeypatch.setattr(fs.fzg, "discover_backlogs", lambda root: [str(repo / "BACKLOG.md")])
    acc = fs.collect_acceptance(str(tmp_path), "2026-07-15", window_days=30)
    assert acc["factory"] == {"accepted": 0, "reverted": 0, "unknown_sha": 0,
                              "spend_usd": 0.0, "usd_per_accepted": None, "reverted_ids": []}

def test_acceptance_gate_block_from_metrics_json(tmp_path, monkeypatch):
    import factory_standup as fs, json as _json
    monkeypatch.setattr(fs.fzg, "discover_backlogs", lambda root: [])
    gm = tmp_path / "state" / "factory" / "gate-metrics.json"
    gm.parent.mkdir(parents=True)
    gm.write_text(_json.dumps({"windows": {"30d": {"n": 10, "totals":
        {"accepted": 9, "rejected": 1, "reverted": 0}}}}), encoding="utf-8")
    acc = fs.collect_acceptance(str(tmp_path), "2026-07-15", window_days=30)
    assert acc["gate"]["accepted"] == 9 and acc["gate"]["n"] == 10

def test_acceptance_gate_note_when_metrics_absent(tmp_path, monkeypatch):
    import factory_standup as fs
    monkeypatch.setattr(fs.fzg, "discover_backlogs", lambda root: [])
    acc = fs.collect_acceptance(str(tmp_path), "2026-07-15", window_days=30)
    assert "note" in acc["gate"]

def test_collect_includes_acceptance_block(tmp_path, monkeypatch):
    import factory_standup as fs
    monkeypatch.setattr(fs.fzg, "discover_backlogs", lambda root: [])
    data = fs.collect(str(tmp_path), "2026-07-15")
    assert "acceptance" in data and data["acceptance"]["window_days"] == 30
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Scripts/factory-gate && python -m pytest tests/test_factory_standup.py -q -k acceptance`
Expected: FAIL with `AttributeError: module 'factory_standup' has no attribute 'collect_acceptance'`

- [ ] **Step 3: Implement**

Add to `factory_standup.py` (after `collect_spend`, before `collect`):

```python
_SHA_TOKEN = re.compile(r"\b[0-9a-f]{7,40}\b")
_DONE_DATE = re.compile(r"✅\s*(?P<date>\d{4}-\d{2}-\d{2})")


def _window_spend(root, today, window_days, sources_prefixes):
    """Sum cost_usd over the window from ledger files whose filename date falls inside it.
    sources_prefixes: ('factory',) for drain spend, ('task-logs',) walks task dirs."""
    from datetime import date
    def _within(d):
        try:
            y, m, dd = (int(x) for x in d.split("-")); ty, tm, td = (int(x) for x in today.split("-"))
            return 0 <= (date(ty, tm, td) - date(y, m, dd)).days <= window_days
        except (ValueError, TypeError):
            return False
    total = 0.0
    dirs = []
    if "factory" in sources_prefixes:
        dirs.append(os.path.join(root, "state", "factory"))
    if "task-logs" in sources_prefixes:
        tl = os.path.join(root, "state", "task-logs")
        try:
            dirs.extend(os.path.join(tl, n) for n in sorted(os.listdir(tl)))
        except OSError:
            pass
    for d in dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            if not (name.startswith("spend-") and name.endswith(".json")):
                continue
            if not _within(name[len("spend-"):-len(".json")]):
                continue
            try:
                with open(os.path.join(d, name), encoding="utf-8") as fh:
                    total += float(json.load(fh).get("cost_usd", 0.0))
            except (OSError, ValueError, TypeError):
                continue
    return round(total, 4)


def collect_acceptance(root, today, window_days=30):
    """A73: factory ship-survival (Done-line SHAs vs `git revert` scan) + gate acceptance
    (read from the gate-metrics JSON Task 2's `report --out` writes) + $/accepted joins.
    Same best-effort/exit-0 posture as collect_spend: unreadable inputs contribute to
    surfaced `unknown` buckets or notes, never raise."""
    import subprocess
    from datetime import date
    def _within(d):
        try:
            y, m, dd = (int(x) for x in d.split("-")); ty, tm, td = (int(x) for x in today.split("-"))
            return 0 <= (date(ty, tm, td) - date(y, m, dd)).days <= window_days
        except (ValueError, TypeError):
            return False

    accepted, reverted, unknown, reverted_ids = 0, 0, 0, []
    for bl in fzg.discover_backlogs(root):
        repo_dir = os.path.dirname(os.path.abspath(bl))
        # windowed Done lines -> short SHAs
        entries = []  # (id, [shas])
        try:
            with open(bl, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            for it in cg.parse_items(text):
                if it["status"] != "done":
                    continue
                line = it["body"] + " " + " ".join(it["criteria"])
                dm = _DONE_DATE.search(line)
                if not dm or not _within(dm.group("date")):
                    continue
                entries.append((it["id"], _SHA_TOKEN.findall(line)))
        except Exception:
            continue
        if not entries:
            continue
        # one revert scan per repo: full SHAs of everything a `git revert` commit reverts
        reverted_full = set()
        try:
            out = subprocess.run(
                ["git", "-C", repo_dir, "log", "--grep", "This reverts commit", "--format=%B%x00"],
                capture_output=True, text=True, timeout=60)
            if out.returncode == 0:
                for m in re.finditer(r"This reverts commit ([0-9a-f]{7,40})", out.stdout):
                    reverted_full.add(m.group(1))
        except (OSError, subprocess.SubprocessError):
            pass  # repo unreadable -> its shas fall through as accepted-with-unknowns below
        for item_id, shas in entries:
            if not shas:
                unknown += 1
                continue
            hit = any(full.startswith(s) for s in shas for full in reverted_full)
            if hit:
                reverted += 1
                reverted_ids.append(item_id)
            else:
                accepted += 1

    drain_spend = _window_spend(root, today, window_days, ("factory",))
    factory = {"accepted": accepted, "reverted": reverted, "unknown_sha": unknown,
               "spend_usd": drain_spend,
               "usd_per_accepted": round(drain_spend / accepted, 4) if accepted else None,
               "reverted_ids": reverted_ids}

    gm_path = os.path.join(root, "state", "factory", "gate-metrics.json")
    try:
        with open(gm_path, encoding="utf-8") as fh:
            w = json.load(fh)["windows"]["30d"]
        pipe_spend = _window_spend(root, today, window_days, ("task-logs",))
        acc_n = int(w["totals"]["accepted"])
        gate = {"n": int(w["n"]), "accepted": acc_n,
                "rejected": int(w["totals"]["rejected"]), "reverted": int(w["totals"]["reverted"]),
                "spend_usd": pipe_spend,
                "usd_per_accepted": round(pipe_spend / acc_n, 4) if acc_n else None}
    except (OSError, ValueError, TypeError, KeyError):
        gate = {"note": "gate metrics JSON absent — run gate_metrics.py report --out state/factory/gate-metrics.json"}

    return {"window_days": window_days, "factory": factory, "gate": gate}
```

Wire into `collect()` — change the return (lines 105-107) to:

```python
    return {"generated": today, "groups": groups,
            "totals": {k: len(v) for k, v in groups.items()}, "errors": errors,
            "spend": collect_spend(root, today, cap),
            "acceptance": collect_acceptance(root, today)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Scripts/factory-gate && python -m pytest tests -q`
Expected: all PASS (full factory-gate suite — the existing 33+ tests must stay green; `collect()` fixtures gain an `acceptance` key, which existing assertions index into rather than equality-compare — if any test equality-compares the whole dict, update it to include the new key)

- [ ] **Step 5: Commit (env repo, from `Documents/Claude`)**

```bash
git add Scripts/factory-gate/factory_standup.py Scripts/factory-gate/tests/test_factory_standup.py
git diff --cached --stat   # confirm ONLY these two paths are staged
git commit -m "feat(A73): standup collect_acceptance — Done-line SHA revert scan + \$/accepted joins"
```

---

### Task 5: brief render + gather wiring (aios repo)

**Files:**
- Modify: `Projects/aios/engine/tools/brief_render.py:308-316` (extend `render_factory_standup`)
- Modify: `Projects/aios/skills/brief/gather.md` (add the gate_metrics invocation beside the existing standup/factory-health reads — find the step that reads `state/factory/standup.json` and add the two commands below it)
- Test: `Projects/aios/engine/tools/tests/test_brief_render.py` (append)

**Interfaces:**
- Consumes: `standup.json` `acceptance` block (Task 4 shape) via the existing `render_factory_standup(data)`.
- Produces: two fixed-format panel lines; gather.md instructs the engine to run
  `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/gate_metrics.py" report --queue "<env_root>/state/queue.json" --today <today> --out "<env_root>/state/factory/gate-metrics.json"` then `render` (lifted verbatim), before the standup collector output is rendered.

- [ ] **Step 1: Write the failing tests**

```python
# append to Projects/aios/engine/tools/tests/test_brief_render.py (follow its import style)

def test_standup_renders_acceptance_lines():
    data = {"groups": {}, "errors": [], "spend": {},
            "acceptance": {"window_days": 30,
                           "factory": {"accepted": 12, "reverted": 1, "unknown_sha": 2,
                                       "spend_usd": 49.4, "usd_per_accepted": 4.12,
                                       "reverted_ids": ["A65"]},
                           "gate": {"n": 123, "accepted": 113, "rejected": 8, "reverted": 2,
                                    "spend_usd": 68.9, "usd_per_accepted": 0.61}}}
    out = brief_render.render_factory_standup(data)
    assert "📊 factory acceptance (30d): 12 shipped / 1 reverted / 2 unknown-sha → $4.12/accepted" in out
    assert "reverted: A65" in out
    assert "📊 gate acceptance (30d): 92% (113/123) · $0.61/accepted" in out

def test_standup_acceptance_gate_note_renders_loud():
    data = {"groups": {}, "errors": [], "spend": {},
            "acceptance": {"window_days": 30,
                           "factory": {"accepted": 0, "reverted": 0, "unknown_sha": 0,
                                       "spend_usd": 0.0, "usd_per_accepted": None, "reverted_ids": []},
                           "gate": {"note": "gate metrics JSON absent — run gate_metrics.py report"}}}
    out = brief_render.render_factory_standup(data)
    assert "gate acceptance: unavailable — gate metrics JSON absent" in out

def test_standup_empty_state_line_unchanged_without_acceptance():
    out = brief_render.render_factory_standup({"groups": {}, "errors": [], "spend": {}})
    assert out == "🏭 Factory Standup — nothing waiting (backlogs drained clean)."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Projects/aios && python -m pytest engine/tools/tests/test_brief_render.py -q -k acceptance`
Expected: FAIL (lines not rendered)

- [ ] **Step 3: Implement**

In `render_factory_standup` (brief_render.py), after the `has_spend` block (line 315), add — and extend the empty-state guard at line 290 to also check `data.get("acceptance")` has content:

```python
    acc = data.get("acceptance") or {}
    fa, ga = acc.get("factory"), acc.get("gate")
    if fa:
        w = acc.get("window_days", 30)
        line = (f"  📊 factory acceptance ({w}d): {fa.get('accepted', 0)} shipped / "
                f"{fa.get('reverted', 0)} reverted / {fa.get('unknown_sha', 0)} unknown-sha")
        if fa.get("usd_per_accepted") is not None:
            line += f" → ${fa['usd_per_accepted']:,.2f}/accepted"
        lines.append(line)
        if fa.get("reverted_ids"):
            lines.append("     reverted: " + ", ".join(fa["reverted_ids"]))
    if ga:
        w = acc.get("window_days", 30)
        if "note" in ga:
            lines.append(f"  📊 gate acceptance: unavailable — {ga['note']}")
        elif ga.get("n"):
            pct = round(100 * ga.get("accepted", 0) / ga["n"])
            line = f"  📊 gate acceptance ({w}d): {pct}% ({ga.get('accepted', 0)}/{ga['n']})"
            if ga.get("usd_per_accepted") is not None:
                line += f" · ${ga['usd_per_accepted']:,.2f}/accepted"
            lines.append(line)
```

Empty-state guard change (line 289-291):

```python
    has_spend = bool(sp.get("output_tokens") or sp.get("cost_usd"))
    acc0 = data.get("acceptance") or {}
    has_acc = bool(acc0.get("factory") or acc0.get("gate"))
    if not any(g.get(k) for k in ("veto", "needs_you", "handed_off", "stuck")) and not errs \
            and not has_spend and not has_acc:
        return "🏭 Factory Standup — nothing waiting (backlogs drained clean)."
```

In `skills/brief/gather.md`: locate the step that reads `state/factory/standup.json` for the Dev slice and insert directly before it:

```
Run the A73 gate-acceptance metrics (deterministic; lift `render` output verbatim):
  python "${CLAUDE_PLUGIN_ROOT}/engine/tools/gate_metrics.py" report --queue "<env_root>/state/queue.json" \
    --today <today> --out "<env_root>/state/factory/gate-metrics.json"
  python "${CLAUDE_PLUGIN_ROOT}/engine/tools/gate_metrics.py" render --queue "<env_root>/state/queue.json" --today <today>
If the tool prints "metrics unavailable", lift that line as-is — never substitute zeros.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Projects/aios && python -m pytest engine/tools/tests/test_brief_render.py engine/tools/tests/test_gate_metrics.py -q`
Expected: all PASS (existing brief_render tests untouched and green)

- [ ] **Step 5: Commit (aios repo)**

```bash
cd Projects/aios
git add engine/tools/brief_render.py engine/tools/tests/test_brief_render.py skills/brief/gather.md
git commit -m "feat(A73): standup panel renders factory+gate acceptance; gather runs gate_metrics"
```

---

### Task 6: Live acceptance run + suites + close-out

**Files:**
- Modify: `Projects/aios/BACKLOG.md` (A73 → `## Done` one-liner on success)

- [ ] **Step 1: Live gate metrics over the real queue (acceptance evidence — show output)**

Run from `Documents/Claude`:
```bash
python Projects/aios/engine/tools/gate_metrics.py report --queue state/queue.json --today 2026-07-15 --out state/factory/gate-metrics.json | python -c "import json,sys; d=json.load(sys.stdin); w=d['windows']['all']; print(w['n'], w['totals'], w['deciders'], w['agreement'])"
python Projects/aios/engine/tools/gate_metrics.py render --queue state/queue.json --today 2026-07-15
```
Expected: all-time n ≈ 1146+ (811+ shipped / 335+ rejected), deciders split ≈ human/auto/scheduled/unknown consistent with the spec's Leg-3 vocabulary counts; render prints the 30d line.

- [ ] **Step 2: Live standup with acceptance (show output)**

```bash
python Scripts/factory-gate/factory_standup.py --root . --today 2026-07-15 --out state/factory/standup.json
python -c "import json; print(json.dumps(json.load(open('state/factory/standup.json'))['acceptance'], indent=2))"
```
Expected: `factory` block with real windowed Done-line counts (revert scan run against the live repos; `unknown_sha` > 0 is fine and listed), `gate` block populated from Step 1's JSON.

- [ ] **Step 3: Full suites, known-red fence applied**

```bash
cd Projects/aios && python -m pytest -q
```
Expected: **exactly 1 pre-existing failure** (`test_domain_mirror.py` FO-drift — A72, tracked); every other test green incl. the new `test_gate_metrics.py`. Any OTHER failure blocks.
```bash
cd ../../Scripts/factory-gate && python -m pytest tests -q
```
Expected: all PASS.

- [ ] **Step 4: Fresh-context review gate**

Dispatch a fresh-context reviewer (not the builder) over both diffs (`git diff main~N` per repo) against the spec; production-state adjacent → run the saved `review-gate` workflow if available, else the single fresh reviewer + `differential-review` on the diff. CRITICAL findings loop back to a fix pass. Show the verdict.

- [ ] **Step 5: Close out A73**

Move the A73 Open item to `## Done` as one line (id, headline, ✅ date, closing commits), append the live-verification tails (first unattended standup tick renders the acceptance block; first brief render shows the 📊 lines) to `## Watching`, commit both repos, push.

---

## Self-review notes (run during plan-writing)

- **Spec coverage:** C1→Tasks 1-2, C2/C3→Task 4, C4→Task 5 + gather wiring, C5→Task 3, acceptance evidence→Task 6. Error-handling section→Task 2 loud-unavailable + Task 4 best-effort buckets + Task 5 note rendering. ✓
- **Type consistency:** `collect_acceptance` return shape (Task 4 Produces) matches Task 5's renderer reads and Task 4's tests; `report --out` path `state/factory/gate-metrics.json` consistent across Tasks 2/4/5/6. `_derive_decided_by` name consistent Task 3 test/impl. ✓
- **No placeholders:** every code step shows the code; gather.md insertion text given verbatim with an anchor rule. ✓
