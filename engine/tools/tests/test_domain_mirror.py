import os, re, sys
from pathlib import Path
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

# per-silo config resolution (profile + schema, fact-free)
import json, tempfile, textwrap
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
check("cfg_fields", any(f[0] == "flag" and f[1] == "checkbox" for f in t["fields"]))
check("cfg_link_tmpl", any(f[0] == "owner" and f[1] == "relation" and f[2] == "things/{slug}" for f in t["fields"]))

def _write_export(path, rows, url_to_slug):
    path.write_text(json.dumps({"_meta": {}, "url_to_slug": url_to_slug, "rows": rows}), encoding="utf-8")

# Extend the demo silo with a second table so we prove >=2 tables from one code path.
# Also cover multi_select, date, and a YAML-ambiguous (": ") string value (Task 1 review gaps).
(_sd / "schema.yaml").write_text(textwrap.dedent("""\
    state-thing:
      required: [name, type, notion_id]
      notion_source_db: things
      notion_fields:
        name: [Name, title]
        flag: ["Is Flag", checkbox]
        owner: [Owner, relation, "people/{slug}"]
        tags: [Tags, multi_select]
        launched: [Launched, date]
        note: [Note, text]
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
              [{"Name": "Widget", "Is Flag": "__YES__", "Owner": "https://n/p1",
                "Tags": ["alpha", "beta"], "Launched": "2026-01-15",
                "Note": "Phase 2: launch", "url": "https://n/t1"}],
              {"https://n/t1": "widget", "https://n/p1": "ada"})

written = dm.import_silo(_root, "demo", snap)
# S8: records land under the SEMANTIC dir (the table's notion_source_db), never the type key.
# FO/GM are semantic on disk already; emitting the type key would create state-note/ beside
# notes/ and silently duplicate every record into a parallel tree.
check("import_wrote_both", (_sd / "tables" / "things" / "widget.md").is_file()
      and (_sd / "tables" / "people" / "ada.md").is_file())
check("import_no_typekey_dirs", not (_sd / "tables" / "state-thing").exists()
      and not (_sd / "tables" / "state-person").exists())
txt = (_sd / "tables" / "things" / "widget.md").read_text(encoding="utf-8")
check("import_type", "type: state-thing" in txt)          # the TYPE key stays in frontmatter
check("import_checkbox", "flag: true" in txt)
check("import_relation", 'owner: "[[people/ada]]"' in txt)
check("import_notion_id", "notion_id: t1" in txt)
check("import_multi_select", "tags: [alpha, beta]" in txt)
check("import_date", "launched: 2026-01-15" in txt)
check("import_quoted_colon", 'note: "Phase 2: launch"' in txt)
# idempotent
before = txt
dm.import_silo(_root, "demo", snap)
check("idempotent", (_sd / "tables" / "things" / "widget.md").read_text(encoding="utf-8") == before)
# fail-loud on dangling relation
_write_export(snap / "things-export.json",
              [{"Name": "Orphan", "Owner": "https://n/missing", "url": "https://n/t2"}],
              {"https://n/t2": "orphan"})
try:
    dm.import_silo(_root, "demo", snap); check("dangling_raises", False)
except KeyError: check("dangling_raises", True)

# fail-loud on a missing snapshot export for a mapped source_db (fresh empty snap dir)
_empty = Path(tempfile.mkdtemp())
_write_export(_empty / "things-export.json", [], {})   # people-export.json deliberately absent
try:
    dm.import_silo(_root, "demo", _empty); check("missing_export_raises", False)
except FileNotFoundError: check("missing_export_raises", True)

# ── Task-4 importer extensions (fact-free, generic): notion_url + last_synced ──
# Extension 1: notion_url is ALWAYS derived from notion_id as https://www.notion.so/<id>
# (NOT the raw row url, which is a different domain + id). Prove it on a fresh silo.
_root2, _sd2 = _scratch_silo()
_snap2 = _sd2 / "_snap"; _snap2.mkdir()
_write_export(_snap2 / "things-export.json",
              [{"Name": "Widget", "url": "https://app.notion.com/abc123def456"}],
              {"https://app.notion.com/abc123def456": "widget"})
dm.import_silo(_root2, "demo", _snap2)
_wtxt = (_sd2 / "tables" / "things" / "widget.md").read_text(encoding="utf-8")
check("ext_notion_url_derived", "notion_url: https://www.notion.so/abc123def456" in _wtxt)
check("ext_notion_url_not_raw", "app.notion.com" not in _wtxt.split("---", 2)[1])
# no last_synced when neither _meta.exported nor CLI value present
check("ext_last_synced_absent_by_default", "last_synced:" not in _wtxt)

# Extension 2: last_synced from the CLI value when _meta carries no export date
_root3, _sd3 = _scratch_silo()
_snap3 = _sd3 / "_snap"; _snap3.mkdir()
_write_export(_snap3 / "things-export.json",
              [{"Name": "Widget", "url": "https://n/w1"}], {"https://n/w1": "widget"})
dm.import_silo(_root3, "demo", _snap3, last_synced="2026-06-30")
_w3 = (_sd3 / "tables" / "things" / "widget.md").read_text(encoding="utf-8")
check("ext_last_synced_from_cli", "last_synced: 2026-06-30" in _w3)

# Extension 2b: _meta.exported OVERRIDES the CLI value (per-snapshot export date wins)
_root4, _sd4 = _scratch_silo()
_snap4 = _sd4 / "_snap"; _snap4.mkdir()
(_snap4 / "things-export.json").write_text(json.dumps(
    {"_meta": {"exported": "2026-07-01"}, "url_to_slug": {"https://n/w1": "widget"},
     "rows": [{"Name": "Widget", "url": "https://n/w1"}]}), encoding="utf-8")
dm.import_silo(_root4, "demo", _snap4, last_synced="2026-06-30")
_w4 = (_sd4 / "tables" / "things" / "widget.md").read_text(encoding="utf-8")
check("ext_last_synced_meta_wins", "last_synced: 2026-07-01" in _w4)

# Emitter fix (generic correctness): a scalar with an embedded newline is escaped to a SINGLE
# physical line (so multi-line free text can't bleed into spurious frontmatter keys), and — now
# that the engine reader decodes double-quoted escapes — it round-trips LOSSLESSLY back to the
# original newline-bearing string (no spurious `line2` key).
_nl_fm = dm.emit_frontmatter({"type": "t", "body": "line1\nline2: bleed"})
_nl_rp = dm._extract_frontmatter(_nl_fm)
check("emit_newline_escaped_single_line", _nl_rp == {"type": "t", "body": "line1\nline2: bleed"})

# Emitter fix: an INTERIOR ` #` is YAML's inline-comment introducer, so an unquoted `foo #123 bar`
# is truncated to `foo` on read-back. Such a value must be quoted to round-trip losslessly.
_h_fm = dm.emit_frontmatter({"type": "t", "item": "event #617203951 (arena)"})
_h_rp = dm._extract_frontmatter(_h_fm)
check("emit_interior_hash_roundtrip", _h_rp == {"type": "t", "item": "event #617203951 (arena)"})

# ══════════════════════════════════════════════════════════════════════════════
# A53 — full-silo migration gaps: cross-export relation, computed field, flow-fold round-trip.
# All three proven on a SYNTHETIC fixture silo (`acme`) with synthetic keys — the engine stays
# fact-free; every specific (region->company map, slug templates) comes from the fixture schema.
# ══════════════════════════════════════════════════════════════════════════════

# ── Capability 3 (round-trip): a multi-line flow-folded note read by the engine reader, emitted
# single-line by the emitter, and re-read must equal the original (read -> emit -> read). ──
_ff_src = ("---\n"
           "type: state-asset\n"
           "notes: 'Workout note, per Ada''s sheet.\n"
           "\n"
           "  RECONCILIATION 2026-05-07:\n"
           "\n"
           "  - balance restated (see item #38).'\n"
           "---\n")
_ff_fm = dm._extract_frontmatter(_ff_src)
check("a53_flowfold_value",
      _ff_fm["notes"] == "Workout note, per Ada's sheet.\nRECONCILIATION 2026-05-07:\n- balance restated (see item #38).")
_ff_rt = dm._extract_frontmatter(dm.emit_frontmatter(_ff_fm))
check("a53_flowfold_roundtrip", _ff_rt == _ff_fm)

# ── Capability 2 (computed field), unit-level: a declarative `lookup` rule maps a source record
# field through a schema-declared table, optionally wrapping the result as a relation wikilink. ──
_lk = {"rule": "lookup", "from": "region", "link": "companies/{slug}",
       "table": {"north": "acme-holdings", "south": "acme-lending"}, "default": "acme-holdings"}
check("a53_compute_hit",     dm.compute_field(_lk, {"region": "south"}) == "[[companies/acme-lending]]")
check("a53_compute_default", dm.compute_field(_lk, {"region": "west"}) == "[[companies/acme-holdings]]")
check("a53_compute_none",    dm.compute_field(_lk, {"region": None}) is None)
check("a53_compute_no_link", dm.compute_field({"rule": "lookup", "from": "region", "table": {"north": "x"}},
                                              {"region": "north"}) == "x")
try:  # unmapped key with NO default -> fail loud (content contract error)
    dm.compute_field({"rule": "lookup", "from": "region", "table": {"north": "x"}}, {"region": "zzz"})
    check("a53_compute_faillOUD", False)
except ValueError:
    check("a53_compute_faillOUD", True)
try:  # unknown rule -> fail loud
    dm.compute_field({"rule": "bogus", "from": "region", "table": {}}, {"region": "north"})
    check("a53_compute_unknown_rule", False)
except ValueError:
    check("a53_compute_unknown_rule", True)

# ── Capabilities 1 + 2, end-to-end on the fixture `acme` silo, validated by the SHARED validator ──
def _a53_silo():
    root = Path(tempfile.mkdtemp())
    (root / "profile").mkdir()
    (root / "profile" / "domains.yaml").write_text("brief:\n  trigger: go\n", encoding="utf-8")
    sd = root / "state" / "domains" / "acme"
    (sd / "tables").mkdir(parents=True)
    (sd / "schema.yaml").write_text(textwrap.dedent('''\
        state-price:
          required: [name, type, notion_id]
          notion_source_db: prices
          notion_fields:
            name: [Name, title]
        state-asset:
          required: [name, type, notion_id]
          relations: [asset, owner_entity]
          notion_source_db: assets
          notion_fields:
            name: [Name, title]
            region: [Region, select]
            asset: [Asset, relation, "prices/{slug}", prices]
          computed_fields:
            owner_entity:
              rule: lookup
              from: region
              link: "companies/{slug}"
              table:
                north: acme-holdings
                south: acme-lending
              default: acme-holdings
        '''), encoding="utf-8")
    return root, sd

_r5, _sd5 = _a53_silo()
_snap5 = _sd5 / "_snap"; _snap5.mkdir()
# prices export: the relation TARGET. Its url_to_slug is what the cross-export relation resolves against.
_write_export(_snap5 / "prices-export.json",
              [{"Name": "USD", "url": "https://n/usd"}], {"https://n/usd": "usd"})
# assets export: its OWN url_to_slug deliberately does NOT contain the price url — so a same-export
# resolution would KeyError; success proves the relation resolved against the prices export.
_write_export(_snap5 / "assets-export.json",
              [{"Name": "John Loan", "Region": "south", "Asset": "https://n/usd", "url": "https://n/a1"},
               {"Name": "West Note", "Region": "west", "Asset": "https://n/usd", "url": "https://n/a2"}],
              {"https://n/a1": "john-loan", "https://n/a2": "west-note"})
dm.import_silo(_r5, "acme", _snap5)
_atxt = (_sd5 / "tables" / "assets" / "john-loan.md").read_text(encoding="utf-8")
check("a53_cross_export_relation", 'asset: "[[prices/usd]]"' in _atxt)          # resolved via prices' slug map
check("a53_computed_owner", 'owner_entity: "[[companies/acme-lending]]"' in _atxt)   # south -> acme-lending
_wtxt = (_sd5 / "tables" / "assets" / "west-note.md").read_text(encoding="utf-8")
check("a53_computed_owner_default", 'owner_entity: "[[companies/acme-holdings]]"' in _wtxt)  # west -> default
# the SHARED validator must pass over the generated tree (acceptance)
import subprocess as _a53_sp
_a53_vr = _a53_sp.run([sys.executable, os.path.join(_TOOLS, "state_validate.py"),
                       "--schema", str(_sd5 / "schema.yaml"), "--all", str(_sd5 / "tables")],
                      capture_output=True, text=True)
check("a53_validator_pass", _a53_vr.returncode == 0)
if _a53_vr.returncode != 0:
    print("A53 validator output:", _a53_vr.stdout[-800:])
# a same-export relation (no rel_source) still resolves against the row's own export (backward compat)
check("a53_same_export_still_works", 'owner: "[[people/ada]]"' in
      (_sd / "tables" / "things" / "widget.md").read_text(encoding="utf-8"))

# ── A53-review finding #1: a cross-export relation naming a NON-mirrored source_db must fail
# PRE-FLIGHT (before any write), like the missing-snapshot check — not mid-write with a partial
# tree. Two tables: a valid one (would be written first) + one whose relation names a bogus
# rel_source. The import must raise AND leave the valid table's dir empty (atomic). ──
def _a53_badrel_silo():
    root = Path(tempfile.mkdtemp())
    (root / "profile").mkdir()
    (root / "profile" / "domains.yaml").write_text("brief:\n  trigger: go\n", encoding="utf-8")
    sd = root / "state" / "domains" / "acme2"
    (sd / "tables").mkdir(parents=True)
    (sd / "schema.yaml").write_text(textwrap.dedent('''\
        state-alpha:
          required: [name, type, notion_id]
          notion_source_db: alpha
          notion_fields:
            name: [Name, title]
        state-beta:
          required: [name, type, notion_id]
          relations: [link]
          notion_source_db: beta
          notion_fields:
            name: [Name, title]
            link: [Link, relation, "nope/{slug}", nomirror]
        '''), encoding="utf-8")
    return root, sd

_r6, _sd6 = _a53_badrel_silo()
_snap6 = _sd6 / "_snap"; _snap6.mkdir()
_write_export(_snap6 / "alpha-export.json", [{"Name": "A1", "url": "https://n/x1"}], {"https://n/x1": "a1"})
_write_export(_snap6 / "beta-export.json",
              [{"Name": "B1", "Link": "https://n/x1", "url": "https://n/y1"}], {"https://n/y1": "b1"})
try:
    dm.import_silo(_r6, "acme2", _snap6)
    check("a53_badrel_preflight_raises", False)
except (KeyError, ValueError) as _e6:
    check("a53_badrel_preflight_raises", "nomirror" in str(_e6))
_alpha_dir = _sd6 / "tables" / "alpha"
check("a53_badrel_no_partial_write",              # the valid first table must NOT have been written
      not _alpha_dir.exists() or not any(_alpha_dir.glob("*.md")))

# ── A72 tripwire: the FO regression must stay HERMETIC — both sides frozen. An edit that
# reattaches the comparison (or the slug-map reconstruction) to the LIVE mirror re-creates the
# exact defect A72 fixed: live operational data changing turns the CODE suite red. This is a
# tripwire on the live-tree handle, not a proof of hermeticity — cheap, and it catches the
# obvious regression. `_FO_SCHEMA_DIR` is the live dir but reaches ONLY schema.yaml (the field
# mapping IS what's under test); the live *records* must never be read.
# The needles are built by concatenation so this guard's own source cannot match them.
_self_src = Path(__file__).read_text(encoding="utf-8")
_live_needle = "_FO_" + "STATE"                # the retired live-record-tree handle
_skip_needle = "_has_" + "multiline_scalar"    # vestigial since A53 taught the reader flow-folding
check("a72_regression_is_hermetic", _live_needle not in _self_src)
check("a72_no_multiline_skip", _skip_needle not in _self_src)

# ══════════════════════════════════════════════════════════════════════════════
# ── A72/A53 REAL regression, HERMETIC: frozen exports -> records == frozen golden ──
# Both sides are frozen: the dated FO exports in the family-office repo's migration dir, and the
# `golden/` records extracted from env commit 81aaf14 (provenance + regeneration: that dir's
# README). This proves the importer's field mapping against REAL Notion data, deterministically,
# forever — the real-data coverage a plain deletion of this block would have discarded (A53).
#
# It deliberately never reads the LIVE state/domains/familyoffice tree. Under the derived-mirror
# contract those records are OUTPUT refreshed from Notion, so asserting them equal to a frozen
# export asserts that operational data never changes — which is what made this test red (A72).
#
# Only guard: the family-office clone's absence (e.g. a laptop without it) — the hermetic fixture
# proofs above still stand there.
import glob as _glob, subprocess
_FO_MIG = Path(_TOOLS).resolve().parents[2] / "family-office" / "state-mirror" / "migration"
_GOLDEN = _FO_MIG / "golden"
_ENV_ROOT = Path(_TOOLS).resolve().parents[2].parent
_FO_SCHEMA_DIR = _ENV_ROOT / "state" / "domains" / "familyoffice"
if _GOLDEN.is_dir() and (_FO_SCHEMA_DIR / "schema.yaml").is_file():
    _load_fm = dm._extract_frontmatter          # the engine's own stdlib reader

    def _nv(v):
        # A length-1 relation list is the migrator's collapsed scalar (semantically identical).
        return v[0] if isinstance(v, list) and len(v) == 1 else v

    def _norm(fm):
        return {k: _nv(v) for k, v in fm.items()}

    _cfg = dm.load_silo_config(_ENV_ROOT, "familyoffice")
    # Normalize the dated exports into the snapshot shape the importer's contract expects:
    #  - <db>-notion-export-<DATE>.json -> <db>-export.json
    #  - inject _meta.exported=<DATE> (drives last_synced; the tax-ledger export carries no _meta)
    #  - reconstruct url_to_slug from the GOLDEN records' notion_id->filename where an older export
    #    predates carrying one; the entities export carries an authentic url_to_slug, used as-is.
    # Reconstructing from GOLDEN (not the live tree) is what makes this hermetic.
    # CAVEAT (unchanged from A53): for the reconstructed tables the slug DERIVATION is not
    # independently proven here (it is taken from the frozen filenames); what IS proven for them is
    # field-mapping equivalence. `entities` exercises a real url_to_slug end-to-end.
    _snap = Path(tempfile.mkdtemp())
    for _t in _cfg["tables"]:
        _db = _t["source_db"]
        # source_db may be a nested path (e.g. "logs/change-log") but the Notion export filename
        # is always flat — glob on the basename, not the full (possibly nested) _db.
        _fs = _glob.glob(str(_FO_MIG / f"{Path(_db).name}-notion-export-*.json"))
        if not _fs:
            continue
        _f = _fs[0]
        _date = Path(_f).name.split("-notion-export-")[1].rsplit(".json", 1)[0]
        _d = json.loads(Path(_f).read_text(encoding="utf-8"))
        _rows = _d["rows"]
        _meta = _d.get("_meta") or {}
        _meta["exported"] = _meta.get("exported") or _date
        _u2s = _d.get("url_to_slug")
        if not _u2s:
            _n2s = {}
            for _p in (_GOLDEN / _db).glob("*.md"):
                _nid = _load_fm(_p.read_text(encoding="utf-8")).get("notion_id")
                if _nid is not None:
                    _n2s[str(_nid)] = _p.stem
            _u2s = {r["url"]: _n2s.get(dm.notion_id_from_url(r["url"]), dm.notion_id_from_url(r["url"]))
                    for r in _rows}
        _snap_target = _snap / f"{_db}-export.json"
        # _db may be nested ("logs/change-log") — make sure the parent dir exists before writing.
        _snap_target.parent.mkdir(parents=True, exist_ok=True)
        _snap_target.write_text(
            json.dumps({"_meta": _meta, "url_to_slug": _u2s, "rows": _rows}), encoding="utf-8")

    # A mapped table with no golden dir is a STALE fixture (a new mapping landed — Plan 2). Fail
    # loud WITH THE FIX, never silently compare nothing — and check this BEFORE import_silo, whose
    # FileNotFoundError on a newly-mapped table with neither an export nor a golden dir would
    # otherwise raise first and bury this check's actionable hint.
    _nogold = sorted(db for db in {t["source_db"] for t in _cfg["tables"]}
                     if not (_GOLDEN / db).is_dir())
    check("fo_golden_covers_mapped_tables", not _nogold)
    if _nogold:
        print("FO golden fixture STALE — mapped tables with no golden dir:", _nogold)
        print("  regenerate: python Projects/family-office/state-mirror/migration/extract_golden.py")

    _out = Path(tempfile.mkdtemp()) / "tables"
    dm.import_silo(_ENV_ROOT, "familyoffice", _snap, _out, last_synced="2026-06-30")

    _REQUIRED = {"entities", "notes"}      # must reach equivalence (scope-narrowed)
    _CURATED = {"wiki", "owner_entity", "asset"}    # golden-only state-native curation, out of scope
    _mism, _bad_extra, _counts = [], [], {}
    for _gen in _out.rglob("*.md"):
        # The table is the record's parent dir relative to _out, as a posix string — this yields
        # "logs/change-log" for a nested source_db and still "tasks" for a flat one. Using parts[0]
        # would truncate a nested source_db to just its first segment ("logs").
        _tbl = _gen.parent.relative_to(_out).as_posix()
        _gold = _GOLDEN / _tbl / _gen.name
        _c = _counts.setdefault(_tbl, [0, 0])        # [n, equal]
        _c[0] += 1
        if not _gold.is_file():
            _mism.append((_tbl, _gen.name, "no golden file")); continue
        _g = _norm(_load_fm(_gen.read_text(encoding="utf-8")))
        _s = _norm(_load_fm(_gold.read_text(encoding="utf-8")))
        _diffs = {k: (_g.get(k), _s.get(k)) for k in _g if _g.get(k) != _s.get(k)}
        _unexpected = (set(_s) - set(_g)) - _CURATED  # a golden field we forgot to map
        if _unexpected:
            _bad_extra.append((_tbl, _gen.name, sorted(_unexpected)))
        if _diffs:
            _mism.append((_tbl, _gen.name, list(_diffs)[:6]))
        else:
            _c[1] += 1
    # ── Body contract (Plan 2). The comparison above is FRONTMATTER-ONLY, so body divergence has
    # always been invisible here. The engine composes a body from the row's `Description` alone
    # (build_record); the retired migrators additionally rendered a `# {Title}` heading + a
    # boilerplate line. That heading is DERIVED and unread (state/domains is not in the vault), so
    # it is deliberately not reproduced — but assert it, so a real body change can never hide.
    # Excluded: records carrying generated DataviewJS view blocks, which a SEPARATE generator
    # (state-mirror/views/render_entities_views.py) appends after import. They are regenerable and
    # not Notion-derived. Plan 3 must re-run that generator after a sync or it strips them.
    _VIEW_MARK = "generated relational views"
    _BOILER = re.compile(r"^State-engine mirror of the Notion .* row\.$")

    def _body(text):
        parts = text.split("---", 2)
        return parts[2] if len(parts) > 2 else ""

    def _strip_derived(b):
        keep = [ln for ln in b.split("\n")
                if not ln.startswith("# ") and not _BOILER.match(ln.strip())]
        return "\n".join(keep).strip()

    _body_ok, _body_bad, _body_skipped = 0, [], 0
    for _gen in _out.rglob("*.md"):
        # Same fix as the frontmatter loop above: the table is the record's PARENT DIR relative to
        # _out (not just its first path segment), so a nested source_db like "logs/change-log"
        # resolves correctly instead of truncating to "logs" and silently missing its golden dir.
        _tbl = _gen.parent.relative_to(_out).as_posix()
        _gold = _GOLDEN / _tbl / _gen.name
        if not _gold.is_file():
            continue
        _gold_text = _gold.read_text(encoding="utf-8")
        if _VIEW_MARK in _gold_text:
            _body_skipped += 1
            continue
        if _body(_gen.read_text(encoding="utf-8")).strip() == _strip_derived(_body(_gold_text)):
            _body_ok += 1
        else:
            _body_bad.append((_tbl, _gen.name))
    # HONEST SCOPE: this is a TRIPWIRE, not a positive proof of body composition. Every compared
    # record is empty-body on BOTH sides today — no mapped export carries a `Description` property,
    # so build_record's body path is never actually exercised here. What it DOES guard is the real
    # risk: a body the retired migrators rendered and the engine drops, changing without a signal.
    # (Proven to fail when broken.) It does NOT prove `Description` passthrough — the first mapped
    # table that has that property will be the first to exercise it. Note also that the skipped set
    # is currently 100% of `entities` (all 11 carry generated view blocks), so that table has no
    # body coverage at all.
    check("fo_body_substance_preserved", not _body_bad)
    print(f"FO body contract: {_body_ok} substance-equal, {len(_body_bad)} divergent, "
          f"{_body_skipped} skipped (generated view blocks)")
    if _body_bad:
        print("FO body divergences (first 6):", _body_bad[:6])

    _req_fail = [m for m in _mism if m[0] in _REQUIRED] + [b for b in _bad_extra if b[0] in _REQUIRED]
    check("fo_semantic_equivalence_required", not _req_fail)
    check("fo_semantic_equivalence_all", not _mism and not _bad_extra)
    for _tbl in sorted(_counts):
        _n, _ok = _counts[_tbl]
        print(f"FO golden equivalence {_tbl}: {_ok}/{_n} compared")
    if _mism:
        print("FO mismatches (first 6):", _mism[:6])
    if _bad_extra:
        print("FO unexpected golden-only keys:", _bad_extra[:6])

    _r = subprocess.run([sys.executable, os.path.join(_TOOLS, "state_validate.py"),
                         "--schema", str(_FO_SCHEMA_DIR / "schema.yaml"), "--all", str(_out)],
                        capture_output=True, text=True)
    check("fo_validator_pass", _r.returncode == 0)
    if _r.returncode != 0:
        print("FO validator output:", _r.stdout[-500:])
else:
    print("FO golden absent — skipping real regression (fixture proofs stand)")

# ---- harness footer (exactly once, at end of file) ----
print("FAILURES:", FAIL)
sys.exit(1 if FAIL else 0)
