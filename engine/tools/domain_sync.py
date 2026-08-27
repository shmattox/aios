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
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notion_gather  # noqa: E402  (reuse, don't reimplement — token resolution + _query_source)
from state_validate import _extract_frontmatter  # noqa: E402  (reuse — same reader domain_mirror uses)


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


# ─────────────────────────────────────────────────────────────────────────────
# reap_orphans — the guarded, Paper-Governs reap (Task 3, S10/A49). The ONLY
# destructive step of the whole pipe. Every guard below is load-bearing — do not
# relax one to make a caller's life easier; see the plan's Global Constraints
# (docs/superpowers/plans/2026-08-27-domain-sync-plan-3-the-pipe.md) and Paper-
# Governs gate 1 (reap-scope sign-off is a prerequisite to running this live).
# ─────────────────────────────────────────────────────────────────────────────

class VolumeBrakeExceeded(RuntimeError):
    """Raised when a table's orphan set would exceed `volume_brake` of its on-disk
    record count. A mass-removal smells like a bad/partial fetch, not a real
    deletion (Global Constraints). The WHOLE reap aborts when this fires — nothing
    is moved for ANY table in the run, even ones under the brake, because a
    fetch bad enough to orphan a fifth of one table is reason to distrust the
    entire run (Paper-Governs: fail loud, never partial)."""


@dataclass
class TableReapResult:
    """One mapped table's reap outcome. `orphans` is always populated (the plan);
    `moved` is populated only when the move actually happened (empty under
    `dry_run`, since dry_run reports the plan and moves nothing).

    `total` is every `*.md` file considered; `eligible` is the subset that had a
    parseable frontmatter AND a truthy `notion_id` (only THESE can ever be judged
    an orphan, and only THESE count toward the volume-brake denominator —
    IMPORTANT-2 fix-loop finding: `total` would otherwise dilute the brake with
    records that were never at risk of being reaped). `skipped_no_id` is the
    count kept-and-not-reaped solely because their `notion_id` was missing/empty
    (surfaced for Task 4's sidecar, not silently dropped)."""
    total: int              # on-disk *.md record count considered for this table
    eligible: int            # subset with parseable frontmatter + truthy notion_id
    skipped_no_id: int        # kept (not reaped) — parseable but falsy notion_id
    orphans: list              # filenames (str) whose notion_id was absent from fetched
    moved: list                 # [(old_path str, new_path str), ...] — actual moves


@dataclass
class ReapReport:
    """Full-silo reap outcome. `skipped=True` means degraded-fetch guard fired —
    `by_table` is then empty and nothing anywhere was touched."""
    silo: str
    dry_run: bool
    skipped: bool
    reason: str              # populated only when skipped
    date: str                 # the `_retired/<date>/` stamp used (or would be used)
    by_table: dict            # table (source_db str) -> TableReapResult


def _collision_safe_dest(dest_dir, filename, notion_id):
    """Never let an archive move overwrite an existing file at the destination
    (fix-loop IMPORTANT-1: archive-not-delete extends to same-day `_retired/`
    collisions — e.g. a slug freed up by an earlier archive gets reused by an
    unrelated new record, or a same-day re-run re-orphans into the same dir). If
    `dest_dir / filename` already exists, append a `notion_id`-derived suffix,
    and — in the vanishingly unlikely case THAT also collides — a numeric
    counter, until a free name is found. Never overwrites; never returns a path
    that already exists."""
    dest = dest_dir / filename
    if not dest.exists():
        return dest
    stem, suffix = Path(filename).stem, Path(filename).suffix
    tag = (notion_id or "")[-8:] or "dup"
    candidate = dest_dir / f"{stem}-{tag}{suffix}"
    n = 2
    while candidate.exists():
        candidate = dest_dir / f"{stem}-{tag}-{n}{suffix}"
        n += 1
    return candidate


def reap_orphans(silo, mapped_tables, fetched_ids_by_table, state_dir, *,
                  dry_run=False, volume_brake=0.2, date=None) -> ReapReport:
    """Archive on-disk records absent from the fetched set.

    `state_dir` is `state/domains/<silo>` (the directory `import_silo` writes
    into and this function reads/moves within) — an explicit path, not derived
    from `silo` by lookup, same convention as `write_snapshot`'s `snapshot_dir`
    (keeps this hermetic against a tmp fixture tree in tests, and lets Task 4's
    orchestrator — which already resolves `env_root` — pass the real one it has).
    It is appended after the three positionals the plan's Task 3 interface names
    (`silo, mapped_tables, fetched_ids_by_table`) rather than inserted before
    them, so that documented prefix stays intact; `dry_run`/`volume_brake` stay
    keyword-only exactly as specified.

    `mapped_tables` is a list of `source_db` strings (S10 scope, e.g.
    `["insurance", "logs/notes"]`) — the SOLE universe this function will ever
    read or touch. A table directory under `state_dir/tables/` not named in this
    list is never globbed, opened, or moved, no matter what sits in it (FO has
    ~14 on-disk table dirs; only the schema-mapped ones are ever reaped).

    `fetched_ids_by_table` maps each mapped table to the set/list of `notion_id`
    values that fetch RETURNED for it. The CALLER's contract: only include a key
    for a table whose fetch actually succeeded (a genuinely-empty-but-successful
    fetch is a valid empty set, distinct from a missing key). A mapped table
    absent from this dict, or explicitly mapped to `None` (the caller's degraded
    signal for "fetch failed/partial"), skips the reap for the ENTIRE silo — not
    just that table — before any file is even read.

    Orphan-ness is decided SOLELY by reading each record's frontmatter
    `notion_id` (via `state_validate._extract_frontmatter`, the same reader
    `domain_mirror` uses) and checking it against `fetched_ids_by_table[table]`
    — never by filename or slug (A84: a Notion title edit changes the slug/
    filename but not the `notion_id`; such a record is NOT an orphan).

    A record must have a READABLE IDENTITY before it can be judged
    absent-from-fetch: a file with no parseable frontmatter (`ValueError`) is
    skipped, not reaped, not counted eligible; a file with parseable frontmatter
    but a FALSY `notion_id` (missing key, or `""`) is likewise kept, not reaped
    — never classified an orphan — and counted in `skipped_no_id` (fix-loop
    IMPORTANT-2: a falsy id being `not in fetched` would otherwise over-reap it).
    The volume brake's denominator is the ELIGIBLE count (parseable + truthy
    `notion_id`), not every `*.md` file, so records that were never at risk of
    being reaped can't dilute the brake into looking safer than it is.

    Guard order: degraded-skip (silo-wide) -> build the read-only orphan PLAN
    for every mapped table (skip-and-count any record without a readable
    identity) -> volume brake over the eligible count (checked — and can raise —
    even under `dry_run`, since a plan that would nuke a fifth of a table is
    worth surfacing loudly at preview time, not just at execution time) ->
    execute (or, under `dry_run`, just report; `_retired/` is never created in
    that case).

    An orphan is MOVED (never `os.remove`d) to
    `state_dir/_retired/<date>/<source_db>/<filename>` — collision-safe: if that
    path is already occupied (a prior archive, e.g. a same-day re-run or a slug
    reused by an unrelated new record), the move never overwrites it; a fresh
    non-colliding name is chosen instead (`_collision_safe_dest`, fix-loop
    IMPORTANT-1).
    """
    state_dir = Path(state_dir)
    tables_root = state_dir / "tables"
    reap_date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ---- SKIP-ON-DEGRADED: any mapped table missing from (or explicitly None in)
    # fetched_ids_by_table aborts the WHOLE silo's reap before any filesystem read
    # of orphan candidates — never a partial reap on a degraded run. ----
    degraded = [t for t in mapped_tables
                if t not in fetched_ids_by_table or fetched_ids_by_table[t] is None]
    if degraded:
        return ReapReport(
            silo=silo, dry_run=dry_run, skipped=True,
            reason=("degraded/missing fetch for mapped table(s): "
                     f"{', '.join(sorted(degraded))} — skipping entire silo reap (never partial)"),
            date=reap_date, by_table={},
        )

    # ---- Build the per-table orphan PLAN (read-only; no moves yet). Mapped tables
    # only; identity keyed strictly on frontmatter notion_id, never filename. A
    # record without a READABLE identity (malformed YAML, or parseable-but-falsy
    # notion_id) is skip-and-count — never classified an orphan (IMPORTANT-2). ----
    plan = {}   # table -> (total, eligible, skipped_no_id, [(Path, notion_id), ...] orphans)
    for table in mapped_tables:
        table_dir = tables_root / Path(table)
        if not table_dir.is_dir():
            plan[table] = (0, 0, 0, [])
            continue
        fetched = fetched_ids_by_table[table]
        total = 0
        eligible = 0
        skipped_no_id = 0
        orphans = []
        for p in sorted(table_dir.glob("*.md")):
            total += 1
            try:
                fm = _extract_frontmatter(p.read_text(encoding="utf-8"))
            except ValueError:
                continue  # malformed record — not this function's job to fix or reap
            nid = fm.get("notion_id")
            if not nid:
                skipped_no_id += 1  # no readable identity -> kept, never an orphan
                continue
            eligible += 1
            if nid not in fetched:
                orphans.append((p, nid))
        plan[table] = (total, eligible, skipped_no_id, orphans)

    # ---- VOLUME BRAKE — over the ELIGIBLE count, not every *.md file (a record
    # with no readable identity was never at risk of being reaped and must not
    # dilute the fraction). Checked before any move, dry_run or not. ----
    for table, (total, eligible, skipped_no_id, orphans) in plan.items():
        if eligible and (len(orphans) / eligible) > volume_brake:
            raise VolumeBrakeExceeded(
                f"reap_orphans[{silo}/{table}]: {len(orphans)}/{eligible} orphan-eligible "
                f"records ({len(orphans) / eligible:.0%}) would be reaped, exceeding the "
                f"{volume_brake:.0%} volume brake — reaping NOTHING for {silo} "
                "(a mass-removal smells like a bad fetch, not a real deletion)."
            )

    # ---- Execute (or just report, under dry_run). ----
    by_table = {}
    for table, (total, eligible, skipped_no_id, orphans) in plan.items():
        orphan_names = [p.name for p, _nid in orphans]
        moved = []
        if not dry_run:
            dest_dir = state_dir / "_retired" / reap_date / table
            for p, nid in orphans:
                dest_dir.mkdir(parents=True, exist_ok=True)
                # Collision-safe: never overwrite an existing archived file (a prior
                # same-day archive, or a slug reused by an unrelated new record).
                dest = _collision_safe_dest(dest_dir, p.name, nid)
                shutil.move(str(p), str(dest))  # MOVE, never delete — archive-not-delete
                moved.append((str(p), str(dest)))
        by_table[table] = TableReapResult(total=total, eligible=eligible,
                                           skipped_no_id=skipped_no_id,
                                           orphans=orphan_names, moved=moved)

    return ReapReport(silo=silo, dry_run=dry_run, skipped=False, reason="",
                       date=reap_date, by_table=by_table)
