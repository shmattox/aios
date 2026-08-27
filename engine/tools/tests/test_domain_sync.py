import json, os, re, sys, tempfile, textwrap
from pathlib import Path

_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _TOOLS)
import domain_sync as dsync
import domain_mirror as dm
import stable_slugs as ss
import notion_gather as ng

FAIL = []
def check(name, cond):
    if not cond: FAIL.append(name)


# ── fixture silo (synthetic; mirrors test_domain_mirror.py's _scratch_silo). Table
# KEYS reuse stable_slugs's known dispatch keys ("insurance", "notes") so the fake-
# gather rows below can flow through the REAL stable_slugs, not a stub. ──
def _fixture_silo():
    root = Path(tempfile.mkdtemp())
    (root / "profile").mkdir()
    (root / "profile" / "domains.yaml").write_text("brief:\n  trigger: go\n", encoding="utf-8")
    sd = root / "state" / "domains" / "widgetco"
    (sd / "tables").mkdir(parents=True)
    (sd / "schema.yaml").write_text(textwrap.dedent("""\
        state-widget:
          required: [name, type, notion_id]
          notion_source_db: insurance
          notion_fields:
            name: [Name, title]
            active: [Active, checkbox]
        state-note:
          required: [name, type, notion_id]
          notion_source_db: logs/notes
          notion_fields:
            name: [Note, title]
        """), encoding="utf-8")
    return root, sd


# ══════════════════════════════════════════════════════════════════════════
# Step 1/round-trip: a FAKE gather (2 rows total, across a flat + a NESTED source_db)
# flows through write_snapshot -> the REAL import_silo, on a fixture silo.
# ══════════════════════════════════════════════════════════════════════════
_root, _sd = _fixture_silo()
_cfg = dm.load_silo_config(_root, "widgetco")
_tables = {t["source_db"]: t for t in _cfg["tables"]}
_snap = _sd / "_snapshots"

_fake_rows_ins = [
    {"url": "https://app.notion.com/00000000000000000000000000000001", "Name": "Widget A", "Active": "__YES__"},
    {"url": "https://app.notion.com/00000000000000000000000000000002", "Name": "Widget B", "Active": "__NO__"},
]
_u2s_ins = ss.stable_slugs("insurance", _fake_rows_ins, {})
_dest_ins = dsync.write_snapshot("widgetco", _tables["insurance"], _fake_rows_ins, _u2s_ins, _snap,
                                 exported="2026-08-27")
check("write_snapshot_flat_path", _dest_ins == _snap / "insurance-export.json")
check("write_snapshot_flat_file_exists", _dest_ins.is_file())

_fake_rows_notes = [
    {"url": "https://app.notion.com/00000000000000000000000000000003", "Note": "First note"},
]
_u2s_notes = ss.stable_slugs("logs/notes", _fake_rows_notes, {})
_dest_notes = dsync.write_snapshot("widgetco", _tables["logs/notes"], _fake_rows_notes, _u2s_notes, _snap,
                                   exported="2026-08-27")
check("write_snapshot_nested_path", _dest_notes == _snap / "logs" / "notes-export.json")
check("write_snapshot_nested_file_exists", _dest_notes.is_file())
check("write_snapshot_nested_parent_created", (_snap / "logs").is_dir())

# _meta shape — the exact contract import_silo reads
_doc_ins = json.loads(_dest_ins.read_text(encoding="utf-8"))
check("meta_has_exported", _doc_ins["_meta"]["exported"] == "2026-08-27")
check("meta_row_count", _doc_ins["_meta"]["row_count"] == 2)
check("meta_silo", _doc_ins["_meta"]["silo"] == "widgetco")
check("meta_source_db", _doc_ins["_meta"]["source_db"] == "insurance")
check("rows_passthrough_verbatim", _doc_ins["rows"] == _fake_rows_ins)
check("url_to_slug_matches_stable_slugs_output", _doc_ins["url_to_slug"] == _u2s_ins)

# exported defaults to today (UTC, YYYY-MM-DD) when omitted
_dest_default = dsync.write_snapshot("widgetco", _tables["insurance"], [], {}, Path(tempfile.mkdtemp()))
_doc_default = json.loads(_dest_default.read_text(encoding="utf-8"))
check("meta_exported_defaults_to_date_shape", bool(re.match(r"^\d{4}-\d{2}-\d{2}$", _doc_default["_meta"]["exported"])))

# round-trip through the REAL engine: import_silo reads exactly what write_snapshot wrote,
# for BOTH the flat and the nested source_db, from one call.
_written = dm.import_silo(_root, "widgetco", _snap)
check("roundtrip_wrote_flat_and_nested",
      (_sd / "tables" / "insurance" / "widget-a.md").is_file()
      and (_sd / "tables" / "insurance" / "widget-b.md").is_file()
      and (_sd / "tables" / "logs" / "notes" / "first-note.md").is_file())
_wa = (_sd / "tables" / "insurance" / "widget-a.md").read_text(encoding="utf-8")
check("roundtrip_field_value", "active: true" in _wa)
# a 32-char all-digit notion_id LOOKS numeric to the emitter's quoting heuristic, so it is
# quoted on emit (`_emit_scalar`/`_looks_number`) — assert the value it decodes back to,
# not a specific literal quoting, so this does not overfit the emitter's formatting choice.
check("roundtrip_notion_id", dm._extract_frontmatter(_wa)["notion_id"] == "00000000000000000000000000000001")
_note = (_sd / "tables" / "logs" / "notes" / "first-note.md").read_text(encoding="utf-8")
check("roundtrip_nested_type", "type: state-note" in _note)
check("roundtrip_nested_notion_id",
      dm._extract_frontmatter(_note)["notion_id"] == "00000000000000000000000000000003")


# ══════════════════════════════════════════════════════════════════════════
# _row_value — pure unit tests for the Notion-property -> raw-row-value normalizer.
# ══════════════════════════════════════════════════════════════════════════
check("row_value_title", dsync._row_value(
    "Name", {"type": "title", "title": [{"plain_text": "Foo"}, {"plain_text": " Bar"}]})
    == [("Name", "Foo Bar")])
check("row_value_title_empty", dsync._row_value("Name", {"type": "title", "title": []}) == [("Name", None)])
check("row_value_checkbox_yes", dsync._row_value("Active", {"type": "checkbox", "checkbox": True})
      == [("Active", "__YES__")])
check("row_value_checkbox_no", dsync._row_value("Active", {"type": "checkbox", "checkbox": False})
      == [("Active", "__NO__")])
check("row_value_select", dsync._row_value("Status", {"type": "select", "select": {"name": "Active"}})
      == [("Status", "Active")])
check("row_value_select_none", dsync._row_value("Status", {"type": "select", "select": None}) == [("Status", None)])
check("row_value_multi_select", dsync._row_value(
    "Tags", {"type": "multi_select", "multi_select": [{"name": "a"}, {"name": "b"}]})
    == [("Tags", ["a", "b"])])
check("row_value_multi_select_empty", dsync._row_value(
    "Tags", {"type": "multi_select", "multi_select": []}) == [("Tags", None)])
check("row_value_number", dsync._row_value("Qty", {"type": "number", "number": 5}) == [("Qty", 5)])
check("row_value_date_full", dict(dsync._row_value(
    "Due", {"type": "date", "date": {"start": "2026-01-01", "end": "2026-01-02"}}))
    == {"date:Due:start": "2026-01-01", "date:Due:end": "2026-01-02", "date:Due:is_datetime": 0})
check("row_value_date_none", dict(dsync._row_value("Due", {"type": "date", "date": None}))
      == {"date:Due:start": None, "date:Due:end": None, "date:Due:is_datetime": None})
check("row_value_date_is_datetime_flag", dsync._row_value(
    "Due", {"type": "date", "date": {"start": "2026-01-01T10:00:00.000-05:00", "end": None}})[0]
    == ("date:Due:start", "2026-01-01T10:00:00.000-05:00"))
check("row_value_relation", dsync._row_value(
    "Owner", {"type": "relation", "relation": [{"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}]})
    == [("Owner", ["https://www.notion.so/aaaaaaaabbbbccccddddeeeeeeeeeeee"])])
check("row_value_relation_empty", dsync._row_value("Owner", {"type": "relation", "relation": []})
      == [("Owner", None)])
check("row_value_unhandled_type", dsync._row_value("Files", {"type": "files", "files": [{"name": "x"}]})
      == [("Files", None)])

_page = {"url": "https://n/x", "properties": {
    "Name": {"type": "title", "title": [{"plain_text": "Foo"}]},
    "Active": {"type": "checkbox", "checkbox": True},
}}
check("row_from_page", dsync._row_from_page(_page) == {"url": "https://n/x", "Name": "Foo", "Active": "__YES__"})


# ══════════════════════════════════════════════════════════════════════════
# gather_table — the live-fetch SEAM, exercised via a monkeypatched notion_gather
# (never live Notion; hermetic per the plan's Global Constraints). Proves gather_table
# wires notion_gather's token/query primitives to _row_from_page correctly.
# ══════════════════════════════════════════════════════════════════════════
_orig_query_source = ng._query_source
_orig_resolve_token = ng.resolve_token


def _fake_query_source_ok(db_id, token, page_size):
    return "databases", [{
        "url": "https://app.notion.com/00000000000000000000000000000009",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Gathered Widget"}]},
            "Active": {"type": "checkbox", "checkbox": True},
            "Tags": {"type": "multi_select", "multi_select": [{"name": "alpha"}, {"name": "beta"}]},
            "Due": {"type": "date", "date": {"start": "2026-08-27", "end": None}},
        },
    }], None


ng._query_source = _fake_query_source_ok
ng.resolve_token = lambda token_env=None: ("fake-token", "env")
try:
    _gathered = dsync.gather_table("widgetco", {"name": "insurance", "source_db": "insurance",
                                                "notion_db": "00000000000000000000000000000009"})
finally:
    ng._query_source = _orig_query_source
    ng.resolve_token = _orig_resolve_token

check("gather_table_row_count", len(_gathered) == 1)
_g0 = _gathered[0]
check("gather_table_url", _g0["url"] == "https://app.notion.com/00000000000000000000000000000009")
check("gather_table_title", _g0["Name"] == "Gathered Widget")
check("gather_table_checkbox", _g0["Active"] == "__YES__")
check("gather_table_multi_select", _g0["Tags"] == ["alpha", "beta"])
check("gather_table_date_fanout", _g0["date:Due:start"] == "2026-08-27" and _g0["date:Due:end"] is None)

# a query failure raises RuntimeError naming the table — never a silent empty result
# (an empty table would be indistinguishable from "everything vanished" to the reap's
# volume brake, Task 3).
def _fake_query_source_err(db_id, token, page_size):
    return None, [], "HTTP 500: boom"


ng._query_source = _fake_query_source_err
ng.resolve_token = lambda token_env=None: ("fake-token", "env")
try:
    try:
        dsync.gather_table("widgetco", {"name": "insurance",
                                        "notion_db": "00000000000000000000000000000009"})
        check("gather_table_error_raises", False)
    except RuntimeError as _e:
        check("gather_table_error_raises", "insurance" in str(_e))
finally:
    ng._query_source = _orig_query_source
    ng.resolve_token = _orig_resolve_token


# ══════════════════════════════════════════════════════════════════════════
# Step 5 — round-trip correctness gate: FROZEN FamilyOffice export rows -> stable_slugs
# -> write_snapshot -> the REAL import_silo -> value-identical to the GOLDEN records.
# REQUIRED: a nested-source_db table (change-log AND decision-log, both `logs/*`).
#
# This is Task 1's slug proof and test_domain_mirror.py's A72 field-mapping proof
# chained one level further out: neither of those constructs the snapshot JSON via
# write_snapshot — they hand-build the dict. This proves the GENERATED snapshot (this
# task's actual deliverable) drives the unmodified engine to the same records the
# hand-made exports did, end to end.
#
# Hermetic: both sides frozen (the dated FO exports + the `golden/` records extracted
# from a frozen commit — same provenance as test_stable_slugs.py Step 5 / A72). Guard:
# the family-office clone's absence (e.g. a laptop without it) skips this block; every
# proof above (fixture round-trip, _row_value units, gather_table wiring) still stands.
# ══════════════════════════════════════════════════════════════════════════
import glob as _glob
_FO_MIG = Path(_TOOLS).resolve().parents[2] / "family-office" / "state-mirror" / "migration"
_GOLDEN = _FO_MIG / "golden"
_ENV_ROOT = Path(_TOOLS).resolve().parents[2].parent
_FO_SCHEMA_DIR = _ENV_ROOT / "state" / "domains" / "familyoffice"

if _GOLDEN.is_dir() and (_FO_SCHEMA_DIR / "schema.yaml").is_file():
    _cfg_fo = dm.load_silo_config(_ENV_ROOT, "familyoffice")
    _snap5 = Path(tempfile.mkdtemp())
    _covered5, _skipped5 = [], []
    for _t in _cfg_fo["tables"]:
        _db = _t["source_db"]
        _prefix = Path(_db).name                      # export filenames are always flat
        _matches = _glob.glob(str(_FO_MIG / f"{_prefix}-notion-export-*.json"))
        _gdir = _GOLDEN / _db
        if not _matches or not _gdir.is_dir():
            _skipped5.append(_db)
            continue
        _date5 = Path(_matches[0]).name.split("-notion-export-")[1].rsplit(".json", 1)[0]
        _export5 = json.loads(Path(_matches[0]).read_text(encoding="utf-8"))
        _rows5 = _export5["rows"]
        # "gather" is FAKED here (Task 2's hermetic contract, Task 4 proves the live path):
        # the frozen export's rows stand in for a live fetch's result. stable_slugs is real
        # (Task 1 already proved it reproduces this exact golden slug map).
        _u2s5 = ss.stable_slugs(_db, _rows5, {})
        dsync.write_snapshot("familyoffice", _t, _rows5, _u2s5, _snap5, exported=_date5)
        _covered5.append(_db)

    check("step5_nested_change_log_covered", "logs/change-log" in _covered5)
    check("step5_nested_decision_log_covered", "logs/decision-log" in _covered5)

    # Seed out_dir from golden so state_native (A80: owner_entity/wiki) carry-forward has a
    # real record to read — same precedent as test_domain_mirror.py's A72 gate; without this
    # every state-native field would show as an "unexpected golden-only field" false alarm.
    _out5 = Path(tempfile.mkdtemp()) / "tables"
    for _db in _covered5:
        _dst = _out5 / _db
        _dst.mkdir(parents=True, exist_ok=True)
        for _p in (_GOLDEN / _db).glob("*.md"):
            (_dst / _p.name).write_text(_p.read_text(encoding="utf-8"), encoding="utf-8")

    _written5 = dm.import_silo(_ENV_ROOT, "familyoffice", _snap5, _out5)

    _CURATED5 = {"owner_entity", "asset"}   # state-native / Plan-2b-reproduced-elsewhere fields
                                            # (same exclusion as test_domain_mirror.py's A72 gate)

    def _nv5(v):
        return v[0] if isinstance(v, list) and len(v) == 1 else v

    def _norm5(fm):
        return {k: _nv5(v) for k, v in fm.items()}

    _mism5, _extra5, _by_table5 = [], [], {}
    for _gen in _written5:
        _tbl = _gen.parent.relative_to(_out5).as_posix()
        _gold = _GOLDEN / _tbl / _gen.name
        _c = _by_table5.setdefault(_tbl, [0, 0])
        _c[0] += 1
        if not _gold.is_file():
            _mism5.append((_tbl, _gen.name, "no golden file"))
            continue
        _g = _norm5(dm._extract_frontmatter(_gen.read_text(encoding="utf-8")))
        _s = _norm5(dm._extract_frontmatter(_gold.read_text(encoding="utf-8")))
        _unexpected = (set(_s) - set(_g)) - _CURATED5
        if _unexpected:
            _extra5.append((_tbl, _gen.name, sorted(_unexpected)))
        _diffs = {k: (_g.get(k), _s.get(k)) for k in _g if _g.get(k) != _s.get(k)}
        if _diffs:
            _mism5.append((_tbl, _gen.name, list(_diffs)[:4]))
        else:
            _c[1] += 1

    check("step5_no_value_mismatches", not _mism5)
    check("step5_no_unexpected_golden_only_fields", not _extra5)
    _nested5 = ("logs/change-log", "logs/decision-log")
    _nested_ok5 = all(_by_table5.get(_tbl, [0, 0])[0] > 0
                      and _by_table5[_tbl][0] == _by_table5[_tbl][1] for _tbl in _nested5)
    check("step5_nested_tables_fully_value_identical", _nested_ok5)

    for _tbl in sorted(_by_table5):
        _n5, _ok5 = _by_table5[_tbl]
        print(f"Step5 write_snapshot->import_silo round-trip [{_tbl}]: {_ok5}/{_n5} value-identical")
    if _skipped5:
        print("Step5 tables skipped (no frozen export or golden dir):", _skipped5)
    if _mism5:
        print("Step5 mismatches (first 6):", _mism5[:6])
    if _extra5:
        print("Step5 unexpected golden-only fields (first 6):", _extra5[:6])
else:
    print("FO golden absent — skipping Step 5 round-trip proof (fixture + unit proofs above stand)")


# ---- harness footer (exactly once, at end of file) ----
print("FAILURES:", FAIL)
sys.exit(1 if FAIL else 0)
