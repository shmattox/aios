# A45 — `domain_mirror.py` engine module (design)

- **Date:** 2026-07-09
- **Item:** AIOS backlog A45 — Domain-mirror engine module (D5 tooling-consolidation half of the state-consolidation program)
- **Program spec:** `docs/superpowers/specs/2026-07-08-state-consolidation-design.md` (D5/D6) — this is the module-level design that spec deferred.
- **First consumer:** env-ops H25/#2 (Personal domain records).
- **Status:** approved 2026-07-09 (import-only scope; publish + render-views deferred).

## Problem

State-consolidation sub-project #1 relocated the FamilyOffice + GM operational records into
env-root `state/domains/<silo>/` and folded the two per-silo *validators* into one shared fact-free
`engine/tools/state_validate.py`. That closed the **validator**. The **builder** half is still
per-silo and instance-bound:

- `Projects/family-office/state-mirror/migration/` — **~20 one-shot migrators**
  (`migrate_entities.py`, `migrate_assets.py`, …), each with its own `build_frontmatter()` that
  **hardcodes the Notion-property → record-field mapping in Python**:
  ```python
  fm["entity_legal_form"] = row.get("Type")
  fm["tax_classification"] = row.get("Tax Entity")
  fm["status"]            = row.get("Status")
  ```
  That per-table hand-mapping is the real duplication — 20 near-identical scripts + a `fo_paths.py`
  copied into three sibling dirs.
- `Projects/general-management/skills/notion-mirror/notion_mirror.py` — the GM tool. It goes the
  **opposite direction** (local records → Notion payload, one-way publish, **never reads Notion**),
  is already generic, already fact-free, and a single file.

The two tools are not duplicates of each other — they run opposite directions and share no logic.
The duplication worth killing is the 20 FO importers.

## Scope decision (import-only)

`domain_mirror.py` owns **one** direction: **Notion snapshot → `state/domains/<silo>/` records**
(import / idempotent refresh). The CLI is verb-first so future directions slot in without
restructuring:

```
domain_mirror.py import --silo <silo>
```

**Explicitly deferred (not this item):**

- **`publish` (records → Notion).** GM `notion-mirror` already does this, generically and
  fact-free. Folding it in now is co-location, not de-duplication (YAGNI). It becomes a future
  `domain_mirror.py publish` verb when it's genuinely re-touched — under sub-project #4 (dashboards)
  or when a 2nd local-SSOT silo needs it. Until then GM `notion-mirror` is left in place with a
  pointer note.
- **Render-views (the DataviewJS relational-view append, FO `build_pages.py`).** Vault-only
  presentation, not the sync/build engine — folds into #4 (dashboards), per A45's own
  "this item is the sync/build engine, not the viewer" hedge. `import` writes the **clean
  Notion-mirror record** (frontmatter + Notion body); no generated `## Holdings` DataviewJS block.

**A45 acceptance is trimmed accordingly:** "retire the per-silo copies" → **retire the FO
state-mirror migrators now**; the GM `notion-mirror` publish leg is deferred, not retired in this
item.

## Architecture

### Pure transform, headless-safe

`import` is a pure function:

```
import_silo(snapshot_dir, schema, out_dir) -> written records
```

- **Input is a Notion export snapshot on disk** (the per-DB JSON exports FO already keeps, e.g.
  `entities-notion-export-*.json`) — **never a live Notion read.** Engine tools run headless
  (Task Scheduler `claude -p`) with **no MCP grant**, so a live read is not available to the tool
  and would break determinism. Snapshot *capture* is a separate step outside the fact-free tool
  (an MCP/session run, or a manual Notion export) — the same boundary the FO migrators already use.
- **stdlib-only** (json + the shared YAML-subset helpers already in the engine), deterministic key
  order, byte-idempotent: re-running against the same snapshot reproduces byte-identical records
  (the FO migrators' existing property).

### Declarative config (zero hardcoded ids / paths / maps)

The per-table mapping moves out of Python and into the per-silo `schema.yaml`, which already
declares `required` / `enums` / `bools` / `relations` / `dates` per table. It gains one block:

```yaml
state-entity:
  required: [name, type, notion_id]
  # ... existing enums/bools/relations/dates ...
  notion_map:            # NEW — record field <- Notion property, by kind
    source_db: entities          # which snapshot export this table is built from
    fields:
      name:                { property: Name,      kind: title }
      entity_legal_form:   { property: Type,      kind: select }
      tax_classification:  { property: "Tax Entity", kind: select }
      status:              { property: Status,    kind: select }
      parent_entity:       { property: Parent,    kind: relation, link: "entities/{slug}" }
      articles_filed:      { property: "Articles Filed", kind: checkbox }
      formation_date:      { property: "Formation Date", kind: date }
```

- **`kind`** is a small closed set of Notion property shapes the importer knows how to coerce:
  `title`, `text`, `select`, `multi_select`, `checkbox`, `number`, `date`, `relation`, `url`,
  `notion_id` (derived from the row URL). Each `kind` is a tiny deterministic coercion —
  the generic replacement for the 20 hand-written `build_frontmatter`s.
- **Notion DB ids** come from `profile/domains.yaml` (already present per silo). `--silo personal`
  resolves the teamspace + DB ids from the profile and the table/field maps from that silo's
  `schema.yaml`. **Nothing instance-specific lives in the `.py`.**
- The fact-free grep (`grep -Ei '[0-9a-f]{32}|state/domains/(familyoffice|gm|personal)|notion.*database.*id'`
  over `domain_mirror.py`) is empty **by construction**.

### Data flow

```
profile/domains.yaml (silo -> teamspace + DB ids)
state/domains/<silo>/schema.yaml (tables + notion_map)
snapshot export JSONs (per DB, on disk)
        |
        v
domain_mirror.py import --silo <silo>
        |  per table: for each snapshot row -> coerce each field by kind -> frontmatter
        |  deterministic key order; write <out_dir>/tables/<table>/<slug>.md
        v
state/domains/<silo>/tables/**/*.md   (clean Notion-mirror records)
        |
        v
state_validate.py --schema <silo>/schema.yaml --all <silo>/tables   (PASS)
```

### Error handling (fail-loud on data, deterministic on transform)

- Unknown `kind`, a `notion_map` field whose property is absent from the snapshot row, or a
  checkbox/select value outside the schema enum → **fail loud** (raise, name the silo/table/row).
  These are content/contract errors, not reconstructable-from-source torn writes.
- Missing snapshot export for a mapped `source_db` → fail loud with the expected path.
- A snapshot with extra properties not in `notion_map` → ignored (the map is the allow-list),
  logged at the tail so drift is visible.
- Economic/Paper-Governs faithfulness: values are copied **verbatim** from the snapshot (which is
  verbatim from Notion); the importer never invents or computes a value (the FO PAPER-GOVERNS rule
  carries over). FamilyOffice import stays byte-faithful to the export.

## Testing & the ≥2-silo proof

One code path, proven on two silos:

1. **FamilyOffice regression (byte-identical).** Add a `notion_map` to the existing
   `state/domains/familyoffice/schema.yaml`, run `import --silo familyoffice` against the existing
   snapshot exports, and assert the output is **byte-identical to the currently shipped
   `familyoffice` records** (minus the deferred DataviewJS view block, which the existing records
   carry — see Open question O1). This proves the generic importer reproduces what the 20
   hand-written migrators produced.
2. **Personal net-new (= H25).** Build `state/domains/personal/` from a LifeOS snapshot:
   `decisions` + `meetings` tables (plus the LifeOS op-state tables), `state_validate.py` PASS with
   sample rows shown. This *is* the H25 deliverable — A45 and H25 share the run.
3. **Fact-free grep** over `domain_mirror.py` returns empty (shown).
4. **Fixture unit tests** in `engine/tools/tests/` (the standalone-script + `suite_test.py` pattern):
   a tiny fixture snapshot + fixture schema → asserted records, one per `kind` coercion, plus the
   fail-loud cases.

## What retires / what's left

| Path | Action |
|---|---|
| `Projects/family-office/state-mirror/migration/migrate_*.py` (~20) + `fo_paths.py` copies | **Retire → pointer README** (replaced by `import` + `notion_map`). Snapshot export JSONs are kept (they're the input). |
| `Projects/family-office/state-mirror/views/` (`build_pages.py` DataviewJS) | **Left** — folds into #4 (dashboards), not this item. |
| `Projects/general-management/skills/notion-mirror/` | **Left + pointer note** — the deferred `publish` leg. |

## Open questions (resolve in the plan)

- **O1 — the FamilyOffice byte-identical target.** The currently-shipped FO records carry the
  generated `## Holdings` DataviewJS append (from `build_pages.py`). Since `import` writes the clean
  record without it, the byte-identical assertion is against the **Notion-mirror body only** (strip
  the `<!-- ▽ generated … ▽ -->` block before comparing — `parity_check.py` already does this split).
  Confirm the parity split is reused so the regression test compares like-for-like.
- **O2 — snapshot freshness for Personal.** H25 needs a current LifeOS export. Decide the capture
  step (MCP/session run vs. manual export) — out of the fact-free tool, but the plan should name who
  produces `personal/*-notion-export-*.json`.
- **O3 — `notion_id` derivation.** FO derives it from the row URL's last path segment. Confirm the
  LifeOS export exposes the same URL shape (it should — same Notion), else add a `kind: notion_id`
  variant.

## Acceptance (revised A45)

1. This design doc committed (done on write).
2. `engine/tools/domain_mirror.py` exists; `python -m pytest tools/tests/ -q` green.
3. Fact-free grep over `domain_mirror.py` empty (shown).
4. `import` builds records for **≥2 silos** from profile + schema config: FamilyOffice re-derive
   **byte-identical** (mirror-body) + Personal net-new, each `state_validate.py` PASS (shown).
5. FO state-mirror migrators retired to a pointer README (never hard-deleted); GM `notion-mirror`
   left with a deferred-publish pointer note.
6. Backlog A45 acceptance updated to the import-only scope; H25 cross-linked to consume `import`.
