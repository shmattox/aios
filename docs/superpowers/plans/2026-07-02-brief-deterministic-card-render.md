# Deterministic Brief Card Render — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the brief's per-item card — especially the `🔵 Your system / 🟠 Claude` two-layer block — impossible to drop, by rendering it from a deterministic engine function that the skill lifts verbatim.

**Architecture:** `brief-cache.json` is the single source of truth (LLM-populated data, gated by `validate_cache`). A new pure function `brief_render.py` turns a cache item into card markdown; the trigger brief echoes that output verbatim and only attaches host-native buttons. The precompute stops hand-authoring card markdown.

**Tech Stack:** Python 3 stdlib only (`json`, `sys`), pytest (existing suite convention: each `test_*.py` has a `__main__` self-runner; `suite_test.py` runs each as a subprocess). Markdown skill/deploy files.

## Global Constraints

- Pure stdlib only in `brief_render.py` — no third-party imports, no network, no LLM call. Copied verbatim from spec §5.1.
- The `🟠 Claude` line is **always** emitted; the `🔵` line is emitted per `system_voice.grade` using the table below. Grade 0 / null → `— your system is silent —`, no blue line. (spec §5.1)
- Graded-voice render strings are **exact** (SKILL.md:275–283):
  - Grade 1: `🔵 **Your system says** *(Grade 1 — solid)*: {text} — cite: {cite}`
  - Grade 2a: `🔵 *Your system's logic implies* *(Grade 2a — precedent)*: {text} — by {cite}`
  - Grade 2b: `🔵 *Loosely, by your {rule}* *(Grade 2b — principle)*: {text}`  (rule = `cite`)
  - Grade 0/null: `— *your system is silent* —`
  - Claude: `🟠 **Claude**: {text}`
- Valid grades: `{"1","2a","2b"}`; `null` = Grade 0. `cite` required for grades 1 and 2a, optional for 2b (matches `brief_session.py` `VALID_GRADES` + `validate_cache`).
- Voice fields resolve **item-level first, then under `recommended`** (two cache item shapes; spec §9.1).
- Suite must stay green: `python -m pytest engine/tools/tests/ -q` (currently 9 files → 10 after this).
- No `harness`/`Cowork`/`FUSE`/`Bayview`/`Seth` strings in new code/tests (sanitize rule, BACKLOG A5).

---

### Task 1: Voice-line renderers + resolver

**Files:**
- Create: `engine/tools/brief_render.py`
- Test: `engine/tools/tests/test_brief_render.py`

**Interfaces:**
- Produces: `render_system_line(sv: dict | None) -> str`, `render_claude_line(cv: dict | None) -> str`, `_voice(item: dict, key: str) -> dict | None`

- [ ] **Step 1: Write the failing test**

```python
# engine/tools/tests/test_brief_render.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import brief_render as R


def test_system_line_grade1():
    sv = {"grade": "1", "text": "Ship it.", "cite": "decisions.md#x"}
    assert R.render_system_line(sv) == \
        "🔵 **Your system says** *(Grade 1 — solid)*: Ship it. — cite: decisions.md#x"


def test_system_line_grade2a():
    sv = {"grade": "2a", "text": "Probably hold.", "cite": "your 2026-06 call"}
    assert R.render_system_line(sv) == \
        "🔵 *Your system's logic implies* *(Grade 2a — precedent)*: Probably hold. — by your 2026-06 call"


def test_system_line_grade2b_uses_cite_as_rule():
    sv = {"grade": "2b", "text": "Lean conservative.", "cite": "Paper-Governs"}
    assert R.render_system_line(sv) == \
        "🔵 *Loosely, by your Paper-Governs* *(Grade 2b — principle)*: Lean conservative."


def test_system_line_grade0_and_null_are_silent():
    assert R.render_system_line(None) == "— *your system is silent* —"
    assert R.render_system_line({"grade": None}) == "— *your system is silent* —"


def test_claude_line_always_present():
    assert R.render_claude_line({"text": "Industry default is X."}) == \
        "🟠 **Claude**: Industry default is X."


def test_voice_resolves_item_level_then_recommended():
    station_item = {"system_voice": {"grade": "1", "text": "a", "cite": "c"}}
    assert R._voice(station_item, "system_voice")["text"] == "a"
    act_item = {"recommended": {"system_voice": {"grade": "1", "text": "b", "cite": "c"}}}
    assert R._voice(act_item, "system_voice")["text"] == "b"
    assert R._voice({}, "system_voice") is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest engine/tools/tests/test_brief_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brief_render'`

- [ ] **Step 3: Write minimal implementation**

```python
# engine/tools/brief_render.py
"""Deterministic renderer: brief-cache item -> card markdown.

Pure stdlib. No LLM, no network. The card format lives HERE, not in skill prose,
so it cannot drift between renders or surfaces. See
docs/superpowers/specs/2026-07-02-brief-deterministic-card-render-design.md
"""
import json
import sys

VALID_GRADES = {"1", "2a", "2b"}


def _voice(item, key):
    """Resolve system_voice/claude_voice from item-level (station items) or,
    failing that, nested under 'recommended' (Act-vs-Track items)."""
    v = item.get(key)
    if v is None and isinstance(item.get("recommended"), dict):
        v = item["recommended"].get(key)
    return v


def render_system_line(sv):
    """Blue line per grade. sv is None (Grade 0) or {grade, text, cite}."""
    if not sv or sv.get("grade") not in VALID_GRADES:
        return "— *your system is silent* —"
    grade = sv["grade"]
    text = sv.get("text", "")
    cite = sv.get("cite")
    if grade == "1":
        return f"🔵 **Your system says** *(Grade 1 — solid)*: {text} — cite: {cite}"
    if grade == "2a":
        return f"🔵 *Your system's logic implies* *(Grade 2a — precedent)*: {text} — by {cite}"
    # grade == "2b"
    rule = cite or "your principle"
    return f"🔵 *Loosely, by your {rule}* *(Grade 2b — principle)*: {text}"


def render_claude_line(cv):
    """Orange line — ALWAYS present."""
    text = (cv or {}).get("text", "")
    return f"🟠 **Claude**: {text}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest engine/tools/tests/test_brief_render.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add engine/tools/brief_render.py engine/tools/tests/test_brief_render.py
git commit -m "brief_render: graded system/claude voice lines + voice resolver"
```

---

### Task 2: `render_card` — the full per-item card

**Files:**
- Modify: `engine/tools/brief_render.py`
- Test: `engine/tools/tests/test_brief_render.py` (append)

**Interfaces:**
- Consumes: `render_system_line`, `render_claude_line`, `_voice` (Task 1)
- Produces: `render_card(item: dict) -> str`

- [ ] **Step 1: Write the failing test** (append to `test_brief_render.py`)

```python
def test_render_card_station_item_minimal():
    item = {
        "item_id": "sys-1", "title": "Fix the sorter taxonomy", "domain": "System",
        "system_voice": {"grade": "1", "text": "Hold, then re-drive.", "cite": "decisions.md#taxo"},
        "claude_voice": {"text": "Batch the 8 stubs."},
    }
    out = R.render_card(item)
    assert out.splitlines()[0] == "**Fix the sorter taxonomy**  [System]"
    assert "🔵 **Your system says**" in out
    assert out.strip().endswith("🟠 **Claude**: Batch the 8 stubs.")
    # optional lines absent when data absent
    assert "Urgency:" not in out


def test_render_card_act_item_full_and_nested_voice():
    item = {
        "id": "fo-2", "title": "Refi decision", "domain": "Family Office",
        "urgency": "closes Fri", "your_playbook": "sale leads, nothing locked",
        "flags": ["Paper-Governs"],
        "recommended": {
            "system_voice": {"grade": "2a", "text": "Wait.", "cite": "your May call"},
            "claude_voice": {"text": "Lock the rate."},
        },
    }
    out = R.render_card(item)
    assert "**Refi decision**  [Family Office]" in out
    assert "- Urgency: closes Fri" in out
    assert "- Your playbook: sale leads, nothing locked" in out
    assert "- Flags: Paper-Governs" in out
    assert "🔵 *Your system's logic implies*" in out
    assert "🟠 **Claude**: Lock the rate." in out


def test_render_card_silent_system_still_has_claude():
    item = {"title": "T", "domain": "Dev", "system_voice": None,
            "claude_voice": {"text": "c"}}
    out = R.render_card(item)
    assert "— *your system is silent* —" in out
    assert "🟠 **Claude**: c" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest engine/tools/tests/test_brief_render.py -k render_card -v`
Expected: FAIL — `AttributeError: module 'brief_render' has no attribute 'render_card'`

- [ ] **Step 3: Write minimal implementation** (append to `brief_render.py`)

```python
def render_card(item):
    """Full per-item card markdown. The two-layer block is ALWAYS present;
    optional context lines (urgency/playbook/flags) appear only when the item
    carries that data (station items are minimal; Act-vs-Track items are full)."""
    title = item["title"]
    domain = item.get("domain", "")
    header = f"**{title}**  [{domain}]" if domain else f"**{title}**"
    lines = [header]
    if item.get("urgency"):
        lines.append(f"- Urgency: {item['urgency']}")
    if item.get("your_playbook"):
        lines.append(f"- Your playbook: {item['your_playbook']}")
    if item.get("flags"):
        flags = item["flags"]
        flags_str = ", ".join(flags) if isinstance(flags, list) else str(flags)
        lines.append(f"- Flags: {flags_str}")
    lines.append("")
    lines.append(render_system_line(_voice(item, "system_voice")))
    lines.append(render_claude_line(_voice(item, "claude_voice")))
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest engine/tools/tests/test_brief_render.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add engine/tools/brief_render.py engine/tools/tests/test_brief_render.py
git commit -m "brief_render: render_card assembles per-item card, two-layer block always present"
```

---

### Task 3: CLI + cache walking (`station` / `card`)

**Files:**
- Modify: `engine/tools/brief_render.py`
- Test: `engine/tools/tests/test_brief_render.py` (append)
- Create (fixture): `engine/tools/tests/fixtures/brief-cache.sample.json`

**Interfaces:**
- Consumes: `render_card` (Task 2)
- Produces: `render_station(cache: dict, station: str) -> str`, `render_card_by_id(cache: dict, item_id: str) -> str`, `main(argv: list[str]) -> int`

- [ ] **Step 1: Write the fixture**

```json
{
  "generated_utc": "2026-07-02T00:00:00Z",
  "stations": {
    "system": {"items": [
      {"item_id": "sys-1", "title": "Fix sorter taxonomy", "domain": "System",
       "system_voice": {"grade": "1", "text": "Hold, then re-drive.", "cite": "decisions.md#taxo"},
       "claude_voice": {"text": "Batch the 8 stubs."}}
    ]},
    "dev": {"items": [
      {"item_id": "dev-1", "title": "Renderer build", "domain": "Dev",
       "system_voice": null,
       "claude_voice": {"text": "Pure function, golden-file it."}}
    ]}
  }
}
```

- [ ] **Step 2: Write the failing test** (append)

```python
import os
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "brief-cache.sample.json")


def test_render_station_emits_all_cards():
    cache = R._load(FIX)
    out = R.render_station(cache, "system")
    assert "**Fix sorter taxonomy**  [System]" in out
    assert "🔵 **Your system says**" in out
    assert "🟠 **Claude**: Batch the 8 stubs." in out


def test_render_card_by_id_found_and_missing():
    cache = R._load(FIX)
    assert "Renderer build" in R.render_card_by_id(cache, "dev-1")
    import pytest
    with pytest.raises(KeyError):
        R.render_card_by_id(cache, "nope")


def test_render_station_is_byte_stable(tmp_path):
    cache = R._load(FIX)
    a = R.render_station(cache, "system")
    b = R.render_station(cache, "system")
    assert a == b  # deterministic
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest engine/tools/tests/test_brief_render.py -k "station or by_id" -v`
Expected: FAIL — `AttributeError: ... has no attribute '_load'`

- [ ] **Step 4: Write minimal implementation** (append to `brief_render.py`)

```python
def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _station_items(cache, station):
    node = cache.get("stations", {}).get(station, {})
    return node.get("items", []) if isinstance(node, dict) else []


def render_station(cache, station):
    return "\n\n".join(render_card(i) for i in _station_items(cache, station))


def render_card_by_id(cache, item_id):
    for node in cache.get("stations", {}).values():
        for it in (node.get("items", []) if isinstance(node, dict) else []):
            if it.get("item_id") == item_id or it.get("id") == item_id:
                return render_card(it)
    raise KeyError(item_id)


def main(argv):
    if len(argv) < 4:
        print("usage: brief_render.py {station|card} <cache.json> <station|item_id>",
              file=sys.stderr)
        return 2
    op, cache_path, key = argv[1], argv[2], argv[3]
    cache = _load(cache_path)
    if op == "station":
        print(render_station(cache, key))
    elif op == "card":
        print(render_card_by_id(cache, key))
    else:
        print(f"unknown op {op!r}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest engine/tools/tests/test_brief_render.py -v`
Expected: PASS (12 tests). Also smoke the CLI:
Run: `python engine/tools/brief_render.py station engine/tools/tests/fixtures/brief-cache.sample.json system`
Expected: prints the System card with the `🔵/🟠` block.

- [ ] **Step 6: Register in the suite self-runner (if suite_test.py enumerates files explicitly)**

Check: `python -m pytest engine/tools/tests/ -q` — confirm `test_brief_render.py` is collected (suite goes 9 → 10 files). If `suite_test.py` hard-lists test modules, add `test_brief_render` to that list.

- [ ] **Step 7: Commit**

```bash
git add engine/tools/brief_render.py engine/tools/tests/test_brief_render.py engine/tools/tests/fixtures/brief-cache.sample.json
git commit -m "brief_render: CLI + station/card cache walking, golden fixture, suite 9->10"
```

---

### Task 4: Harden the `validate_cache` gate test

**Files:**
- Test: `engine/tools/tests/test_brief_session.py` (append)
- Modify (only if the negative case does not already fail): `engine/tools/brief_session.py:327-401`

**Interfaces:**
- Consumes: `brief_session.validate_cache(cache_obj) -> (ok: bool, errors: list[str])`

- [ ] **Step 1: Write the failing/confirming test** (append to `test_brief_session.py`)

```python
def test_validate_cache_rejects_missing_claude_voice_text():
    import brief_session as B
    cache = {
        "station_counts": {"system": 1, "personal": 0, "familyoffice": 0, "dev": 0},
        "stations": {"system": {"items": [
            {"item_id": "x", "title": "T", "domain": "System",
             "system_voice": {"grade": "1", "text": "a", "cite": "c"},
             "claude_voice": {}}  # missing .text
        ]}},
    }
    ok, errs = B.validate_cache(cache)
    assert ok is False
    assert any("claude_voice.text" in e for e in errs)
```

Note: match the exact cache shape `validate_cache` expects (see `brief_session.py:327-401` for the required top-level keys — `station_counts` + the four domain keys). Adjust the skeleton above to satisfy structural checks so the test isolates the `claude_voice.text` rule.

- [ ] **Step 2: Run test**

Run: `python -m pytest engine/tools/tests/test_brief_session.py -k missing_claude_voice -v`
Expected: PASS if the rule already exists (line 383 emits `missing claude_voice.text`). If it FAILS, add the missing check in `validate_cache` next to the existing `claude_voice` block, then re-run to green.

- [ ] **Step 3: Commit**

```bash
git add engine/tools/tests/test_brief_session.py engine/tools/brief_session.py
git commit -m "brief_session: lock validate_cache rejection of missing claude_voice.text"
```

---

### Task 5: Rewire the brief skill + precompute to the renderer

**Files:**
- Modify: `skills/brief/SKILL.md` (Stage-2 render step ~238–247; "Render — per item" fence ~355–364)
- Modify: `deploy/tasks/brief-cache.md` (precompute contract)

**Interfaces:**
- Consumes: `brief_render.py station <cache> <station>` / `card <cache> <item_id>` (Task 3)

- [ ] **Step 1: Rewire the Stage-2 render step in `skills/brief/SKILL.md`**

Replace the do-it-yourself render instruction with a verbatim-lift instruction. New text for the Stage-2 per-domain render step:

```markdown
## Stage 2 render — per domain station
1. Run `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/brief_render.py" station <env_root>/state/brief-cache.json <station>`
   and **emit its output verbatim** — do NOT re-compose, re-order, or re-word the card. The card
   format (title, urgency/playbook/flags, and the mandatory `🔵 Your system / 🟠 Claude` two-layer
   block) is produced by the engine renderer, never reproduced here.
2. **Fold in any held decision draft** whose `folds_into` == this card id (unchanged).
3. Attach **per-item buttons** whose two framed options map 1:1 to the two rendered layers:
   **A ← "Your system says"**, **B ← "Claude adds"**, plus **Other** (unchanged mechanics).
4. On pick: run the action, write the ledger, render confirmation, advance (unchanged).
```

Then mark the old "Render — per item" fence and the "Render format (chat)" graded-voice table as **reference only**, adding this line above each:

```markdown
> **Reference only — mirrored from `engine/tools/brief_render.py`.** Do NOT render from this by hand;
> call the renderer and lift its output. This block documents what the renderer emits.
```

- [ ] **Step 2: Rewire the precompute contract in `deploy/tasks/brief-cache.md`**

Add to the precompute's write section (after it describes writing `brief-cache.json`):

```markdown
**Do NOT hand-author the per-item card markdown.** Your job is to populate the STRUCTURED
`brief-cache.json` completely — every station item needs `title`, `domain`, and either a valid
`system_voice` ({grade, text, cite}; cite required for grades 1/2a) or `system_voice: null` for
Grade 0, plus `claude_voice.text`. `validate_cache` is your exit gate. The chat card is rendered
deterministically from this JSON by `brief_render.py` — never by prose.

**Generate `brief-cache.md` from the JSON** (single source of truth): after writing + validating
`brief-cache.json`, produce the markdown via
`python "${CLAUDE_PLUGIN_ROOT}/engine/tools/brief_render.py" station <cache> <station>` for each
station and concatenate under the existing masthead. Do not compose `brief-cache.md` by hand.
```

- [ ] **Step 3: Update the skill's VERIFY step to note the renderer is the sole card producer**

In `skills/brief/SKILL.md` VERIFY step (~285), append:

```markdown
The per-item card is emitted ONLY by `brief_render.py` (lifted verbatim); if you find yourself
hand-writing a `🔵`/`🟠` line, STOP — call the renderer. `validate_cache` guarantees the data; the
renderer guarantees the format.
```

- [ ] **Step 4: Verify end-to-end against the live cache**

Run: `python engine/tools/brief_render.py station /c/Users/sethh/Documents/Claude/state/brief-cache.json system`
Expected: every item in the System station prints with the `🔵/🟠` block (Grade-0 items show `— your system is silent —` + the `🟠 Claude` line). No item is missing the two-layer block.

- [ ] **Step 5: Commit**

```bash
git add skills/brief/SKILL.md deploy/tasks/brief-cache.md
git commit -m "brief: render cards via brief_render (verbatim lift); precompute writes data, engine renders"
```

---

### Task 6: Doctrine — record the general rule

**Files:**
- Modify: `../../Memory/decisions.md` (env repo — `C:\Users\sethh\Documents\Claude\Memory\decisions.md`)
- Modify: `../../CLAUDE.md` (env repo — Development principles or Conventions)

> **Cross-repo note:** Tasks 1–5 land in `Projects/aios` (own remote). Task 6 edits the **env repo**
> (`claude-env`). Commit it separately, in the env repo, not the aios repo.

- [ ] **Step 1: Append the decision entry to `Memory/decisions.md`**

```markdown
**2026-07-02 (brief render) — Must-hold formats are engine-rendered + lifted verbatim, never reproduced from prose.** The brief silently dropped its `🔵 Your system / 🟠 Claude` two-layer block across desktop/laptop/Cowork. Root cause: the card format was prose the model reproduced, through TWO LLM stages (precompute writes the cache, the trigger brief re-renders it). The only never-broken format (the widget review panel) is a template file lifted verbatim. Fix (aios A10): built `engine/tools/brief_render.py` — a pure `json → card markdown` function; the skill echoes it verbatim and only attaches host buttons; the precompute populates structured JSON (gated by `validate_cache`) and no longer hand-authors cards. **General rule (rolled out opportunistically via env-maintenance):** a must-hold rendered format is emitted by a deterministic engine renderer and lifted verbatim; skills never reproduce a must-hold format from prose — model judgment populates structured data, the engine renders it. Spec/plan: `Projects/aios/docs/superpowers/{specs,plans}/2026-07-02-brief-deterministic-card-render*`.
```

- [ ] **Step 2: Add the one-line rule to `CLAUDE.md`** (Development principles list)

```markdown
- **Deterministic render for must-hold formats.** A format that must always appear is emitted by a deterministic engine renderer and lifted verbatim by the skill — never reproduced from skill prose. Model judgment populates structured data; the engine renders it. (`Memory/decisions.md` 2026-07-02 brief render.)
```

- [ ] **Step 3: Commit in the env repo**

```bash
# from C:\Users\sethh\Documents\Claude (env repo)
git add Memory/decisions.md CLAUDE.md
git commit -m "doctrine: must-hold formats are engine-rendered + lifted verbatim (brief render)"
```

---

## Review & Ship (dev-tier gate)

Per the env's dev-tier discipline, before declaring A10 done:

- [ ] `python -m pytest engine/tools/tests/ -q` green at 10 files (exit code shown).
- [ ] Fresh-context review (independent subagent, not the builder) of the full aios diff → **zero CRITICAL** to ship; run `differential-review` on the diff (block HIGH/CRITICAL). No dependency changes expected (stdlib only), so `supply-chain-risk-auditor` is N/A — confirm no new imports.
- [ ] Live acceptance (spec §10): a real brief run renders every item with the `🔵/🟠` block present (shown).
- [ ] `git push` the aios repo; commit + push the env repo (doctrine) separately.
- [ ] Tick **A10** in `Projects/aios/BACKLOG.md`.

## Self-Review (plan vs spec)

- **Spec coverage:** §4 architecture → Tasks 1–5; §5.1 renderer → Tasks 1–3; §5.2 skill rewire → Task 5; §5.3 precompute + generated `brief-cache.md` → Task 5 Steps 2; §6 seam (buttons) → Task 5 Step 1.3; §7 enforcement legs (a valid-cache exists → Task 4; b sole producer → Task 5; c golden test → Task 3); §8 doctrine → Task 6; §9 open items resolved (nesting → `_voice`, Tasks 1–2; brief-cache.md generated → Task 5; station keys → Task 3 `_station_items`); §10 acceptance → Review & Ship.
- **Placeholder scan:** none — every code step shows complete code; Task 4 notes the one conditional (add the check only if the negative test doesn't already fail).
- **Type consistency:** `render_system_line`/`render_claude_line`/`_voice`/`render_card`/`render_station`/`render_card_by_id`/`main` names are consistent across Tasks 1–3 and referenced identically in Task 5.
