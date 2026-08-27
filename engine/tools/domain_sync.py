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
import domain_mirror as dm  # noqa: E402  (reuse — load_silo_config/import_silo/notion_id_from_url/find_env_root)
from stable_slugs import load_slugmap, save_slugmap, stable_slugs  # noqa: E402  (Task 1, reused verbatim)
from state_validate import _extract_frontmatter, validate_file  # noqa: E402  (reuse — same reader domain_mirror uses)


# ─────────────────────────────────────────────────────────────────────────────
# seed_slugmap — Task 1: bootstrap `_slugmap.json` from the EXISTING on-disk
# mirror, so a silo's FIRST sync re-uses every record's current slug (A84
# first-seen-wins) instead of re-slugging from scratch. Reuses the same
# frontmatter reader reap_orphans/domain_mirror already use
# (state_validate._extract_frontmatter) — no new parser.
# ─────────────────────────────────────────────────────────────────────────────

def _frontmatter_notion_id(path):
    """One record's frontmatter `notion_id`, or `None` if the file's frontmatter
    is unparseable or the key is missing/empty. Never guesses — a record with
    no readable identity is simply skipped by the caller."""
    try:
        fm = _extract_frontmatter(Path(path).read_text(encoding="utf-8"))
    except ValueError:
        return None
    return fm.get("notion_id") or None


def seed_slugmap(state_dir, *, dry_run=False) -> dict:
    """Bootstrap {notion_id: slug} from the EXISTING on-disk mirror so a first
    sync re-uses every record's current slug (A84 first-seen-wins) instead of
    re-slugging from scratch. slug = filename stem; notion_id = frontmatter
    `notion_id`. A record with no notion_id is skipped (can't key it) — never
    guessed. Idempotent; deterministic key order (save_slugmap).

    NON-DESTRUCTIVE: seeds into (never replaces) any existing `_slugmap.json` —
    an entry already present WINS (A84 first-seen-wins), so re-seeding never
    re-slugs a record on disk-title drift, and an id this `.md`-only scan cannot
    see (e.g. an FO `.ndjson`-row id already in the map) is preserved, not
    clobbered. Only `*.md` records are read (Personal is all flat `*.md`, no
    nested `.ndjson`; a silo with an `.ndjson` table would need its rows seeded
    separately — those rows' existing map entries are left intact here)."""
    state_dir = Path(state_dir)
    tables_root = state_dir / "tables"
    out = dict(load_slugmap(state_dir / "_slugmap.json"))  # merge, don't overwrite
    skipped = 0
    for md in sorted(tables_root.rglob("*.md")):
        nid = _frontmatter_notion_id(md)   # reuses domain_mirror's reader (state_validate._extract_frontmatter)
        if not nid:
            skipped += 1
            continue
        out.setdefault(nid, md.stem)       # existing slugmap entry wins (A84); never re-slug on drift
    if not dry_run:
        save_slugmap(state_dir / "_slugmap.json", out)
    return out


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


# ─────────────────────────────────────────────────────────────────────────────
# sync_silo — Task 4: the orchestration. Walks a silo's mapped tables end to
# end: gather -> stable_slugs -> write_snapshot -> import_silo -> reap_orphans
# -> state_validate -> sync-status.json. Every piece it calls is REUSED
# (Tasks 1-3 + the existing engine); this function is only the seam.
#
# Paper-Governs: sync_silo NEVER commits (that stays the deploy-task/Seth
# step — the first FO commit is Seth-gated, plan §Paper-Governs gate 2). GM is
# EXCLUDED entirely (S9 — local-SSOT, publish-out): calling this on "gm"
# touches nothing, not even its schema.yaml.
# ─────────────────────────────────────────────────────────────────────────────

_GM_EXCLUDED_REASON = ("GM excluded from domain-sync — local-SSOT with publish-OUT (S9); "
                        "applying the sync would destroy its factory-authored records. "
                        "Never gathered, never touched.")


@dataclass
class TableSyncResult:
    """One mapped table's gather/import outcome for this run.

    `gather_error` is `""` on a clean fetch; non-empty marks this table (and,
    via `SyncReport.degraded`, the whole silo) degraded for this run — the
    caller-contract `reap_orphans` already expects (Task 3: a mapped table
    absent from `fetched_ids_by_table`, or mapped to `None`, skips the ENTIRE
    silo's reap — never partial)."""
    source_db: str
    row_count: int    # rows gather returned (0 on a degraded table)
    imported: int      # records import_silo actually wrote for this table this run
    gather_error: str   # "" clean; else the exception text, verbatim


@dataclass
class SyncReport:
    """Full-silo sync outcome. `skipped=True` means the GM exclusion fired —
    every other field is a placeholder/empty and nothing was read or
    written. `reap` is `None` under `dry_run` (reap never runs) or when the
    caller passed `no_reap=True`; `validate_pass`/`validate_errors` are
    `None`/`[]` under `dry_run` (nothing was written to validate) and
    populated otherwise, degraded or not (state_validate always runs after an
    import, per the plan). `status_path` is `None` whenever nothing was
    written (`dry_run`, or the GM skip). `failed=True` (fix-loop IMPORTANT-1)
    means `dm.import_silo` itself raised — DISTINCT from `degraded` (which
    means "some table's gather couldn't reach Notion," not "the write broke
    mid-loop"): `import_silo` has no rollback, so any exception it raises
    happened after SOME records were already written to disk — a partial
    write. `reap` is always `None` in that case (reap must never run against
    a partial import) and `failure_reason` carries the exception text."""
    silo: str
    dry_run: bool
    skipped: bool
    reason: str
    degraded: bool
    degraded_tables: list
    tables: dict          # source_db -> TableSyncResult
    reap: object            # ReapReport | None
    validate_pass: object    # bool | None
    validate_errors: list
    status_path: object       # Path | None
    failed: bool = False       # dm.import_silo raised (fix-loop IMPORTANT-1) — a partial write
    failure_reason: str = ""    # the exception text, verbatim; "" unless failed


def _validate_silo(state_dir, schema):
    """Validate every mapped record on disk against the silo's schema —
    `state_validate`'s own `--all` convention (skip `README.md` + any
    `_views/` path component, and validate BOTH `*.md` records AND
    `*.ndjson` tables — `main()` above, `state_validate.py`'s own `--all`
    branch globs both), reused here rather than shelled out to, so this
    function can return the pass/fail + error list `sync_silo` needs for the
    status sidecar instead of stdout text. Fix-loop IMPORTANT-2: the `.md`-
    only glob silently excluded `.ndjson` tables (e.g. FO's `tables/budget/`,
    H83's finance-feed) while this function's own docstring claimed to
    reproduce `--all` — `sync-status.json`'s `validate.pass` could read
    `true` with a broken ndjson table sitting right next to it. A single
    malformed record's validation error is caught and reported like any
    other schema violation — never allowed to abort the batch."""
    tables_dir = Path(state_dir) / "tables"
    if not tables_dir.is_dir():
        return True, []
    targets = [p for p in sorted(tables_dir.rglob("*.md"))
               if p.name != "README.md" and "_views" not in p.parts]
    targets += [p for p in sorted(tables_dir.rglob("*.ndjson")) if "_views" not in p.parts]
    errors = []
    for p in targets:
        try:
            errs = validate_file(p, schema)
        except Exception as exc:  # noqa: BLE001 - one malformed record must never abort validation
            errs = [f"could not validate ({type(exc).__name__}: {exc})"]
        errors.extend(f"{p}: {e}" for e in errs)
    return (not errors), errors


# ── sync-status.json — reuses A60's exact sweep-status.json PATTERN (atomic
# tmp+os.replace write; last_attempt_utc/last_good_utc/consecutive_degraded
# persisted run-over-run so a permanently-degraded silo can't hide behind a
# fresh-looking last_attempt_utc). Source: engine/tools/resolve_sweep_task.py
# STATUS_FILE/_read_status/_write_status + its run()'s degraded-vs-good
# branches (git show 7f2a720^:engine/tools/resolve_sweep_task.py — the whole
# resolve/dossier surface was later retired wholesale as moat-free, A91, but
# THIS sidecar pattern is what H51 says to reuse rather than invent a sixth
# observability surface). The literal field NAMES that were specific to the
# resolve sweep's own content (candidates_fingerprint/candidates_unchanged_
# days/last_source) have no sync analog and are not reproduced; the freshness/
# degraded-streak fields + the atomic-write mechanics are reused verbatim. ──
SYNC_STATUS_FILE = "sync-status.json"


def _utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_sync_status(state_dir):
    try:
        with open(Path(state_dir) / SYNC_STATUS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_status_file(state_dir, status):
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    tmp = state_dir / (SYNC_STATUS_FILE + ".tmp")
    tmp.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
    dest = state_dir / SYNC_STATUS_FILE
    tmp.replace(dest)                        # atomic — a crash mid-write can't corrupt the sidecar
    return dest


def _write_sync_status(state_dir, silo, *, degraded, degraded_tables, tables, reap, no_reap,
                        validate_pass, validate_errors, failed=False, failure_reason="") -> Path:
    prior = _read_sync_status(state_dir)
    now = _utcnow()
    # `failed` (fix-loop IMPORTANT-1) is a WORSE outcome than `degraded` — a mid-loop
    # import_silo exception, not just an unreachable source — so it must count toward the
    # streak too, and must NOT be reported as a good run (last_good_utc must not advance).
    is_bad = degraded or failed
    consecutive_degraded = (int(prior.get("consecutive_degraded") or 0) + 1) if is_bad else 0
    if failed:
        # Louder+distinct from the ordinary reasons below: the reap wasn't skipped because a
        # source was unreachable, it was skipped because the WRITE itself broke mid-loop and
        # reaping against a partial import would compare against a half-written tree.
        reap_skipped = True
        reap_skip_reason = f"import_silo failed — never reap against a partial import: {failure_reason}"
    elif reap is None:
        reap_skipped = True
        reap_skip_reason = "no_reap flag" if no_reap else ""
    elif reap.skipped:
        reap_skipped = True
        reap_skip_reason = reap.reason
    else:
        reap_skipped = False
        reap_skip_reason = ""
    reap_by_table = reap.by_table if (reap is not None and not reap.skipped) else {}
    tables_status = {}
    for db, tr in tables.items():
        rr = reap_by_table.get(db)
        tables_status[db] = {
            "row_count": tr.row_count,
            "imported": tr.imported,
            "gather_error": tr.gather_error,
            "reaped": len(rr.moved) if rr else 0,
            "skipped_no_id": rr.skipped_no_id if rr else 0,
            # "malformed-skipped" (brief): files reap considered but could not read an identity
            # from at all (unparseable frontmatter) — total minus the two readable buckets.
            "malformed_skipped": (rr.total - rr.eligible - rr.skipped_no_id) if rr else 0,
        }
    status = {
        "last_attempt_utc": now,
        "last_good_utc": (prior.get("last_good_utc") if is_bad else now),
        "consecutive_degraded": consecutive_degraded,
        "silo": silo,
        # tri-state, worst-first: "failed" (the write itself broke, partial) outranks
        # "degraded" (a source was unreachable, nothing written for that table) outranks
        # "written" (clean). Never conflate failed with degraded — they need different responses.
        "status": "failed" if failed else ("degraded" if degraded else "written"),
        "failure_reason": failure_reason,
        "degraded_tables": sorted(degraded_tables),
        "reap_skipped": reap_skipped,
        "reap_skip_reason": reap_skip_reason,
        "validate": {"pass": validate_pass, "error_count": len(validate_errors),
                     "errors": validate_errors[:20]},
        "tables": tables_status,
    }
    return _write_status_file(state_dir, status)


def _title_field_of(table_cfg):
    """The Notion title property for one `load_silo_config` table entry (Task 2,
    S14/A84 Plan-4): the `notion_fields` entry whose spec is `[<Prop>, "title"]`,
    already carried on `table_cfg["fields"]` as `(field, kind, link_tmpl, prop,
    rel_source)`. `None` when a table declares no title field (falls back to
    `stable_slugs`'s FO `_GENERIC_RULES` dict, or raises if that table has no
    rule either) — never a live Notion lookup, fact-free (schema-derived only).
    """
    for _field, kind, _link_tmpl, prop, _rel_source in table_cfg["fields"]:
        if kind == "title":
            return prop
    return None


def sync_silo(env_root, silo, *, dry_run=False, no_reap=False, volume_brake=0.2,
              _gather=None) -> SyncReport:
    """The orchestration: gather -> stable_slugs -> write_snapshot -> import_silo
    -> reap_orphans -> state_validate -> sync-status.json.

    `_gather` is the hermetic test seam ("monkeypatch or inject gather_table",
    the plan's Task 4 Step 1) — a callable `(silo, table_cfg) -> rows`
    standing in for the live `gather_table`. Defaults to the real
    `gather_table`, so an unmodified CLI call gathers for real; every OTHER
    step (stable_slugs/write_snapshot/import_silo/reap_orphans/
    state_validate) is always the REAL engine call — hermetic tests are
    hermetic only because `_gather` never reaches Notion.

    GM is EXCLUDED entirely (S9): `silo == "gm"` returns a `skipped` report
    immediately, before even reading its schema.yaml — never gathered, never
    imported, never reaped.

    `dry_run=True` short-circuits BEFORE any write: gathers each table (so
    the report reflects a real plan) and runs `stable_slugs` in memory only,
    but writes NOTHING — no snapshot, no persisted `_slugmap.json`, no
    import, no reap, no status file (plan Task 4 Step 1/2-4 acceptance).

    An `import_silo` exception (fix-loop IMPORTANT-1 — it writes as it
    iterates, no rollback, so a mid-loop failure like a fail-loud `relation`
    coerce on a dangling cross-export url leaves a PARTIAL write across
    tables) is caught here and reported as `failed=True` — distinct from
    `degraded` — with the reap NEVER run (reaping against a half-written
    tree would be comparing identities to a tree in an unknown state).

    A per-table gather failure marks that table (and the whole silo)
    `degraded` rather than raising — `fetched_ids_by_table[db] = None` is
    `reap_orphans`'s own degraded-skip signal (Task 3), so passing it through
    unmodified makes reap skip the ENTIRE silo whenever any mapped table
    failed to gather, exactly as Task 3 already guards. A degraded table with
    no PRIOR snapshot on disk (a first-ever sync) would otherwise make
    `import_silo` raise `FileNotFoundError` for the whole silo (it always
    expects one export per mapped table, reused unmodified here) — so a
    degraded table without a stale snapshot to fall back on gets an empty
    (0-row) stub written instead, letting its unaffected siblings still
    import normally (spec "Hard part 3": per-table all-or-nothing, never a
    partial write) while it contributes zero new/changed records itself.
    """
    if silo == "gm":
        return SyncReport(silo=silo, dry_run=dry_run, skipped=True, reason=_GM_EXCLUDED_REASON,
                           degraded=False, degraded_tables=[], tables={}, reap=None,
                           validate_pass=None, validate_errors=[], status_path=None)

    gather_fn = _gather or gather_table
    env_root = Path(env_root)
    cfg = dm.load_silo_config(env_root, silo)
    state_dir = cfg["state_dir"]
    snapshot_dir = state_dir / "_snapshots"     # Personal's already-working flat pattern (no date subdir)
    slugmap_path = state_dir / "_slugmap.json"
    slugmap = load_slugmap(slugmap_path)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    mapped_tables = [t["source_db"] for t in cfg["tables"]]

    tables_result = {}
    fetched_ids_by_table = {}
    degraded_tables = []

    for table in cfg["tables"]:
        db = table["source_db"]
        try:
            rows = gather_fn(silo, table)
        except Exception as exc:  # noqa: BLE001 - a gather failure degrades THIS table, never crashes the silo
            degraded_tables.append(db)
            fetched_ids_by_table[db] = None      # reap_orphans' own degraded-skip signal (Task 3)
            tables_result[db] = TableSyncResult(source_db=db, row_count=0, imported=0,
                                                 gather_error=str(exc))
            continue
        u2s = stable_slugs(db, rows, slugmap,    # mutates slugmap in place (first-seen-wins, A84)
                           title_field=_title_field_of(table))
        fetched_ids_by_table[db] = {dm.notion_id_from_url(r["url"]) for r in rows}
        tables_result[db] = TableSyncResult(source_db=db, row_count=len(rows), imported=0, gather_error="")
        if not dry_run:
            write_snapshot(silo, table, rows, u2s, snapshot_dir, exported=today)

    degraded = bool(degraded_tables)

    if dry_run:
        return SyncReport(silo=silo, dry_run=True, skipped=False, reason="",
                           degraded=degraded, degraded_tables=degraded_tables, tables=tables_result,
                           reap=None, validate_pass=None, validate_errors=[], status_path=None)

    save_slugmap(slugmap_path, slugmap)

    for db in degraded_tables:
        stub_path = snapshot_dir / f"{db}-export.json"
        if not stub_path.is_file():
            table = next(t for t in cfg["tables"] if t["source_db"] == db)
            write_snapshot(silo, table, [], {}, snapshot_dir, exported=today)

    # Fix-loop IMPORTANT-1: `import_silo` writes records to disk AS IT ITERATES its tables —
    # no transaction, no rollback. A plain `relation` coerce is fail-loud (`_link` KeyError,
    # domain_mirror.py) on an url not in that field's slug map; a future mapped field using
    # it (today's one live cross-export field, FO's `asset`, uses the TOLERANT
    # `json_relation` instead) or any other mid-loop exception would leave SOME tables'
    # records written and others not — a partial write across tables (violates the Global
    # Constraint "never partial", and these are economic records for FO). Never reap
    # against that: catch here, mark the silo FAILED (louder + distinct from `degraded` —
    # this is a write-time break, not an unreachable source) and stop before reap ever runs.
    try:
        written = dm.import_silo(env_root, silo, snapshot_dir, last_synced=today)
    except Exception as exc:  # noqa: BLE001 - deliberately broad: ANY import_silo failure means a
        # possible partial write, and the response (fail loud, skip reap, never swallow) is the
        # same regardless of which exception type raised it.
        validate_pass, validate_errors = _validate_silo(state_dir, cfg["schema"])
        status_path = _write_sync_status(
            state_dir, silo, degraded=degraded, degraded_tables=degraded_tables, tables=tables_result,
            reap=None, no_reap=no_reap, validate_pass=validate_pass, validate_errors=validate_errors,
            failed=True, failure_reason=str(exc),
        )
        return SyncReport(silo=silo, dry_run=False, skipped=False, reason="",
                           degraded=degraded, degraded_tables=degraded_tables, tables=tables_result,
                           reap=None, validate_pass=validate_pass, validate_errors=validate_errors,
                           status_path=status_path, failed=True, failure_reason=str(exc))

    tables_root = state_dir / "tables"
    for p in written:
        db_key = p.parent.relative_to(tables_root).as_posix()
        if db_key in tables_result:
            tables_result[db_key].imported += 1

    reap_report = None
    if not no_reap:
        reap_report = reap_orphans(silo, mapped_tables, fetched_ids_by_table, state_dir,
                                    dry_run=False, volume_brake=volume_brake, date=today)

    validate_pass, validate_errors = _validate_silo(state_dir, cfg["schema"])

    status_path = _write_sync_status(
        state_dir, silo, degraded=degraded, degraded_tables=degraded_tables, tables=tables_result,
        reap=reap_report, no_reap=no_reap, validate_pass=validate_pass, validate_errors=validate_errors,
    )

    return SyncReport(silo=silo, dry_run=False, skipped=False, reason="",
                       degraded=degraded, degraded_tables=degraded_tables, tables=tables_result,
                       reap=reap_report, validate_pass=validate_pass, validate_errors=validate_errors,
                       status_path=status_path)


# ─────────────────────────────────────────────────────────────────────────────
# CLI — `python domain_sync.py --silo <silo> [--dry-run] [--no-reap]`. `--all`
# loops every non-GM mapped silo (any `state/domains/<silo>/schema.yaml`),
# for the nightly scheduled task (`deploy/tasks/domain-sync.md`), which
# invokes this file directly (`type: "script"` in tasks.manifest.json).
# Notion token resolution only happens inside `gather_table`, reached only
# when this actually gathers — never on `--dry-run`... no, `--dry-run` DOES
# gather (Task 4 Step 1: a dry-run still reports a real plan); only a
# process with no configured token never reaches this file at all outside a
# live run, so this CLI path is exercised by the controller's live smoke,
# never by the hermetic test suite (plan brief).
# ─────────────────────────────────────────────────────────────────────────────

def _discover_silos(env_root):
    domains_dir = Path(env_root) / "state" / "domains"
    if not domains_dir.is_dir():
        return []
    return sorted(p.parent.name for p in domains_dir.glob("*/schema.yaml") if p.parent.name != "gm")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="domain_sync.py")
    ap.add_argument("--env-root", help="defaults to the first ancestor with profile/domains.yaml")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--silo", help="run one silo (GM is refused - S9)")
    group.add_argument("--all", action="store_true", help="run every non-GM mapped silo")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-reap", action="store_true")
    ap.add_argument("--seed", action="store_true",
                     help="bootstrap _slugmap.json from the existing on-disk mirror (Task 1) "
                          "instead of syncing; requires --silo")
    args = ap.parse_args(argv)

    env_root = Path(args.env_root) if args.env_root else dm.find_env_root(Path(__file__))

    if args.seed:
        if not args.silo:
            ap.error("--seed requires --silo (not --all)")
        if args.silo == "gm":
            ap.error("GM is refused - S9 (local-SSOT, never a Notion-derived mirror); "
                     "--seed refuses gm the same as sync_silo does")
        cfg = dm.load_silo_config(env_root, args.silo)
        got = seed_slugmap(cfg["state_dir"], dry_run=args.dry_run)
        tag = " (dry-run)" if args.dry_run else ""
        print(f"[{args.silo}] seeded {len(got)} slug(s) from on-disk mirror{tag}")
        return 0

    silos = _discover_silos(env_root) if args.all else [args.silo]

    any_bad = False
    for silo in silos:
        report = sync_silo(env_root, silo, dry_run=args.dry_run, no_reap=args.no_reap)
        if report.skipped:
            print(f"[{silo}] skipped — {report.reason}")
            continue
        if report.failed:
            any_bad = True
            print(f"[{silo}] FAILED — {report.failure_reason} status={report.status_path}")
            continue
        tag = "DEGRADED" if report.degraded else ("dry-run" if report.dry_run else "written")
        any_bad = any_bad or report.degraded
        row_counts = {db: tr.row_count for db, tr in report.tables.items()}
        print(f"[{silo}] {tag} — rows={row_counts} "
              f"reap_skipped={report.reap.skipped if report.reap else (not report.dry_run)} "
              f"validate_pass={report.validate_pass} status={report.status_path}")
    return 1 if any_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
