# Domain-Sync Plan 4 — Personal slug coverage (schema-derived slugs + disk seed) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Plan 3 domain-sync pipe able to sync the Personal silo — by deriving each table's slug title-field from its own schema (instead of the FamilyOffice-only hardcoded dict) and seeding `_slugmap.json` from the existing on-disk mirror so no record re-slugs.

**Architecture:** Two additive changes to the already-shipped engine. (1) `stable_slugs()` gains an explicit `title_field` the caller passes from the table's schema; the FamilyOffice-specific `_GENERIC_RULES` name-field lookup becomes a *fallback* only (still correct for FO → golden stays byte-exact), while `sync_silo` always passes the real schema title field (correct for every silo). `prices`/`sessions` stay bespoke. (2) A new `seed_slugmap()` reads the existing `state/domains/<silo>/tables/**/*.md` frontmatter (`notion_id`) → filename-stem slug, writing `_slugmap.json` so A84 first-seen-wins preserves every existing record.

**Tech Stack:** Python 3, stdlib only (`pathlib`, `json`, `re`, `yaml` via the engine's existing `domain_mirror` helpers). No new deps.

**Spec:** `docs/superpowers/specs/2026-07-15-domain-sync-notion-to-local-design.md` (§"Hard part 1 — slug stability"). This plan is a defect-fix + generalization within that spec's slug-identity scope; the identity contract (first-seen-wins, keyed on `notion_id`) is unchanged — only the title-field *source* generalizes from a hardcoded FO dict to the per-table schema.

## Global Constraints

- **FO golden byte-exactness is sacred.** `engine/tools/tests/test_stable_slugs.py` (the 13-table golden reproduction, Step 5 gate) MUST stay green unchanged in intent — the FO `notion_id → slug` map must reproduce exactly. Verified precondition: every FO table's schema title-field already equals its hardcoded `_GENERIC_RULES` field (Item==Item, Decision==Decision, Name==Name, …), so schema-derived is identical for FO.
- **Additive, not a rewrite.** `prices` and `sessions` bespoke rules are untouched. `_GENERIC_RULES` is retained (as fallback + default-word source), not deleted.
- **Fact-free engine.** No silo names, no collection ids, no instance data in engine code. The title field comes from the schema (instance data) at call time; the seed reads the instance mirror at run time.
- **No live Notion or real-tree writes inside SDD tasks.** Tasks 1–2 are hermetic (tmp dirs / frozen fixtures / the existing golden). The live Personal proof (wiring all ids, seeding the real mirror, `--no-reap` import + `git diff`) is done by the controller AFTER the branch merges, never by a task subagent.
- **No real FO/Personal economic data in test fixtures.** Use synthetic rows only. Controller greps added test lines before trusting any report.
- Run tests with `PYTHONIOENCODING=utf-8`.

---

### Task 1: `seed_slugmap()` — bootstrap `_slugmap.json` from the existing on-disk mirror

**Files:**
- Modify: `engine/tools/domain_sync.py` (add `seed_slugmap`; add a `seed` CLI subcommand/flag)
- Test: `engine/tools/tests/test_seed_slugmap.py`

**Interfaces:**
- Consumes: `domain_mirror.find_env_root`, `domain_mirror.load_silo_config` (for `state_dir`), `domain_mirror.notion_id_from_url` is NOT needed here (id read straight from frontmatter). Frontmatter parse: reuse the engine's existing frontmatter reader if one exists in `domain_mirror` (grep for `frontmatter`/`_read_front`/`yaml.safe_load` there first and reuse it); otherwise a minimal `---`-delimited YAML block parse.
- Produces: `seed_slugmap(state_dir: Path, *, dry_run: bool=False) -> dict` returning the `{notion_id: slug}` map it wrote (or would write). Writes via the existing `stable_slugs.save_slugmap` (deterministic sorted key order). Slug of a record = its filename stem; `notion_id` = the record's frontmatter `notion_id`.

- [ ] **Step 1: Write the failing test**

```python
# test_seed_slugmap.py — hermetic: build a fake mirror in tmp, seed it, assert map.
import json, tempfile, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import domain_sync as dsync

def _write(p, notion_id, title="X"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\ntype: state-thing\nnotion_id: {notion_id}\nlast_synced: 2026-01-01\n---\nbody\n", encoding="utf-8")

def test_seed_reads_notion_id_to_stem():
    with tempfile.TemporaryDirectory() as d:
        sd = pathlib.Path(d)
        _write(sd/"tables"/"decisions"/"example-decision.md", "00000000000000000000000000000042")
        _write(sd/"tables"/"tasks"/"book-physical.md", "aaaa1111bbbb2222cccc3333dddd4444")
        got = dsync.seed_slugmap(sd)
        assert got == {
            "00000000000000000000000000000042": "example-decision",
            "aaaa1111bbbb2222cccc3333dddd4444": "book-physical",
        }, got
        # written file round-trips
        from stable_slugs import load_slugmap
        assert load_slugmap(sd/"_slugmap.json") == got

def test_seed_skips_record_without_notion_id():
    with tempfile.TemporaryDirectory() as d:
        sd = pathlib.Path(d)
        (sd/"tables"/"notes").mkdir(parents=True)
        (sd/"tables"/"notes"/"no-id.md").write_text("---\ntype: x\n---\nbody\n", encoding="utf-8")
        got = dsync.seed_slugmap(sd)
        assert got == {}, got  # a record with no notion_id is skipped (counted), never guessed

def test_seed_dry_run_writes_nothing():
    with tempfile.TemporaryDirectory() as d:
        sd = pathlib.Path(d)
        _write(sd/"tables"/"notes"/"n.md", "id1")
        dsync.seed_slugmap(sd, dry_run=True)
        assert not (sd/"_slugmap.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python engine/tools/tests/test_seed_slugmap.py`
Expected: FAIL (`seed_slugmap` not defined).

- [ ] **Step 3: Implement `seed_slugmap`**

```python
def seed_slugmap(state_dir, *, dry_run=False) -> dict:
    """Bootstrap {notion_id: slug} from the EXISTING on-disk mirror so a first
    sync re-uses every record's current slug (A84 first-seen-wins) instead of
    re-slugging from scratch. slug = filename stem; notion_id = frontmatter
    `notion_id`. A record with no notion_id is skipped (can't key it) — never
    guessed. Idempotent; deterministic key order (save_slugmap)."""
    from stable_slugs import save_slugmap
    state_dir = Path(state_dir)
    tables_root = state_dir / "tables"
    out = {}
    skipped = 0
    for md in sorted(tables_root.rglob("*.md")):
        nid = _frontmatter_notion_id(md)   # reuse domain_mirror's reader if present
        if not nid:
            skipped += 1
            continue
        out[nid] = md.stem
    if not dry_run:
        save_slugmap(state_dir / "_slugmap.json", out)
    return out
```
Implementer: FIRST grep `domain_mirror.py` for an existing frontmatter/YAML-header reader and reuse it as `_frontmatter_notion_id`; only write a minimal `---`…`---` parser if none exists. Personal is all flat `*.md` (no `.ndjson`), so `.md` coverage is sufficient; if a nested `.ndjson` exists its rows are skipped this task (note it in the report — out of Personal scope).

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONIOENCODING=utf-8 python engine/tools/tests/test_seed_slugmap.py`
Expected: PASS (all three).

- [ ] **Step 5: Add a `seed` CLI path + commit**

Add to the existing `argparse` CLI a `--seed` action (or `seed` subcommand) that resolves env_root, takes `--silo`, and calls `seed_slugmap(load_silo_config(...)["state_dir"])`, printing the count written. Keep it consistent with the existing CLI shape. Then:
```bash
git add engine/tools/domain_sync.py engine/tools/tests/test_seed_slugmap.py
git commit -m "feat(domain-sync): seed_slugmap() bootstraps _slugmap.json from existing mirror"
```

---

### Task 2: schema-derived slug title-field (generalize `stable_slugs` past the FO dict)

**Files:**
- Modify: `engine/tools/stable_slugs.py` (thread `title_field`; dict becomes fallback + default source)
- Modify: `engine/tools/domain_sync.py` (`sync_silo` passes the schema title field per table)
- Test: `engine/tools/tests/test_stable_slugs.py` (extend — Personal-shaped cases; golden stays green)

**Interfaces:**
- Consumes: the table's schema title notion-field name. In `sync_silo`, derive it per table from `cfg`: the `notion_fields` entry whose spec is `[<NotionProp>, "title"]` → `<NotionProp>`. `load_silo_config`/`cfg["tables"]` currently exposes `fields`/`notion_fields`; if the title prop isn't already on the table dict, compute it in `sync_silo` from the loaded schema (grep `load_silo_config` for what it threads; add a `title_field` to each `tables.append({...})` if cleaner — a 1-line addition mirroring the `notion_db` threading already there).
- Produces: `stable_slugs(table_name, rows, slugmap, *, title_field=None) -> dict` (unchanged return shape `{url: slug}`). Backward-compatible: `title_field=None` falls back to the existing `_GENERIC_RULES` name-field (keeps the golden test green); when provided, `title_field` wins.

- [ ] **Step 1: Write the failing test** (Personal-shaped: a table NOT in the FO dict, and a dict table with a DIFFERENT title field)

```python
# add to test_stable_slugs.py
# (a) a Personal-only table with no _GENERIC_RULES entry now slugs via title_field
check("schema_derived_new_table",
      ss.stable_slugs("conditions",
                      [{"url": "https://n/c1", "Condition": "Hypertension Stage 1"}], {},
                      title_field="Condition") == {"https://n/c1": "hypertension-stage-1"})
# (b) a table whose name collides with the FO dict but whose Personal title field differs:
#     title_field MUST win over the (wrong-for-Personal) dict field "Item"
check("schema_derived_overrides_dict_field",
      ss.stable_slugs("tasks",
                      [{"url": "https://n/t1", "Task": "Book annual physical"}], {},
                      title_field="Task") == {"https://n/t1": "book-annual-physical"})
# (c) empty-title still falls back to a deterministic default (no crash)
_r = ss.stable_slugs("conditions", [{"url": "https://n/c2", "Condition": None}], {}, title_field="Condition")
check("schema_derived_empty_default", list(_r.values())[0] != "")
# (d) BACKWARD COMPAT: no title_field → FO dict field still used (golden path)
check("dict_fallback_when_no_title_field",
      ss.stable_slugs("notes", [{"url": "https://n/n1", "Note": "A note"}], {}) == {"https://n/n1": "a-note"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python engine/tools/tests/test_stable_slugs.py`
Expected: FAIL (`title_field` is an unexpected keyword arg; `conditions` raises ValueError).

- [ ] **Step 3: Implement schema-derived dispatch**

In `stable_slugs.py`, thread `title_field` through `stable_slugs` → `_new_slug_for`:
```python
def _new_slug_for(table_key, row, notion_id, seen, title_field=None):
    if table_key == "prices":
        return _prices_slug(row)
    if table_key == "sessions":
        return _sessions_slug(row, notion_id, seen)
    # generic: title field from the caller's schema wins; the FO dict is the
    # fallback (correct for FO → golden byte-exact) and the default-word source.
    dict_field, dict_default = _GENERIC_RULES.get(table_key, (None, None))
    name_field = title_field or dict_field
    default = dict_default or (table_key.rstrip("s") or table_key)  # deterministic fallback word
    if name_field is None:
        raise ValueError(f"stable_slugs: no title_field and no rule for table {table_key!r}")
    return _unique_slug(row.get(name_field), notion_id, default, seen)
```
And in `stable_slugs(...)` add `*, title_field=None` and pass it into `_new_slug_for(..., title_field=title_field)`.

- [ ] **Step 4: `sync_silo` passes the schema title field**

In `domain_sync.py` `sync_silo`, compute each table's title field and pass it:
```python
u2s = stable_slugs(db, rows, slugmap, title_field=_title_field_of(table))
```
where `_title_field_of(table)` returns the `notion_fields` prop whose spec is `[..., "title"]` (or the value threaded onto the table dict by `load_silo_config`). If simpler, thread `title_field` in `domain_mirror.load_silo_config`'s `tables.append({...})` exactly like `notion_db` — then `sync_silo` reads `table["title_field"]`. Pick whichever is a smaller, cleaner diff; document the choice in the report.

- [ ] **Step 5: Run the FULL slug suite + the golden gate**

Run: `PYTHONIOENCODING=utf-8 python engine/tools/tests/test_stable_slugs.py`
Expected: PASS — new schema-derived checks pass AND `golden_reproduce_all_13_tables` + every `golden_reproduce_*` stays green (FO byte-exactness preserved).

- [ ] **Step 6: Regression — domain suites**

Run: `PYTHONIOENCODING=utf-8 python engine/tools/tests/test_domain_sync.py` and `... test_domain_mirror.py`
Expected: PASS (FO golden equivalence 40/40 unchanged).

- [ ] **Step 7: Commit**

```bash
git add engine/tools/stable_slugs.py engine/tools/domain_sync.py engine/tools/tests/test_stable_slugs.py
git commit -m "feat(domain-sync): schema-derived slug title-field; FO dict now fallback (golden byte-exact preserved)"
```

---

## Self-Review

- **Spec coverage:** §Hard part 1 slug-stability — identity contract unchanged (first-seen-wins on notion_id); only the title-field source generalizes. ✓ Covered by Task 2. Seed (safe migration of the existing mirror) covered by Task 1.
- **Placeholder scan:** none — every step has concrete code.
- **Type consistency:** `stable_slugs` return shape `{url: slug}` unchanged; new `title_field` is keyword-only and optional (backward compatible). `seed_slugmap` returns `{notion_id: slug}` and writes via the shared `save_slugmap`. `_new_slug_for` signature extended with an optional `title_field` — updated at its only call site inside `stable_slugs`.
- **Post-merge controller steps (NOT SDD tasks):** wire the remaining ~19 Personal `notion_db` ids into `state/domains/personal/schema.yaml` (env repo); run `domain_sync.py --seed --silo personal` against the real mirror; run `--no-reap` import; `git diff` to confirm only `last_synced` bumps on existing records + genuinely-new files (no mass re-slug, no duplicates). Then FO stays gated as before.
