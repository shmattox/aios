#!/usr/bin/env python3
"""domain_sync.py — domain-sync Plan 3 orchestration: Notion -> snapshot -> `import_silo`.

Task 2 (this slice): the SNAPSHOT GENERATOR — turn live Notion rows into the on-disk
`<snapshot_dir>/<source_db>-export.json` `import_silo` (domain_mirror.py) already reads.
That file was always hand-made before (the FO migration's `*-notion-export-*.json`); this
is the seam that produces it going forward, so a sync no longer needs a human to run a
one-off export script per table.

  write_snapshot(silo, table_cfg, rows, url_to_slug, snapshot_dir) -> Path
      Writes the export JSON. `table_cfg` is a `domain_mirror.load_silo_config(...)["tables"]`
      entry (or any dict carrying a `source_db` key) — its `source_db` drives the on-disk
      path via pathlib, so a NESTED source_db ("logs/change-log") lands at
      `snapshot_dir/logs/change-log-export.json`, matching import_silo's own
      `snapshot_dir / f"{db}-export.json"` lookup byte-for-byte (domain_mirror.import_silo).

  gather_table(silo, table_cfg, ...) -> list[dict]
      Fetches one table's rows LIVE from Notion, already shaped as `write_snapshot` /
      `import_silo` expect. Reuses notion_gather's token resolution + paginating
      `_query_source` (same auth/endpoint-fallback path as every other headless gather,
      A18); this module supplies only the per-property -> raw-row-value normalization
      `notion_gather.normalize_page` does not attempt (that function collapses a page to a
      generic title/status/priority/due summary for urgency triage — a sync needs every
      raw property, verbatim, in the export-JSON convention the frozen FO exports and
      `domain_mirror.coerce` already speak: checkboxes as `__YES__`/`__NO__`, a date
      property fanned out to `date:<Name>:start`/`:end`/`:is_datetime`, etc.).

Reap (Task 3), the walker + CLI + sync-status sidecar (Task 3/4 wiring) land in this same
file as later slices — see the plan (`docs/superpowers/plans/2026-08-27-domain-sync-
plan-3-the-pipe.md`) §Task 3/4. Hermetic: this module never talks to live Notion except
inside `gather_table`, and every test here drives it through a FAKE gather (a fixture list
of rows passed straight to `write_snapshot`) — see `tests/test_domain_sync.py`.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notion_gather  # noqa: E402  (reuse, don't reimplement — token resolution + _query_source)


# ─────────────────────────────────────────────────────────────────────────────
# The snapshot writer
# ─────────────────────────────────────────────────────────────────────────────

def write_snapshot(silo, table_cfg, rows, url_to_slug, snapshot_dir, *, exported=None) -> Path:
    """Write one table's `<source_db>-export.json` snapshot.

    Shape: `{"_meta": {"exported", "row_count", "silo", "source_db"}, "rows": rows,
    "url_to_slug": url_to_slug}` — exactly what `domain_mirror.import_silo` reads
    (`exports[db] = json.loads(...)`; `slug_maps[db] = exports[db]["url_to_slug"]`;
    `eff_last_synced = (data.get("_meta") or {}).get("exported")`).

    `rows` and `url_to_slug` are passed through VERBATIM (Paper-Governs faithfulness —
    this function does not touch a single value; `stable_slugs()` is what produced
    `url_to_slug` and this only serializes it). `exported` defaults to today (UTC,
    YYYY-MM-DD); callers that need a deterministic snapshot (tests, the golden
    round-trip proof) pass it explicitly.

    Snapshots are TRANSIENT + gitignored (`state/_snapshots/`, Global Constraints) — no
    diff-stability requirement, unlike `stable_slugs.save_slugmap`'s sorted-key rule.
    """
    db = table_cfg["source_db"] if isinstance(table_cfg, dict) else table_cfg
    dest = Path(snapshot_dir) / f"{db}-export.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "exported": exported or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "row_count": len(rows),
        "silo": silo,
        "source_db": db,
    }
    dest.write_text(
        json.dumps({"_meta": meta, "rows": rows, "url_to_slug": url_to_slug},
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return dest


# ─────────────────────────────────────────────────────────────────────────────
# The live fetch — thin, on top of notion_gather's primitives
# ─────────────────────────────────────────────────────────────────────────────

def _date_pairs(name, prop):
    v = prop.get("date")
    if v is None:
        return {f"date:{name}:start": None, f"date:{name}:end": None, f"date:{name}:is_datetime": None}
    start = v.get("start")
    return {
        f"date:{name}:start": start,
        f"date:{name}:end": v.get("end"),
        # 0/1 int, matching the frozen FO exports' own convention (sessions-notion-export).
        f"date:{name}:is_datetime": int(bool(start) and "T" in start),
    }


def _row_value(name, prop):
    """One Notion property object -> `[(key, value), ...]` in the raw row-value
    convention `write_snapshot`/`domain_mirror.coerce` speak. Usually one pair; a
    `date` property fans out to three (`:start`/`:end`/`:is_datetime`), reproducing the
    frozen FO exports' own flattening (see e.g. `sessions-notion-export-*.json`'s
    `"date:Date:start"` key, which `stable_slugs._sessions_slug` reads directly).

    checkbox -> `__YES__`/`__NO__` (coerce's literal contract). select/status -> the
    option name. multi_select -> a plain LIST of names — the `multi_select` kind's raw-
    list contract (Personal/GM's schemas map multi_select fields this way; see
    `state/domains/personal/schema.yaml`). relation -> a list of derived
    `https://www.notion.so/<id>` urls — the `relation` kind's raw-list contract.

    NOT reproduced live: the FamilyOffice-specific `json_multi_select`/`json_relation` kinds
    expect a JSON-ARRAY-STRING, an artifact of specific retired migrators' own export
    scripts (verified inconsistent even across FO's own frozen exports — `Key Members`
    JSON-string vs `Parent Entity` a genuine list, both "relation-shaped" Notion
    properties). A silo that maps a live-gathered field as json_multi_select/
    json_relation needs its own adapter on top of this, same as any other schema-
    specific rule — out of scope here (Task 2's correctness gate is FAKED gather only;
    the live path is exercised by Task 4's smoke on a non-economic silo, whose mapped
    tables use the plain `multi_select`/no-relation conventions this function speaks)."""
    t = prop.get("type")
    if t == "date":
        return list(_date_pairs(name, prop).items())
    v = prop.get(t)
    if t in ("title", "rich_text"):
        return [(name, ("".join(p.get("plain_text", "") for p in (v or [])) or None))]
    if t == "checkbox":
        return [(name, "__YES__" if v else "__NO__")]
    if t in ("select", "status"):
        return [(name, (v or {}).get("name"))]
    if t == "multi_select":
        return [(name, [o.get("name") for o in (v or [])] or None)]
    if t == "relation":
        return [(name, [f"https://www.notion.so/{r['id'].replace('-', '')}" for r in (v or [])] or None)]
    if t == "people":
        return [(name, [p.get("name") or p.get("id") for p in (v or [])] or None)]
    if t == "formula":
        fv = v or {}
        return [(name, fv.get(fv.get("type")))]
    if t in ("number", "url", "email", "phone_number"):
        return [(name, v)]
    return [(name, None)]  # files/rollups/etc: not carried (same scope limit as normalize_page)


def _row_from_page(page):
    row = {"url": page.get("url")}
    for name, prop in (page.get("properties") or {}).items():
        for k, v in _row_value(name, prop):
            row[k] = v
    return row


def gather_table(silo, table_cfg, *, token=None, token_env=notion_gather.DEFAULT_TOKEN_NAME,
                 page_size=100):
    """Fetch one table's rows LIVE, shaped for `write_snapshot`. `table_cfg` needs a
    `notion_db` key (the already-resolved database/data-source id or `collection://`
    ref, from the silo's `profile/domains.yaml` — resolving it is the CALLER's job, same
    as `notion_gather.cmd_tasks` resolves `--db` from its own CLI args; the walker
    (Task 3) is what pairs a schema table with its profile id).

    Fails loud: no token -> `notion_gather._no_token_exit` (process exit 2, same
    contract as every other headless gather); a query error (bad id, HTTP failure after
    both endpoint attempts) -> `RuntimeError` naming the table. Never returns a partial
    result silently — a sync run must know a table's fetch failed, not treat it as an
    empty table (which the reap's volume brake would otherwise read as "everything
    vanished")."""
    tok = token
    if tok is None:
        tok, _src = notion_gather.resolve_token(token_env)
        if not tok:
            notion_gather._no_token_exit(token_env)  # exits — no token, no silent partial gather
    db_id = notion_gather.normalize_id(table_cfg["notion_db"])
    _endpoint, pages, err = notion_gather._query_source(db_id, tok, page_size)
    if err is not None:
        name = table_cfg.get("name") or table_cfg.get("source_db") or table_cfg["notion_db"]
        raise RuntimeError(f"gather_table[{silo}/{name}]: {err}")
    return [_row_from_page(p) for p in pages]
