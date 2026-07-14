# A45 — `domain_mirror.py` (import-only) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one generic, fact-free `engine/tools/domain_mirror.py import --silo <silo>` that transforms a Notion export snapshot into `state/domains/<silo>/` records via declarative per-table config, replacing the ~20 hand-written FamilyOffice migrators.

**Architecture:** A pure, stdlib-only transform. `import` resolves per-silo facts from the instance profile + that silo's `schema.yaml` (Notion DB ids from `profile/domains.yaml`; field maps from `schema.yaml`'s new `notion_fields:` flow-lists), loads on-disk snapshot JSON exports, coerces each field by a small closed set of `kind`s, and writes deterministic markdown records. No live Notion reads (headless-safe). Genericity is proven on a synthetic fixture silo (unit) + the real FamilyOffice silo (regression by semantic equivalence). The real Personal silo is delivered downstream by env-ops H25 using this same tool.

**Tech Stack:** Python 3 stdlib only (`json`, `re`, `pathlib`, `argparse`, `datetime`). Reuses the engine's shared YAML-subset reader from `state_validate.py`. Tests follow the engine's standalone-script + `suite_test.py` idiom.

## Global Constraints

- **Fact-free (Stage Contract):** ZERO hardcoded Notion ids, silo names, or `state/domains/<silo>` paths in `domain_mirror.py`. Everything resolves from `--silo` + profile + schema. The acceptance grep `grep -Ei '[0-9a-f]{32}|state/domains/(familyoffice|gm|personal)|notion.*database.*id' tools/domain_mirror.py` must return empty.
- **stdlib only** — no PyYAML, no `requests`, no network. Same rule as every engine tool.
- **Reuse, don't copy readers** — import the YAML-subset/frontmatter reader from `state_validate.py`; do NOT add a new drifted `_parse_yaml` copy (the engine already has an A49/A51 reader-consolidation debt; don't add to it).
- **Verbatim faithfulness (Paper-Governs):** every record value is copied verbatim from the snapshot; the importer never invents, computes, or reformats a value. Null in → null/omitted out.
- **Deterministic + idempotent:** re-running against the same snapshot produces byte-identical output (stable key order = schema field order).
- **Env root resolution:** walk up from the tool file to the first dir containing `profile/domains.yaml` (never a hardcoded absolute path), matching `meetings_router_task.py`.
- **Test command:** `cd Projects/aios/engine && python -m pytest tools/tests/ -q` (green iff every standalone script exits 0).

## File Structure

- **Create** `engine/tools/domain_mirror.py` — the importer: config resolution, snapshot load, `kind` coercions, deterministic emitter, CLI. One focused file (~300 lines).
- **Create** `engine/tools/tests/test_domain_mirror.py` — standalone test script (auto-collected by `suite_test.py`).
- **Create** `engine/tools/tests/fixtures/domain_mirror/` — synthetic `demo` silo: `profile/domains.yaml`, `schema.yaml` (2 tables), two snapshot export JSONs, and the expected records.
- **Modify** `state/domains/familyoffice/schema.yaml` — add `notion_source_db:` + `notion_fields:` flow-lists per table.
- **Modify** `Projects/aios/BACKLOG.md` — trim A45 acceptance to import-only; note the retire + the H25 cross-link.
- **Modify** `Projects/family-office/state-mirror/migration/` — replace the ~20 `migrate_*.py` + `fo_paths.py` with a pointer `README.md`.
- **Modify** `Projects/general-management/skills/notion-mirror/SKILL.md` — add a one-line "deferred publish leg of A45" pointer note.

## The `kind` set (closed; the generic replacement for 20 hand-mapped `build_frontmatter`s)

| kind | snapshot value | record value |
|---|---|---|
| `title` / `text` / `url` | string or null | `str` or `None` |
| `select` | string or null | `str` or `None` |
| `multi_select` | list or null | `list[str]` or `None` |
| `checkbox` | `"__YES__"`/`"__NO__"`/null | `True`/`False`/`None` (unknown → **fail loud**) |
| `number` | int/float or null | numeric or `None` |
| `date` | ISO string or null | `str` or `None` |
| `relation` | url or list-of-urls or null | `"[[<link>]]"` or `list`, via `url_to_slug` + the field's link template (3rd flow-list arg, e.g. `entities/{slug}`); single→scalar, many→list |

`notion_id` (row-url last path segment), `type` (the table's schema key), and the slug (from the export's `url_to_slug`) are always derived by the importer — not `notion_fields` entries.

---

### Task 1: Coercions + deterministic emitter (pure primitives)

**Files:**
- Create: `engine/tools/domain_mirror.py`
- Test: `engine/tools/tests/test_domain_mirror.py`

**Interfaces:**
- Produces:
  - `notion_id_from_url(url: str) -> str`
  - `coerce(kind: str, value, *, url_to_slug: dict, link_tmpl: str | None) -> object` (raises `ValueError` on unknown kind / bad checkbox)
  - `emit_frontmatter(fields: dict) -> str` (deterministic; preserves insertion order; wraps in `---\n…\n---\n`)

- [ ] **Step 1: Write the failing test** (append to `test_domain_mirror.py`)

```python
import os, sys
_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _TOOLS)
import domain_mirror as dm

FAIL = []
def check(name, cond):
    if not cond: FAIL.append(name)

# notion_id
check("notion_id", dm.notion_id_from_url("https://app.notion.com/358187194baf815e9cc2f41b30339750")
      == "358187194baf815e9cc2f41b30339750")

# coercions
u2s = {"https://n/aaa": "example-holdings"}
check("checkbox_yes", dm.coerce("checkbox", "__YES__", url_to_slug={}, link_tmpl=None) is True)
check("checkbox_no",  dm.coerce("checkbox", "__NO__",  url_to_slug={}, link_tmpl=None) is False)
check("checkbox_null",dm.coerce("checkbox", None,      url_to_slug={}, link_tmpl=None) is None)
check("select_null",  dm.coerce("select",   None,      url_to_slug={}, link_tmpl=None) is None)
check("number",       dm.coerce("number",   5000,      url_to_slug={}, link_tmpl=None) == 5000)
check("relation_one", dm.coerce("relation", "https://n/aaa", url_to_slug=u2s, link_tmpl="entities/{slug}")
      == "[[entities/example-holdings]]")
check("relation_many",dm.coerce("relation", ["https://n/aaa"], url_to_slug=u2s, link_tmpl="entities/{slug}")
      == ["[[entities/example-holdings]]"])
try:
    dm.coerce("checkbox", "maybe", url_to_slug={}, link_tmpl=None); check("checkbox_badraises", False)
except ValueError: check("checkbox_badraises", True)
try:
    dm.coerce("bogus", "x", url_to_slug={}, link_tmpl=None); check("unknown_kind_raises", False)
except ValueError: check("unknown_kind_raises", True)

# emitter: deterministic order, null, quoting of wikilinks + numeric-looking strings
fm = dm.emit_frontmatter({"type": "state-entity", "name": "Example Legacy Trust",
                          "status": None, "articles_filed": False,
                          "parent_entity": "[[entities/x]]", "notion_id": "abc"})
check("emit_order", fm.splitlines()[0] == "---" and fm.splitlines()[1] == "type: state-entity")
check("emit_null", "status: null" in fm)
check("emit_bool", "articles_filed: false" in fm)
check("emit_wikilink_quoted", 'parent_entity: "[[entities/x]]"' in fm)
check("emit_close", fm.rstrip().endswith("---"))
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd Projects/aios/engine && python tools/tests/test_domain_mirror.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'domain_mirror'`.

- [ ] **Step 3: Write minimal implementation** (`engine/tools/domain_mirror.py`)

```python
#!/usr/bin/env python3
"""domain_mirror.py — generic, fact-free Notion-snapshot -> state/domains/<silo>/ importer.

import verb: transform a per-silo Notion export snapshot (on disk) into markdown state records,
using declarative per-table config (Notion DB ids from profile/domains.yaml; field maps from the
silo's schema.yaml `notion_fields:`). NEVER reads live Notion (engine runs headless, no MCP grant).
stdlib only. Deterministic + idempotent. Values copied verbatim (Paper-Governs faithfulness).

  python domain_mirror.py import --silo <silo> [--snapshot-dir DIR] [--out DIR] [--dry-run]
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

_CHECKBOX = {"__YES__": True, "__NO__": False}


def notion_id_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def _link(url: str, url_to_slug: dict, link_tmpl: str) -> str:
    slug = url_to_slug[url]                       # KeyError => fail loud (dangling relation)
    return "[[" + link_tmpl.format(slug=slug) + "]]"


def coerce(kind, value, *, url_to_slug, link_tmpl):
    if kind in ("title", "text", "select", "url"):
        return None if value in (None, "") else str(value)
    if kind == "multi_select":
        return None if not value else [str(v) for v in value]
    if kind == "number":
        return None if value is None else value
    if kind == "date":
        return None if value in (None, "") else str(value)
    if kind == "checkbox":
        if value is None:
            return None
        if value not in _CHECKBOX:
            raise ValueError(f"unexpected checkbox value: {value!r}")
        return _CHECKBOX[value]
    if kind == "relation":
        if not value:
            return None
        if isinstance(value, list):
            return [_link(u, url_to_slug, link_tmpl) for u in value]
        return _link(value, url_to_slug, link_tmpl)
    raise ValueError(f"unknown kind: {kind!r}")


def _emit_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    needs_quote = (
        s == "" or s.strip() != s or s.startswith("[[") or s[:1] in "[]{}#&*!|>'\"%@`,-?:"
        or ": " in s or s.lower() in ("null", "true", "false", "yes", "no", "~")
        or _looks_number(s)
    )
    if needs_quote:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _looks_number(s: str) -> bool:
    try:
        float(s); return True
    except ValueError:
        return False


def emit_frontmatter(fields: dict) -> str:
    lines = ["---"]
    for k, v in fields.items():
        if isinstance(v, list):
            inner = ", ".join(_emit_scalar(x) for x in v)
            lines.append(f"{k}: [{inner}]")
        else:
            lines.append(f"{k}: {_emit_scalar(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd Projects/aios/engine && python tools/tests/test_domain_mirror.py`
Expected: exits 0 (add `sys.exit(1 if FAIL else 0)` with a `print(FAIL)` at the end of the test file — see Task 3 Step 1 which finalizes the harness footer).

- [ ] **Step 5: Commit**

```bash
git add engine/tools/domain_mirror.py engine/tools/tests/test_domain_mirror.py
git commit -m "A45: domain_mirror coercions + deterministic emitter (TDD)"
```

---

### Task 2: Per-silo config resolution (profile + schema, fact-free)

**Files:**
- Modify: `engine/tools/domain_mirror.py`
- Test: `engine/tools/tests/test_domain_mirror.py`

**Interfaces:**
- Consumes: the YAML-subset reader from `state_validate`.
- Produces:
  - `find_env_root(start: Path) -> Path` (walks up to the dir holding `profile/domains.yaml`)
  - `load_silo_config(env_root: Path, silo: str) -> dict` returning `{"state_dir": Path, "schema": dict, "tables": [ {name, source_db, fields:[(field,kind,link_tmpl)]} ]}` — reads `state/domains/<silo>/schema.yaml`, extracting each table's `notion_source_db` + `notion_fields`.

- [ ] **Step 1: Write the failing test** (append)

```python
import tempfile, textwrap
def _scratch_silo():
    root = Path(tempfile.mkdtemp())
    (root / "profile").mkdir()
    (root / "profile" / "domains.yaml").write_text("brief:\n  trigger: go\n", encoding="utf-8")
    sd = root / "state" / "domains" / "demo"
    (sd / "tables").mkdir(parents=True)
    (sd / "schema.yaml").write_text(textwrap.dedent("""\
        state-thing:
          required: [name, type, notion_id]
          notion_source_db: things
          notion_fields:
            name: [Name, title]
            flag: ["Is Flag", checkbox]
            owner: [Owner, relation, "things/{slug}"]
        """), encoding="utf-8")
    return root, sd

_root, _sd = _scratch_silo()
check("env_root", dm.find_env_root(_sd) == _root)
cfg = dm.load_silo_config(_root, "demo")
check("cfg_state_dir", cfg["state_dir"] == _sd)
t = cfg["tables"][0]
check("cfg_table_name", t["name"] == "state-thing")
check("cfg_source_db", t["source_db"] == "things")
check("cfg_fields", ("flag", "checkbox", None) in t["fields"])
check("cfg_link_tmpl", ("owner", "relation", "things/{slug}") in t["fields"])
```

- [ ] **Step 2: Run to verify it fails** — `AttributeError: module 'domain_mirror' has no attribute 'find_env_root'`.

- [ ] **Step 3: Write minimal implementation** (append to `domain_mirror.py`)

```python
sys.path.insert(0, str(Path(__file__).resolve().parent))
from state_validate import _parse_yaml, _extract_frontmatter  # reuse the engine's YAML-subset reader


def find_env_root(start: Path) -> Path:
    p = Path(start).resolve()
    for cand in (p, *p.parents):
        if (cand / "profile" / "domains.yaml").is_file():
            return cand
    raise FileNotFoundError(f"no profile/domains.yaml above {start}")


def load_silo_config(env_root: Path, silo: str) -> dict:
    state_dir = (Path(env_root) / "state" / "domains" / silo).resolve()
    schema_path = state_dir / "schema.yaml"
    if not schema_path.is_file():
        raise FileNotFoundError(f"no schema.yaml for silo {silo!r}: {schema_path}")
    schema = _parse_yaml(schema_path.read_text(encoding="utf-8"))
    tables = []
    for tname, tdef in schema.items():
        if not isinstance(tdef, dict) or "notion_fields" not in tdef:
            continue                                   # non-importable table (e.g. state-price manual)
        fields = []
        for field, spec in tdef["notion_fields"].items():
            prop = spec[0]
            kind = spec[1]
            link_tmpl = spec[2] if len(spec) > 2 else None
            fields.append((field, kind, link_tmpl, prop))
        tables.append({"name": tname, "source_db": tdef["notion_source_db"], "fields": fields})
    return {"state_dir": state_dir, "schema": schema, "tables": tables}
```

> Note: `fields` tuples are `(field, kind, link_tmpl, prop)`; the test above checks the first three
> positions via membership on 3-tuples — adjust the test asserts to slice `t["fields"][i][:3]` if
> comparing 3-tuples, OR keep 4-tuples and assert `any(f[0]=="flag" and f[1]=="checkbox" for f in t["fields"])`.
> Use the `any(...)` form (rewrite the two `check("cfg_fields"/"cfg_link_tmpl")` lines accordingly).

- [ ] **Step 4: Run to verify it passes** — exits 0.

- [ ] **Step 5: Commit**

```bash
git add engine/tools/domain_mirror.py engine/tools/tests/test_domain_mirror.py
git commit -m "A45: fact-free per-silo config resolution (profile + schema notion_fields)"
```

---

### Task 3: Import driver + CLI + fixture-silo genericity proof (silo #2)

**Files:**
- Modify: `engine/tools/domain_mirror.py`
- Create: `engine/tools/tests/fixtures/domain_mirror/{things-export.json, people-export.json}` (via the test, in a temp dir — no committed fixtures needed beyond what the test writes)
- Test: `engine/tools/tests/test_domain_mirror.py`

**Interfaces:**
- Consumes: `coerce`, `emit_frontmatter`, `notion_id_from_url`, `load_silo_config`, `find_env_root`.
- Produces:
  - `build_record(table: dict, row: dict, url_to_slug: dict) -> tuple[str, str]` returning `(slug, file_text)`
  - `import_silo(env_root: Path, silo: str, snapshot_dir: Path, out_dir: Path | None = None, *, dry_run=False) -> list[Path]`
  - `main(argv)` wiring `import --silo … [--snapshot-dir] [--out] [--dry-run]`

- [ ] **Step 1: Finalize the test harness footer + write the end-to-end fixture test** (append, then add the footer ONCE at the very end of the file)

```python
def _write_export(path, rows, url_to_slug):
    path.write_text(json.dumps({"_meta": {}, "url_to_slug": url_to_slug, "rows": rows}), encoding="utf-8")

# Extend the demo silo with a second table so we prove >=2 tables from one code path.
(_sd / "schema.yaml").write_text(textwrap.dedent("""\
    state-thing:
      required: [name, type, notion_id]
      notion_source_db: things
      notion_fields:
        name: [Name, title]
        flag: ["Is Flag", checkbox]
        owner: [Owner, relation, "people/{slug}"]
    state-person:
      required: [name, type, notion_id]
      notion_source_db: people
      notion_fields:
        name: [Name, title]
    """), encoding="utf-8")

snap = _sd / "_snap"; snap.mkdir()
_write_export(snap / "people-export.json",
              [{"Name": "Ada", "url": "https://n/p1"}], {"https://n/p1": "ada"})
_write_export(snap / "things-export.json",
              [{"Name": "Widget", "Is Flag": "__YES__", "Owner": "https://n/p1", "url": "https://n/t1"}],
              {"https://n/t1": "widget", "https://n/p1": "ada"})

written = dm.import_silo(_root, "demo", snap)
check("import_wrote_both", (_sd / "tables" / "state-thing" / "widget.md").is_file()
      and (_sd / "tables" / "state-person" / "ada.md").is_file())
txt = (_sd / "tables" / "state-thing" / "widget.md").read_text(encoding="utf-8")
check("import_type", "type: state-thing" in txt)
check("import_checkbox", "flag: true" in txt)
check("import_relation", 'owner: "[[people/ada]]"' in txt)
check("import_notion_id", "notion_id: t1" in txt)
# idempotent
before = txt
dm.import_silo(_root, "demo", snap)
check("idempotent", (_sd / "tables" / "state-thing" / "widget.md").read_text(encoding="utf-8") == before)
# fail-loud on dangling relation
_write_export(snap / "things-export.json",
              [{"Name": "Orphan", "Owner": "https://n/missing", "url": "https://n/t2"}],
              {"https://n/t2": "orphan"})
try:
    dm.import_silo(_root, "demo", snap); check("dangling_raises", False)
except KeyError: check("dangling_raises", True)

# ---- harness footer (exactly once, at end of file) ----
print("FAILURES:", FAIL)
sys.exit(1 if FAIL else 0)
```

- [ ] **Step 2: Run to verify it fails** — `AttributeError: … 'import_silo'`.

- [ ] **Step 3: Write minimal implementation** (append to `domain_mirror.py`)

```python
def build_record(table, row, url_to_slug) -> tuple[str, str]:
    slug = url_to_slug[row["url"]]
    fm = {"type": table["name"]}
    for field, kind, link_tmpl, prop in table["fields"]:
        fm[field] = coerce(kind, row.get(prop), url_to_slug=url_to_slug, link_tmpl=link_tmpl)
    fm["notion_id"] = notion_id_from_url(row["url"])
    body = (row.get("Description") or "").strip()
    text = emit_frontmatter(fm) + ("\n" + body + "\n" if body else "")
    return slug, text


def import_silo(env_root, silo, snapshot_dir, out_dir=None, *, dry_run=False):
    cfg = load_silo_config(env_root, silo)
    out = Path(out_dir) if out_dir else cfg["state_dir"] / "tables"
    snapshot_dir = Path(snapshot_dir)
    written = []
    for table in cfg["tables"]:
        export = snapshot_dir / f"{table['source_db']}-export.json"
        if not export.is_file():
            raise FileNotFoundError(f"[{silo}/{table['name']}] missing snapshot: {export}")
        data = json.loads(export.read_text(encoding="utf-8"))
        url_to_slug = data["url_to_slug"]
        tdir = out / table["name"]
        for row in data["rows"]:
            slug, text = build_record(table, row, url_to_slug)
            dest = tdir / f"{slug}.md"
            if not dry_run:
                tdir.mkdir(parents=True, exist_ok=True)
                dest.write_text(text, encoding="utf-8")
            written.append(dest)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(prog="domain_mirror.py")
    sub = ap.add_subparsers(dest="verb", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("--silo", required=True)
    imp.add_argument("--snapshot-dir")
    imp.add_argument("--out")
    imp.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    env_root = find_env_root(Path(__file__))
    snap = Path(args.snapshot_dir) if args.snapshot_dir else \
        env_root / "state" / "domains" / args.silo / "_snapshots"
    written = import_silo(env_root, args.silo, snap, args.out, dry_run=args.dry_run)
    print(f"{'[dry-run] ' if args.dry_run else ''}{len(written)} records for silo {args.silo!r}")
    for p in written:
        print("  ", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd Projects/aios/engine && python tools/tests/test_domain_mirror.py && python -m pytest tools/tests/ -q`
Expected: script exits 0; suite green (new script auto-collected).

- [ ] **Step 5: Commit**

```bash
git add engine/tools/domain_mirror.py engine/tools/tests/test_domain_mirror.py
git commit -m "A45: import driver + CLI; fixture 2-table silo proves genericity + idempotency + fail-loud"
```

---

### Task 4: FamilyOffice `notion_fields` + real regression (silo #1, semantic equivalence)

**Files:**
- Modify: `state/domains/familyoffice/schema.yaml`
- Test: `engine/tools/tests/test_domain_mirror.py` (add a guarded real-data regression, skipped if snapshots absent)

**Interfaces:**
- Consumes: `import_silo`, the real FO snapshots at `Projects/family-office/state-mirror/migration/*-notion-export-*.json`, and the existing shipped records at `state/domains/familyoffice/tables/**`.

- [ ] **Step 1: Author `notion_fields` for each importable FO table.** For every table that has a migrator, translate its `build_frontmatter` mapping into a `notion_fields:` flow-list block. Encode from the migrator source (`Projects/family-office/state-mirror/migration/migrate_<table>.py`). Example for `state-entity` (from `migrate_entities.py`):

```yaml
state-entity:
  required: [name, type, notion_id]
  enums: { ... unchanged ... }
  bools: [articles_filed, ein_issued, oa_executed, bank_account, reg_agent_paid]
  relations: [parent_entity, project, wiki]
  dates: [formation_date, target_close, last_synced]
  notion_source_db: entities
  notion_fields:
    name:               [Name, title]
    role:               [Role, text]
    entity_legal_form:  [Type, select]
    tax_classification: ["Tax Entity", select]
    status:             [Status, select]
    formation_state:    [State, select]
    tier_level:         ["Tier Level", select]
    project:            ["Project (filter)", select]
    articles_filed:     ["Articles Filed", checkbox]
    ein_issued:         ["EIN Issued", checkbox]
    oa_executed:        ["OA Executed", checkbox]
    bank_account:       ["Bank Account", checkbox]
    reg_agent_paid:     ["Reg Agent Paid", checkbox]
    parent_entity:      ["Parent Entity", relation, "entities/{slug}"]
```

Repeat for each FO importable table (assets, tasks, decisions, change-log, notes, budget-*, prices as applicable), copying the exact property names from that table's migrator. **Do not** add `notion_fields` to tables with no Notion source (e.g. a manually-maintained `state-price`) — those keep working unimported.

- [ ] **Step 2: Write the regression test** (append, before the harness footer)

```python
import glob, subprocess
_FO_SNAP = Path(_TOOLS).resolve().parents[2] / "family-office" / "state-mirror" / "migration"
_FO_STATE = Path(_TOOLS).resolve().parents[2].parent / "state" / "domains" / "familyoffice"
if _FO_SNAP.is_dir() and (_FO_STATE / "schema.yaml").is_file():
    import tempfile
    tmp_out = Path(tempfile.mkdtemp()) / "tables"
    # normalize FO export filenames (they carry dates) -> <db>-export.json into a scratch snap dir
    snap = Path(tempfile.mkdtemp())
    for f in glob.glob(str(_FO_SNAP / "*-notion-export-*.json")):
        db = Path(f).name.split("-notion-export-")[0]
        (snap / f"{db}-export.json").write_text(Path(f).read_text(encoding="utf-8"), encoding="utf-8")
    env_root = _FO_STATE.parents[2]  # .../state/domains/familyoffice -> env root
    dm.import_silo(env_root, "familyoffice", snap, tmp_out)
    # semantic equivalence: parsed frontmatter dicts equal (mirror body only; strip generated block)
    def _norm(p):
        t = p.read_text(encoding="utf-8")
        return _extract_frontmatter(t)  # dict; ignores body + generated DataviewJS append
    mism = []
    for gen in tmp_out.rglob("*.md"):
        rel = gen.relative_to(tmp_out)
        shipped = _FO_STATE / "tables" / rel
        if not shipped.is_file() or _norm(gen) != _norm(shipped):
            mism.append(str(rel))
    check("fo_semantic_equivalence", not mism)
    if mism:
        print("FO regression mismatches:", mism[:10])
    # validator PASS on the generated tree
    r = subprocess.run([sys.executable, os.path.join(_TOOLS, "state_validate.py"),
                        "--schema", str(_FO_STATE / "schema.yaml"), "--all", str(tmp_out)],
                       capture_output=True, text=True)
    check("fo_validator_pass", r.returncode == 0)
else:
    print("FO snapshots absent — skipping real regression (fixture proof stands)")
```

- [ ] **Step 3: Run + reconcile mapping** — `cd Projects/aios/engine && python tools/tests/test_domain_mirror.py`. If `fo_semantic_equivalence` fails, the printed mismatch list shows which table/field diverges; fix the `notion_fields` mapping (a wrong property name or missing field) until the parsed dicts match. Expected end state: exits 0.

- [ ] **Step 4: Fact-free grep + full suite**

Run:
```bash
cd Projects/aios/engine
grep -Ei '[0-9a-f]{32}|state/domains/(familyoffice|gm|personal)|notion.*database.*id' tools/domain_mirror.py || echo "FACT-FREE OK (empty)"
python -m pytest tools/tests/ -q
```
Expected: grep prints "FACT-FREE OK (empty)"; suite green.

- [ ] **Step 5: Commit**

```bash
git add engine/tools/domain_mirror.py engine/tools/tests/test_domain_mirror.py \
        ../../state/domains/familyoffice/schema.yaml
git commit -m "A45: FO notion_fields + real semantic-equivalence regression (silo #1 proven)"
```

> Note: `state/domains/familyoffice/schema.yaml` lives in the env-root `claude-env` repo, NOT the
> aios repo. Stage/commit it in the correct repo (`cd` to env root for that file). Keep the two
> commits separate per-repo; the plan's `git add` above is illustrative — split by repo at execution.

---

### Task 5: Retire FO migrators; update backlog + cross-links

**Files:**
- Modify: `Projects/family-office/state-mirror/migration/` → replace `migrate_*.py` + `fo_paths.py` with `README.md`
- Modify: `Projects/aios/BACKLOG.md` (A45 acceptance → import-only; retire + H25 cross-link)
- Modify: `Projects/general-management/skills/notion-mirror/SKILL.md` (deferred-publish pointer note)

- [ ] **Step 1: Write the pointer README** at `Projects/family-office/state-mirror/migration/README.md`:

```markdown
# RETIRED — replaced by the generic engine importer (AIOS A45, 2026-07-09)

The ~20 `migrate_*.py` scripts + `fo_paths.py` that lived here were one-shot, per-table Notion
importers with hardcoded property→field maps. They are superseded by the fact-free
`Projects/aios/engine/tools/domain_mirror.py import --silo familyoffice`, driven by the declarative
`notion_fields:` blocks in `state/domains/familyoffice/schema.yaml`.

- Snapshot exports (`*-notion-export-*.json`) are KEPT here — they are the importer's input.
- The DataviewJS render-views leg (`../views/build_pages.py`) is NOT retired — it folds into
  state-consolidation #4 (dashboards).
- History: git. Spec/plan: `Projects/aios/docs/superpowers/{specs,plans}/2026-07-09-a45-domain-mirror*`.
```

- [ ] **Step 2: Remove the retired scripts** (never hard-delete blindly — git preserves history):

```bash
cd Projects/family-office/state-mirror/migration
git rm migrate_*.py fo_paths.py
git add README.md
```

- [ ] **Step 3: Update `Projects/aios/BACKLOG.md`** — edit A45's acceptance line to the import-only scope (retire FO state-mirror ✅; GM publish + render-views deferred to #4; ≥2-silo proof = FO real + fixture demo silo; real Personal = H25). Add a one-line cross-link on H25's env-ops item is out of scope here (different repo) — note it in the close-out instead.

- [ ] **Step 4: Add the GM pointer note** — one line at the top of `Projects/general-management/skills/notion-mirror/SKILL.md`:

```markdown
> **A45 (2026-07-09):** this is the deferred `publish` (records→Notion) leg. `domain_mirror.py` owns
> the `import` direction only for now; a future `domain_mirror.py publish` verb folds this in under
> state-consolidation #4 or when a 2nd local-SSOT silo needs it. Left in place until then.
```

- [ ] **Step 5: Commit (per repo)**

```bash
# family-office repo
cd Projects/family-office && git commit -m "A45: retire per-table migrators -> pointer README (replaced by engine importer)"
# aios repo
cd Projects/aios && git add BACKLOG.md && git commit -m "A45: trim acceptance to import-only; note retire + deferrals"
# general-management repo
cd Projects/general-management && git add skills/notion-mirror/SKILL.md && git commit -m "A45: mark notion-mirror as the deferred publish leg"
```

---

## Self-Review

**1. Spec coverage:**
- Import-only Notion→records ✅ (Tasks 1–3). Declarative config, fact-free ✅ (Task 2 + Global Constraints + Task 4 grep). Headless-safe snapshot transform ✅ (Task 3 `import_silo` reads JSON only). Retire FO migrators ✅ (Task 5). Defer render-views + GM publish ✅ (Task 5 README + SKILL note).
- **≥2-silo proof — intentional deviation from spec acceptance #4:** the plan proves genericity via **FO (real, semantic-equivalence) + a synthetic fixture `demo` silo (unit)**, and delivers the **real Personal silo under H25** (its own parked goal), rather than fusing Personal into A45. Rationale: keeps A45 a clean, unparked dev item and H25 its own parked ops item — matching the backlog's two-item decomposition and the two compiled goals. Flag to Seth (done in the handoff message).
- **Byte-identical → semantic equivalence (refines spec O1):** the engine is stdlib-only; matching PyYAML's exact byte output is fragile and valueless. Regression compares parsed frontmatter dicts (mirror body only, generated block stripped via `_extract_frontmatter`). Stronger where it matters (data), silent on formatting (the emitter's own concern, tested for determinism in Task 1/3).
- O2 (who produces the LifeOS snapshot) and the real Personal build → **H25**, not this plan.

**2. Placeholder scan:** No TBD/TODO; every code step carries runnable code; test code is concrete. Task 4 Step 1 requires transcribing each FO table's real property names from its migrator — that's a mechanical, source-defined step, not a placeholder (the pattern + one full example are given).

**3. Type consistency:** `import_silo(env_root, silo, snapshot_dir, out_dir=None, *, dry_run=False)`, `build_record(table, row, url_to_slug) -> (slug, text)`, `coerce(kind, value, *, url_to_slug, link_tmpl)`, `load_silo_config(...) -> {"state_dir","schema","tables":[{"name","source_db","fields":[(field,kind,link_tmpl,prop)]}]}`, `emit_frontmatter(dict) -> str`, `notion_id_from_url(str) -> str`, `find_env_root(Path) -> Path` — consistent across tasks. (Task 2 test asserts adjusted to `any(...)` over 4-tuples per its inline note.)

## Cross-repo note (execution-critical)

This plan touches **three** git repos: `aios` (tool + tests + BACKLOG), `claude-env` env root (`state/domains/familyoffice/schema.yaml`), and `family-office` + `general-management` (retire/pointer). Commit each file in its owning repo; never stage across a repo boundary. This is a native session — commit + push each repo at its task boundary.
