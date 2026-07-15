---
type: plan
topic: a80-state-native
spec: docs/superpowers/specs/2026-07-15-a80-state-native-design.md
created: 2026-07-15
status: ready-to-execute
backlog: aios A80 · env-ops H66 (the schema declaration)
blocks: domain-sync Plan 2b + Plan 3
---

# A80 — `state_native:` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach the importer to carry forward fields that no rule can reproduce, so a sync stops silently deleting locally-authored data.

**Architecture:** One optional schema key, `state_native: [<field>…]`, read by `load_silo_config`. `import_silo` reads the declared keys off the destination record before rebuilding it and hands them to `build_record`, which emits them in the **computed slot** — exactly where they already sit on disk, so a re-import is byte-identical. No new module; `build_record` stays a pure record-builder that never touches the filesystem.

**Tech Stack:** Python 3.14, stdlib only. Tests are standalone `check(name, cond)` scripts run as subprocesses by `suite_test.py`.

## Global Constraints

- **stdlib only** in `Projects/aios/engine/` — no PyYAML, and add no new imports beyond what the files already carry.
- **`Projects/aios` is PUBLIC** (`isPrivate:false`); `Projects/family-office` is **PRIVATE**. No real FamilyOffice entity names, open-item ids, or EINs may enter aios.
- **Fact-free engine:** the `state_native` field list lives in the silo `schema.yaml`, never in engine code. The engine knows only the rule.
- **Test convention (critical):** `test_*.py` here are standalone scripts using a `check(name, cond)` harness ending `sys.exit(1 if FAIL else 0)`. `conftest.py` tells pytest to ignore them; `suite_test.py` runs each as a subprocess. Never write `def test_*` pytest functions in them.
- **A table declaring no `state_native:` must be byte-identical to today.**
- Native git only. Stage → inspect `git diff --cached --stat` **as its own call** → commit.

## Verified Ground Truth (checked live 2026-07-15 — do not re-derive)

| Claim | Verified |
|---|---|
| `build_record` rebuilds `fm` from scratch | `domain_mirror.py:163` — `fm = {"type": table["name"]}` |
| The reader is already imported by this module | `domain_mirror.py:94` — `from state_validate import _parse_yaml, _extract_frontmatter` |
| `wiki` sits in the computed slot | index **21 of 24** on `entities` — after every notion_field, before `notion_id`/`notion_url`/`last_synced` |
| Absent must mean omitted, not null | `people` carries `wiki` on **7 of 21** records — 14 have no such key at all |
| Scope is 18 values | `entities` 11 + `people` 7; **Personal has zero** undeclared fields |
| `dest` is computed AFTER `build_record` | `domain_mirror.py:220-221` — and `slug` comes *from* `build_record`, so `import_silo` must derive it itself |
| `import_silo` already returns the written paths | `domain_mirror.py:223` `written.append(dest)` → `return written` |
| The test discards that return and rglobs instead | `test_domain_mirror.py:423` discards; `:428` and `:469` both `for _gen in _out.rglob("*.md")` |
| `_CURATED` today | `test_domain_mirror.py:426` — `{"wiki", "owner_entity", "asset"}` |

## The hazard seeding creates, and why Task 2 fixes it first

Seeding the temp `out_dir` from the golden means the destination directory now contains **golden files**. Both comparison loops iterate `_out.rglob("*.md")` — so any golden record the import does **not** regenerate would linger and be compared **against itself**, passing trivially. A false pass, in the exact test we are strengthening.

It cannot bite today (every table's export row count equals its golden count — verified 10/10), which is precisely why it must be closed now rather than discovered later. **Fix: iterate `import_silo`'s returned `written` list instead of rglobbing the directory.** It is what the import actually wrote, it is already returned and thrown away, and it makes the leftover class impossible rather than merely absent.

## File Structure

| Repo | File | Responsibility |
|---|---|---|
| aios (public) | `engine/tools/domain_mirror.py` | Task 1: `state_native` config + collision guard + `_read_state_native` + `preserved` |
| aios (public) | `engine/tools/tests/test_domain_mirror.py` | Task 1: fixture edge cases · Task 2: real-data proof |
| env root | `state/domains/familyoffice/schema.yaml` | Task 2: declare `state_native: [wiki]` on two tables |
| aios + env | `BACKLOG.md`, `state/domains/README.md` | Task 3: close-out + the contract line |

---

### Task 1: The engine — declare, guard, carry forward

**Files:**
- Modify: `Projects/aios/engine/tools/domain_mirror.py` (4 sites)
- Modify: `Projects/aios/engine/tools/tests/test_domain_mirror.py` (fixture tests)

**Interfaces:**
- Consumes: `_extract_frontmatter`, already imported at `domain_mirror.py:94`.
- Produces:
  - `load_silo_config` → each table dict gains `"state_native": list[str]` (empty when undeclared).
  - `_read_state_native(dest: Path, keys: list[str]) -> dict` — declared keys present on an existing record; `{}` when `keys` is empty or `dest` does not exist.
  - `build_record(table, row, url_to_slug, slug_maps, last_synced=None, preserved=None)` — `preserved` is an optional dict emitted in the computed slot. **Task 2 relies on this exact signature.**

- [ ] **Step 1: Write the failing fixture tests**

The `demo` fixture silo is redefined mid-file (`test_domain_mirror.py`, the `state-thing` / `state-person` schema block). Add a NEW self-contained fixture block for A80 — do not disturb the existing `demo`/`acme` silos. Insert immediately **above** the `# ── A72/A53 REAL regression` marker:

```python
# ══════════════════════════════════════════════════════════════════════════════
# A80 — state_native: fields no rule can reproduce are carried forward, never rebuilt away.
# Synthetic fixture silo; the engine stays fact-free (the field list lives in the schema).
# ══════════════════════════════════════════════════════════════════════════════
def _a80_silo(state_native_line):
    root = Path(tempfile.mkdtemp())
    (root / "profile").mkdir()
    (root / "profile" / "domains.yaml").write_text("brief:\n  trigger: go\n", encoding="utf-8")
    sd = root / "state" / "domains" / "keep"
    (sd / "tables").mkdir(parents=True)
    (sd / "schema.yaml").write_text(textwrap.dedent(f"""\
        state-thing:
          required: [name, type, notion_id]
        {state_native_line}
          notion_source_db: things
          notion_fields:
            name: [Name, title]
            status: [Status, select]
        """), encoding="utf-8")
    return root, sd

# config: the key is read, and defaults to [] when undeclared
_r80, _sd80 = _a80_silo("  state_native: [curated]")
_t80 = dm.load_silo_config(_r80, "keep")["tables"][0]
check("a80_config_reads_key", _t80["state_native"] == ["curated"])
_r80b, _sd80b = _a80_silo("")                        # no state_native declared at all
check("a80_config_defaults_empty",
      dm.load_silo_config(_r80b, "keep")["tables"][0]["state_native"] == [])

# COLLISION: a field cannot be both unreproducible and read off the snapshot -> fail loud at LOAD
_r80c, _sd80c = _a80_silo("  state_native: [status]")   # `status` is also a notion_field
try:
    dm.load_silo_config(_r80c, "keep")
    check("a80_collision_raises", False)
except ValueError as _e80:
    check("a80_collision_raises", "status" in str(_e80))

# carry-forward end-to-end on the fixture
_snap80 = _sd80 / "_snap"; _snap80.mkdir()
_write_export(_snap80 / "things-export.json",
              [{"Name": "Widget", "Status": "old", "url": "https://n/w1"}],
              {"https://n/w1": "widget"})
_out80 = _sd80 / "tables"
dm.import_silo(_r80, "keep", _snap80, _out80)
check("a80_absent_key_omitted", "curated:" not in
      (_out80 / "things" / "widget.md").read_text(encoding="utf-8"))   # nothing to carry -> OMITTED

# now hand-author the state-native field, re-import with a CHANGED snapshot value, and prove:
#   the curated field SURVIVES verbatim while its notion_fields sibling UPDATES.
_w80 = _out80 / "things" / "widget.md"
_w80.write_text(_w80.read_text(encoding="utf-8").replace(
    "status: old", 'status: old\ncurated: "[[hand/authored]]"'), encoding="utf-8")
_write_export(_snap80 / "things-export.json",
              [{"Name": "Widget", "Status": "new", "url": "https://n/w1"}],
              {"https://n/w1": "widget"})
dm.import_silo(_r80, "keep", _snap80, _out80)
_txt80 = _w80.read_text(encoding="utf-8")
check("a80_state_native_survives_reimport", 'curated: "[[hand/authored]]"' in _txt80)
check("a80_sibling_still_updates", "status: new" in _txt80)
# and it lands in the COMPUTED SLOT: after the notion_fields, before the derived trio
_keys80 = list(dm._extract_frontmatter(_txt80))
check("a80_emitted_in_computed_slot",
      _keys80.index("curated") > _keys80.index("status")
      and _keys80.index("curated") < _keys80.index("notion_id"))
```

- [ ] **Step 2: Run it to verify it fails for the right reason**

Run: `cd Projects/aios && python engine/tools/tests/test_domain_mirror.py 2>&1 | tail -3`
Expected: FAIL — `FAILURES:` includes `a80_config_reads_key` (the table dict has no `state_native`
key yet, so `_t80["state_native"]` raises `KeyError` before the check even evaluates).

If it raises `KeyError` and aborts rather than reporting a FAILURES list, that is expected at this
step — the harness runs checks at import. Proceed to Step 3.

- [ ] **Step 3: Add the config read + collision guard**

In `load_silo_config`, replace the `tables.append(...)` block (currently `domain_mirror.py:128-130`):

```python
        computed = list((tdef.get("computed_fields") or {}).items())
        # OPTIONAL `state_native:` (A80) — fields NO rule can reproduce (a hand-set link, not a
        # snapshot property and not derivable from one), so the importer must carry them forward
        # rather than rebuild them away. Fact-free: which fields qualify is the silo's call.
        state_native = list(tdef.get("state_native") or [])
        derived = {f[0] for f in fields} | {c[0] for c in computed}
        clash = sorted(set(state_native) & derived)
        if clash:
            raise ValueError(f"[{tname}] state_native fields are also derived from the snapshot: "
                             f"{clash} — a field cannot be both unreproducible and computed")
        tables.append({"name": tname, "source_db": tdef["notion_source_db"],
                       "fields": fields, "computed": computed, "state_native": state_native})
```

- [ ] **Step 4: Add the reader**

Insert directly above `def build_record(` (currently `domain_mirror.py:161`):

```python
def _read_state_native(dest, keys) -> dict:
    """The declared state-native keys present on an EXISTING record at `dest` (A80).

    Absent file or absent key -> that key is simply not carried, so build_record omits it. That is
    deliberate and evidence-led: a table's state-native field is routinely present on only SOME of
    its records, and emitting `null` for the rest would rewrite records that are already correct.
    A genuinely unreadable file raises out of read_text — failing loud is right, because carrying
    nothing from a corrupt record is exactly the silent deletion this exists to prevent."""
    if not keys or not dest.is_file():
        return {}
    fm = _extract_frontmatter(dest.read_text(encoding="utf-8"))
    return {k: fm[k] for k in keys if k in fm}
```

- [ ] **Step 5: Emit `preserved` in the computed slot**

Change `build_record`'s signature (`domain_mirror.py:161`) and add the emit loop after the computed
loop:

```python
def build_record(table, row, url_to_slug, slug_maps, last_synced=None, preserved=None) -> tuple[str, str]:
```

Then, immediately after the `for cfield, cspec in table["computed"]:` loop and **before**
`notion_id = notion_id_from_url(row["url"])`:

```python
    # state_native (A80): carried verbatim from the existing record — no rule can reproduce these,
    # so rebuilding without them DELETES them. Emitted HERE, in the computed slot, because that is
    # where they already sit on disk; any other position would churn every such record for nothing.
    # `preserved` holds only keys that were actually present, so an absent one stays absent.
    for _k, _v in (preserved or {}).items():
        fm[_k] = _v
```

`build_record` still takes no filesystem argument and does no I/O — `import_silo` does the reading.

- [ ] **Step 6: Wire it in `import_silo`**

Replace the row loop (currently `domain_mirror.py:219-223`):

```python
        tdir = out / table["source_db"]
        for row in data["rows"]:
            # The slug is derived here as well as in build_record because the DESTINATION path is
            # needed BEFORE the record is built: state_native carries forward from whatever is
            # already there. Same expression, same fail-loud on an unmapped row url.
            dest = tdir / f"{url_to_slug[row['url']]}.md"
            preserved = _read_state_native(dest, table["state_native"])
            slug, text = build_record(table, row, url_to_slug, slug_maps,
                                      last_synced=eff_last_synced, preserved=preserved)
            dest = tdir / f"{slug}.md"
            if not dry_run:
                tdir.mkdir(parents=True, exist_ok=True)
                dest.write_text(text, encoding="utf-8")
            written.append(dest)
```

- [ ] **Step 7: Run the tests**

Run: `cd Projects/aios && python engine/tools/tests/test_domain_mirror.py 2>&1 | tail -3`
Expected: `FAILURES: []`.

Run: `cd Projects/aios && python -m pytest -q 2>&1 | tail -1`
Expected: `49 passed`.

The FO regression must still print all ten tables N/N — a table declaring no `state_native:` gets
`state_native: []`, so `_read_state_native` returns `{}` on the first branch without touching disk,
and behavior is unchanged.

- [ ] **Step 8: Commit**

```bash
cd "C:/Users/sethh/Documents/Claude/Projects/aios"
git add engine/tools/domain_mirror.py engine/tools/tests/test_domain_mirror.py
git diff --cached --stat        # its own call — confirm ONLY those two files
git commit -m "A80: state_native — carry forward fields no rule can reproduce

build_record rebuilds frontmatter from scratch, so an undeclared field isn't
overwritten by Notion — it ceases to exist. For a field Notion never had, 'Notion
wins' means destroyed.

One optional schema key. Preserved values are emitted in the computed slot,
because that is where they already sit on disk, so a re-import is byte-identical.
Absent stays omitted, never null: a state-native field is routinely present on
only some of a table's records. A field declared both state_native and derived
fails loud at load — it cannot be both."
```

---

### Task 2: Declare it, and turn the exclusion that hid this bug into coverage

**Files:**
- Modify: `state/domains/familyoffice/schema.yaml` (env root — the `state-entity` and `state-person` blocks)
- Modify: `Projects/aios/engine/tools/tests/test_domain_mirror.py` (the FO regression block)

**Interfaces:**
- Consumes: Task 1's `state_native` config key and `preserved` parameter.
- Produces: `wiki` compared 18/18 against real data; `_CURATED` reduced to `{owner_entity, asset}`.

**Why the test must stop rglobbing.** Seeding puts golden files in `_out`. Both loops iterate
`_out.rglob("*.md")`, so a golden record the import does not regenerate would be compared against
itself and pass trivially. Iterating `import_silo`'s returned list makes that impossible. It cannot
happen today (all ten tables' export counts equal their golden counts) — close it anyway.

- [ ] **Step 1: Declare the key on both tables**

In `state/domains/familyoffice/schema.yaml`, add one line to the `state-entity:` block, beside its
existing `notion_source_db:`:

```yaml
  state_native: [wiki]
```

Add the identical line to the `state-person:` block.

The `state-entity` block's existing comment already explains *why* (`wiki` is "a curated cross-link
… whose slug is hand-set … NOT reconstructable from the … snapshot; it is state-native curation").
Leave that comment; the key now enforces what it describes.

- [ ] **Step 2: Seed the destination from the golden, and iterate what was written**

In `test_domain_mirror.py`, replace the import call (currently `:423`):

```python
    _out = Path(tempfile.mkdtemp()) / "tables"
    dm.import_silo(_ENV_ROOT, "familyoffice", _snap, _out, last_synced="2026-06-30")
```

with:

```python
    _out = Path(tempfile.mkdtemp()) / "tables"
    # A80: seed the DESTINATION from the golden so state_native carry-forward has a real record to
    # read — without this, `wiki` has nothing to carry and would have to stay excluded, which is the
    # very blind spot that hid this class. Still hermetic: the seed is the FROZEN golden, never the
    # live record tree. Copied per-table so golden/README.md is not swept in.
    for _t in _cfg["tables"]:
        _src = _GOLDEN / _t["source_db"]
        if not _src.is_dir():
            continue
        _dst = _out / _t["source_db"]
        _dst.mkdir(parents=True, exist_ok=True)
        for _p in _src.glob("*.md"):
            (_dst / _p.name).write_text(_p.read_text(encoding="utf-8"), encoding="utf-8")
    # Iterate what the import actually WROTE, not the directory: the seed above means a record the
    # import did not regenerate would otherwise be compared against itself and pass trivially.
    _written = dm.import_silo(_ENV_ROOT, "familyoffice", _snap, _out, last_synced="2026-06-30")
```

- [ ] **Step 3: Point both loops at `_written` and drop `wiki` from `_CURATED`**

Change the comparison loop (currently `:426-428`):

```python
    _REQUIRED = {"entities", "notes"}                # must reach equivalence (scope-narrowed)
    _CURATED = {"owner_entity", "asset"}             # still golden-only: A80 covers `wiki`, and
                                                     # these two are REPRODUCIBLE (a ruleset / a
                                                     # decode) so they are Plan 2b's, not A80's.
    _mism, _bad_extra, _counts = [], [], {}
    for _gen in _written:
```

And the body-contract loop (currently `:469`):

```python
    for _gen in _written:
```

- [ ] **Step 4: Run the proof**

Run: `cd Projects/aios && python engine/tools/tests/test_domain_mirror.py 2>&1 | tail -14`
Expected: all ten tables still N/N, `FAILURES: []`, and the body contract still
`552 substance-equal, 0 divergent, 11 skipped`.

**The `wiki` proof is now implicit and must be made explicit** — with `wiki` out of `_CURATED`, the
`_unexpected` check (`set(_s) - set(_g)) - _CURATED`) would flag `wiki` as "a golden field we forgot
to map" on all 18 records **if carry-forward were broken**. So `FAILURES: []` IS the 18/18 proof. Add
one count line directly after the per-table print loop so it is visible rather than inferred:

```python
    _wiki_n = sum(1 for _g in _written if "wiki" in _load_fm(_g.read_text(encoding="utf-8")))
    check("a80_wiki_carried_on_real_data", _wiki_n == 18)
    print(f"A80 state_native: wiki carried forward on {_wiki_n} real records (entities 11 + people 7)")
```

Expected: `A80 state_native: wiki carried forward on 18 real records (entities 11 + people 7)`.

- [ ] **Step 5: Prove the check can fail**

Temporarily comment out the `for _k, _v in (preserved or {}).items():` loop in `build_record`, re-run.
Expected: `a80_wiki_carried_on_real_data` FAILS (0, not 18) **and** `fo_semantic_equivalence_all`
fails with `wiki` listed as an unexpected golden-only key on 18 records. **Restore the loop.**
A check that cannot fail is worse than none.

- [ ] **Step 6: The byte-order proof**

Run:
```bash
cd "C:/Users/sethh/Documents/Claude"
python Projects/aios/engine/tools/domain_mirror.py import --silo personal | tail -1
git status --porcelain state/domains/personal/tables | head
```
Expected: `280 records for silo 'personal'` and the second command prints **nothing**. Personal
declares no `state_native:`, so this proves the no-declaration path is byte-identical — the
regression the Global Constraints require.

- [ ] **Step 7: Full suite + validators, then commit both repos**

Run: `cd Projects/aios && python -m pytest -q 2>&1 | tail -1` → `49 passed`.
Run (from env root): `for s in familyoffice personal gm; do python Projects/aios/engine/tools/state_validate.py --schema state/domains/$s/schema.yaml --all state/domains/$s/tables | tail -1; done`
Expected: `645/645 PASS`, `280/280 PASS`, `32/32 PASS`.

```bash
cd "C:/Users/sethh/Documents/Claude"
git add state/domains/familyoffice/schema.yaml
git diff --cached --stat        # its own call
git commit -m "Declare state_native: [wiki] on the two FO tables that carry it (A80)

The schema already explained in prose that wiki is hand-set curation and not
reconstructable from the snapshot. The key now enforces what the comment
described, so a sync stops erasing 18 values."

cd "C:/Users/sethh/Documents/Claude/Projects/aios"
git add engine/tools/tests/test_domain_mirror.py
git diff --cached --stat        # its own call
git commit -m "Prove state_native on real data: wiki 18/18, and _CURATED shrinks

Seed the temp destination from the frozen golden so carry-forward has a record to
read, and wiki moves from EXCLUDED to compared — the exclusion that hid this whole
class becomes coverage. Still hermetic: seeded from the golden, never the live tree.

Also stop rglobbing the output dir. Seeding means a record the import didn't
regenerate would be compared against itself and pass trivially; iterating the list
import_silo already returns makes that impossible. It couldn't bite today (every
table's export count equals its golden count) — closing it while it's cheap."
```

---

### Task 3: Close out — the contract line and the backlog

**Files:**
- Modify: `state/domains/README.md` (env root)
- Modify: `Projects/aios/BACKLOG.md`, `C:/Users/sethh/Documents/Claude/BACKLOG.md`

**Interfaces:**
- Consumes: Tasks 1-2 green.
- Produces: the written contract matching the enforced one; A80 closed.

- [ ] **Step 1: State the contract where the mirror is described**

`state/domains/README.md` currently carries the phased-migration language. Add the A80 sentence to
it (the full rewrite is H66's, with Plan 3 — do not do that here):

```markdown
**The mirror is derived output — EXCEPT the fields each table declares `state_native:`.** Those are
local-canonical: no rule can reproduce them (a hand-set wiki cross-link is not a Notion property and
is not derivable from one), so the importer carries them forward verbatim instead of rebuilding them
away. Everything else comes from Notion and is overwritten on every sync. A field cannot be both —
declaring one `state_native:` *and* mapping it fails loud at load.
```

- [ ] **Step 2: Close A80 in the aios backlog**

Move A80 to `## Done` as ONE line (the env convention — the full close-out lives in the closing
commit):

```markdown
- [x] **A80** — `state_native:`: the importer no longer deletes fields no rule can reproduce. One optional schema key; preserved values emitted in the computed slot so a re-import is byte-identical; absent stays omitted (never null); a field declared both state_native and derived fails loud at load. Proven on real data — `wiki` 18/18, and `_CURATED` shrank to `{owner_entity, asset}`, turning the exclusion that hid the class into coverage. Fixture proofs: absent → omitted · no destination → omitted · collision → ValueError before any write · survives a re-import while its siblings update. Unblocks domain-sync Plan 2b + Plan 3. (✅ 2026-07-15, <sha>)
```

- [ ] **Step 3: Update the env backlog**

On **H66**, note that the FO schema now declares `state_native: [wiki]` and that A80 has shipped, so
**Plan 2b and Plan 3 are unblocked**. Add to `## Watching`:

```markdown
- **A80 tail (delete when clean):** confirm the first real sync carries `wiki` forward on all 18 records rather than erasing them (`git diff` on `state/domains/familyoffice/tables/{entities,people}` should show NO `wiki` line removed). This is the first time the contract runs against live data rather than the golden.
```

- [ ] **Step 4: Fresh-context review, then commit**

Dispatch a subagent (never the builder) with the diff across both repos:

> Review this against `Projects/aios/docs/superpowers/specs/2026-07-15-a80-state-native-design.md`.
> Focus: (1) **Can a state_native field still be lost?** Trace every path — new record, absent key,
> unreadable destination, dry-run, a table declaring the key with no matching records. (2) **Is the
> engine still fact-free** — does any FO specific appear in `domain_mirror.py`? (3) **Is the test
> still hermetic** — does the seeding read the golden and never `state/domains`? `a72_regression_is_hermetic`
> must still be green. (4) **Does the new check actually guard** — construct a break and confirm it
> reds. (5) `Projects/aios` is PUBLIC: run `python engine/tools/sanitize_check.py` tree-wide and
> confirm the roster at `<env>/state/sanitize-patterns.txt` EXISTS before trusting a clean result.
> Report CRITICAL/MAJOR/MINOR with `file:line`. Cite any rule you invoke by its real location.

Fix any CRITICAL, re-review until clean, then commit the docs + backlogs (stage → inspect → commit,
each as its own call) and push all repos.

---

## Acceptance (shown in chat, not asserted)

- `wiki` carried on **18** real records — `A80 state_native: wiki carried forward on 18 real records` printed, and `_CURATED` no longer contains `wiki`.
- `FAILURES: []` with all ten tables N/N; body contract `552 substance-equal, 0 divergent, 11 skipped`.
- `a72_regression_is_hermetic` still green — the seed reads the golden, never the live tree.
- Fixture proofs green: absent → omitted · no destination → omitted · collision → `ValueError` · survives re-import while siblings update · emitted in the computed slot.
- The break-it check (Step 5) is shown failing, then restored.
- `python -m pytest -q` exits **0** — `49 passed`.
- A Personal import (no `state_native:` declared) produces **zero git diff** — the no-declaration path is byte-identical.
- `state_validate --all` PASSes all three silos.
- A fresh-context reviewer reports zero CRITICAL.

## Explicitly NOT in A80

- `owner_entity` (2-key `lookup`) and `asset` (JSON-array decode) — **Plan 2b**. Both are reproducible; neither is state-native by the spec's test.
- Bodies of any kind, including the generated view blocks — **A81**'s render stage.
- Any change to `state_validate` — its `relations:` list already covers the type axis.
- The full `state/domains/README.md` contract rewrite — H66, with Plan 3.
