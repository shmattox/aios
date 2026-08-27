#!/usr/bin/env python3
"""stable_slugs.py — the slug machinery (domain-sync Plan 3 Task 1, S14/A84).

**Slugs are identities, not summaries.** `stable_slugs()` is first-seen-wins,
keyed on `notion_id`, backed by a per-silo committed `_slugmap.json`
(`{notion_id: slug}`). A Notion title edit NEVER re-slugs an existing record —
re-slugging on every title change would churn git history and break
wikilinks. See spec `docs/superpowers/specs/2026-07-15-domain-sync-notion-
to-local-design.md` §"Hard part 1 — slug stability".

The per-table slug RULES below are recovered VERBATIM from the retired
FamilyOffice migrators (`family-office/state-mirror/migration/migrate_<table>.py`,
deleted across three ports — recover with `git show <sha>^:path`):
  - `4876cf9` (deleted entities/insurance/notes/people/tax_ledger)
  - `872386a` (deleted tasks/manifest/change_log/decision_log/sessions)
  - `93f753a` (deleted projects)
  - `acb546a` (deleted prices)
  - `dfab07f` (deleted assets)

Ten of the eleven bespoke migrators (insurance/notes/people/tax_ledger/tasks/
manifest/change_log/decision_log/projects/assets) share one BYTE-IDENTICAL
`slugify()` + `unique_slug()` pair, differing only in which export field
carries the row's name and the empty-name fallback word — reproduced once as
the generic rule and dispatched per table. `entities` never ran its own
slugify (`migrate_entities.py` only ever *consumed* a precomputed
`url_to_slug` from its export) — but the frozen export's `url_to_slug` was
verified byte-for-byte reproducible by the same generic rule on `Name`
(11/11 rows, zero collisions), so it is filed under the generic dispatch too.
`prices` and `sessions` are genuinely bespoke (see below) and are reproduced
as their own functions.

Every one of the thirteen rules below was proven, row-by-row, to reproduce
its table's golden `notion_id -> slug` map exactly against the frozen FO
exports (Task 1 Step 5 correctness gate) — see `test_stable_slugs.py`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from domain_mirror import notion_id_from_url  # noqa: E402  (reuse, don't reimplement)


# ─────────────────────────────────────────────────────────────────────────────
# Generic rule — verbatim from migrate_insurance.py / migrate_notes.py /
# migrate_people.py / migrate_tax_ledger.py / migrate_tasks.py /
# migrate_manifest.py / migrate_change_log.py / migrate_decision_log.py /
# migrate_projects.py / migrate_assets.py (equivalent — migrate_assets.py's
# `slugify` omits the `(name or "")` None-guard since its caller never passes
# None, but is otherwise identical and behaves the same on every real row):
#
#   def slugify(name: str) -> str:
#       s = _NONALNUM.sub("-", (name or "").strip().lower()).strip("-")
#       return s[:60].rstrip("-")
#
#   def unique_slug(name: str, notion_id: str, seen: set) -> str:
#       base = slugify(name) or "<table-default>"
#       slug = base
#       if slug in seen:
#           slug = f"{base}-{notion_id[-6:]}"
#       seen.add(slug)
#       return slug
#
# `_NONALNUM = re.compile(r"[^a-z0-9]+")` in every one of the ten migrators.
# ─────────────────────────────────────────────────────────────────────────────
_NONALNUM = re.compile(r"[^a-z0-9]+")


def _slugify(name) -> str:
    s = _NONALNUM.sub("-", (name or "").strip().lower()).strip("-")
    return s[:60].rstrip("-")


def _unique_slug(name, notion_id: str, default: str, seen: set) -> str:
    base = _slugify(name) or default
    slug = base
    if slug in seen:
        slug = f"{base}-{notion_id[-6:]}"
    seen.add(slug)
    return slug


# table basename -> (name field in the export row, fallback word when name is empty).
# Fallback words + name fields recovered from each migrator's `unique_slug(row[<field>], ...)`
# call and its `base = slugify(name) or "<default>"` line.
_GENERIC_RULES = {
    "entities": ("Name", "entity"),            # entities' own export url_to_slug verified == this rule
    "insurance": ("Name", "insurance"),
    "notes": ("Note", "note"),
    "people": ("Name", "person"),
    "tax-ledger": ("Event", "tax-ledger-row"),
    "tasks": ("Item", "task"),
    "manifest": ("Name", "manifest-row"),
    "change-log": ("Item", "change"),
    "decision-log": ("Decision", "decision"),
    "projects": ("Project", "project"),
    "assets": ("Name", "asset"),
}


# ─────────────────────────────────────────────────────────────────────────────
# Bespoke rule — prices. Verbatim from migrate_prices.py:
#
#   def _slug(name: str) -> str:
#       return name.strip().lower()
#
# No collision handling in the original (a market-price catalog's names are
# unique by construction) — reproduced verbatim, not hardened.
# ─────────────────────────────────────────────────────────────────────────────
def _prices_slug(row: dict) -> str:
    return row["Name"].strip().lower()


# ─────────────────────────────────────────────────────────────────────────────
# Bespoke rule — sessions. Verbatim from migrate_sessions.py: a date+title
# COMPOSITE (title-only collides constantly across generic session titles and
# shared-date rows; a bare notion_id-suffix reads poorly in a chronological
# log), with the numeric `Session ID` as the collision-breaker instead of the
# generic rule's `notion_id[-6:]`:
#
#   _LEADING_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}\s*[—\-:]*\s*")
#   _LEADING_SES  = re.compile(r"^(?:SES-\d+|Session\s*\d+)\s*[—\-:]*\s*", re.IGNORECASE)
#
#   def _title_fragment(title):
#       stripped = _LEADING_SES.sub("", title or "")
#       stripped = _LEADING_DATE.sub("", stripped)
#       return slugify(stripped)[:40]
#
#   def unique_slug(date_str, title, session_id, seen):
#       frag = _title_fragment(title)
#       if date_str and frag:   base = f"{date_str}-{frag}"
#       elif date_str:          base = date_str
#       else:                   base = frag or f"session-{session_id:03d}"
#       slug = base
#       if slug in seen:
#           slug = f"{base}-{session_id:03d}"
#       seen.add(slug)
#       return slug
# ─────────────────────────────────────────────────────────────────────────────
_LEADING_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}\s*[—\-:]*\s*")
_LEADING_SES = re.compile(r"^(?:SES-\d+|Session\s*\d+)\s*[—\-:]*\s*", re.IGNORECASE)


def _session_title_fragment(title) -> str:
    stripped = _LEADING_SES.sub("", title or "")
    stripped = _LEADING_DATE.sub("", stripped)
    return _slugify(stripped)[:40]


def _sessions_slug(row: dict, notion_id: str, seen: set) -> str:
    date_str = row.get("date:Date:start")
    frag = _session_title_fragment(row.get("Session"))
    session_id = row.get("Session ID")
    if date_str and frag:
        base = f"{date_str}-{frag}"
    elif date_str:
        base = date_str
    else:
        base = frag or f"session-{session_id:03d}"
    slug = base
    if slug in seen:
        slug = f"{base}-{session_id:03d}"
    seen.add(slug)
    return slug


def _new_slug_for(table_key: str, row: dict, notion_id: str, seen: set, title_field=None) -> str:
    """Dispatch a NEW notion_id (not yet in the slugmap) to its table's bespoke rule.

    `title_field` (Task 2, S14/A84 Plan-4) is the caller's schema-derived title
    property name — when given it WINS over the `_GENERIC_RULES` dict's name
    field, generalizing slug assignment past the FamilyOffice-only hardcoded dict to any
    silo. The dict remains the fallback (so an omitted `title_field` reproduces
    the exact FO golden path byte-for-byte) and the source of the empty-name
    default word when the dict has an entry for this table.
    """
    if table_key == "prices":
        return _prices_slug(row)
    if table_key == "sessions":
        return _sessions_slug(row, notion_id, seen)
    dict_field, dict_default = _GENERIC_RULES.get(table_key, (None, None))
    name_field = title_field or dict_field
    default = dict_default or (table_key.rstrip("s") or table_key)  # deterministic fallback word
    if name_field is None:
        raise ValueError(f"stable_slugs: no title_field and no rule for table {table_key!r}")
    return _unique_slug(row.get(name_field), notion_id, default, seen)


def stable_slugs(table_name: str, rows: list[dict], slugmap: dict, *, title_field=None) -> dict:
    """First-seen-wins slug assignment for one table's export rows.

    `slugmap` is the persistent `{notion_id: slug}` map (mutated in place AND
    returned, so callers can persist it via `save_slugmap`). A row whose
    `notion_id` is already in `slugmap` KEEPS its existing slug even if its
    title changed (A84) — a new row gets the table's bespoke rule and the map
    is updated. Returns `{url: slug}` for this call's rows, the shape
    `import_silo` reads as `exports[db]["url_to_slug"]`.

    `table_name` may be a bare table key ("sessions") or a nested
    `source_db` ("logs/sessions") — dispatch normalizes on the basename so
    either form from a caller works.

    `title_field` (keyword-only, Task 2 Plan-4) is the schema-derived Notion
    title property name for this table (the `notion_fields` entry whose spec
    is `[<Prop>, "title"]`). When given, it wins over the FO `_GENERIC_RULES`
    dict for the generic (non-prices/non-sessions) dispatch, so any silo's
    table slugs correctly from its own schema. `title_field=None` (the
    default) reproduces the existing dict-only behavior exactly — this is
    what keeps the FO golden reproduction byte-exact.
    """
    table_key = Path(table_name).name
    seen = set(slugmap.values())  # global uniqueness, not just this batch (A84 incremental sync)
    url_to_slug = {}
    for row in rows:
        notion_id = notion_id_from_url(row["url"])
        if notion_id in slugmap:
            slug = slugmap[notion_id]
        else:
            slug = _new_slug_for(table_key, row, notion_id, seen, title_field=title_field)
            slugmap[notion_id] = slug
        url_to_slug[row["url"]] = slug
    return url_to_slug


def load_slugmap(path) -> dict:
    """Load a committed `_slugmap.json` ({notion_id: slug}); missing file -> {}."""
    p = Path(path)
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save_slugmap(path, slugmap: dict) -> None:
    """Persist the `{notion_id: slug}` map, deterministic key order for diff-stable commits."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(dict(sorted(slugmap.items())), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
