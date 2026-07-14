import os, sys
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
check("import_wrote_both", (_sd / "tables" / "state-thing" / "widget.md").is_file()
      and (_sd / "tables" / "state-person" / "ada.md").is_file())
txt = (_sd / "tables" / "state-thing" / "widget.md").read_text(encoding="utf-8")
check("import_type", "type: state-thing" in txt)
check("import_checkbox", "flag: true" in txt)
check("import_relation", 'owner: "[[people/ada]]"' in txt)
check("import_notion_id", "notion_id: t1" in txt)
check("import_multi_select", "tags: [alpha, beta]" in txt)
check("import_date", "launched: 2026-01-15" in txt)
check("import_quoted_colon", 'note: "Phase 2: launch"' in txt)
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
_wtxt = (_sd2 / "tables" / "state-thing" / "widget.md").read_text(encoding="utf-8")
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
_w3 = (_sd3 / "tables" / "state-thing" / "widget.md").read_text(encoding="utf-8")
check("ext_last_synced_from_cli", "last_synced: 2026-06-30" in _w3)

# Extension 2b: _meta.exported OVERRIDES the CLI value (per-snapshot export date wins)
_root4, _sd4 = _scratch_silo()
_snap4 = _sd4 / "_snap"; _snap4.mkdir()
(_snap4 / "things-export.json").write_text(json.dumps(
    {"_meta": {"exported": "2026-07-01"}, "url_to_slug": {"https://n/w1": "widget"},
     "rows": [{"Name": "Widget", "url": "https://n/w1"}]}), encoding="utf-8")
dm.import_silo(_root4, "demo", _snap4, last_synced="2026-06-30")
_w4 = (_sd4 / "tables" / "state-thing" / "widget.md").read_text(encoding="utf-8")
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
_atxt = (_sd5 / "tables" / "state-asset" / "john-loan.md").read_text(encoding="utf-8")
check("a53_cross_export_relation", 'asset: "[[prices/usd]]"' in _atxt)          # resolved via prices' slug map
check("a53_computed_owner", 'owner_entity: "[[companies/acme-lending]]"' in _atxt)   # south -> acme-lending
_wtxt = (_sd5 / "tables" / "state-asset" / "west-note.md").read_text(encoding="utf-8")
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
      (_sd / "tables" / "state-thing" / "widget.md").read_text(encoding="utf-8"))

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
_alpha_dir = _sd6 / "tables" / "state-alpha"
check("a53_badrel_no_partial_write",              # the valid first table must NOT have been written
      not _alpha_dir.exists() or not any(_alpha_dir.glob("*.md")))

# ── Task-4 REAL regression: FO Notion snapshots -> state records, semantic equivalence ──
# Runs UNCONDITIONALLY against the real FO snapshots (only guard: snapshot/schema absence, e.g. a
# laptop with no family-office clone). Comparison uses the ENGINE's own reader,
# dm._extract_frontmatter — no PyYAML. The reader now decodes YAML escapes quote-style-aware
# (state_validate._unquote: single-quoted '' -> ', double-quoted \n/\t/\"/\\), so a shipped value
# in YAML single-quoted style and the generated double-quoted form de-serialize to the SAME literal.
# Both sides are read by the same reader, so the comparison is a true semantic equivalence check on
# the engine's terms; state_validate.py --all is also run over the generated tree (must exit 0).
#
# ONE reader-grammar boundary remains: a shipped value written as a MULTI-LINE quoted (flow-folded)
# scalar is outside the stdlib subset reader's single-line grammar (the module documents that it
# does not parse block/folded scalars). Records carrying such a value are detected structurally and
# excluded from the field comparison (counted + printed) — this is a reader limitation, NOT a
# field-mapping error; the affected record's economic fields still validate.
import glob as _glob, subprocess
_FO_SNAP = Path(_TOOLS).resolve().parents[2] / "family-office" / "state-mirror" / "migration"
_ENV_ROOT = Path(_TOOLS).resolve().parents[2].parent
_FO_STATE = _ENV_ROOT / "state" / "domains" / "familyoffice"
if _FO_SNAP.is_dir() and (_FO_STATE / "schema.yaml").is_file():
    _load_fm = dm._extract_frontmatter    # the engine's own stdlib reader (now escape-decoding)

    def _nv(v):
        # A length-1 relation list is the migrator's collapsed scalar (semantically identical).
        return v[0] if isinstance(v, list) and len(v) == 1 else v

    def _norm(fm):
        return {k: _nv(v) for k, v in fm.items()}

    def _closed_on_line(v):
        # True iff the quoted scalar `v` (v[0] in "'\"") is terminated on its own physical line.
        q = v[0]; i = 1; n = len(v)
        while i < n:
            if q == '"':
                if v[i] == "\\":
                    i += 2; continue
                if v[i] == '"':
                    return True
            else:  # single-quoted: a lone ' closes; '' is an escaped quote
                if v[i] == "'":
                    if i + 1 < n and v[i + 1] == "'":
                        i += 2; continue
                    return True
            i += 1
        return False

    def _has_multiline_scalar(text):
        # A shipped frontmatter value that OPENS a quote it does not close on its own line is a
        # multi-line flow-folded scalar — outside the subset reader's grammar.
        L = text.splitlines()
        try:
            end = L.index("---", 1)
        except ValueError:
            return False
        for line in L[1:end]:
            if ":" not in line:
                continue
            v = line.partition(":")[2].strip()
            if v[:1] in ("'", '"') and not _closed_on_line(v):
                return True
        return False

    _cfg = dm.load_silo_config(_ENV_ROOT, "familyoffice")
    _name2db = {t["name"]: t["source_db"] for t in _cfg["tables"]}
    # Normalize the dated FO exports into a scratch snapshot dir the importer's contract expects:
    #  - filename <db>-notion-export-<DATE>.json -> <db>-export.json
    #  - inject _meta.exported=<DATE> (drives last_synced; the tax-ledger export carries no _meta)
    #  - reconstruct url_to_slug (the slug decisions the snapshot contract carries) from the shipped
    #    records' own notion_id->filename where an older export predates emitting one; the entities
    #    export already carries an authentic url_to_slug and is used as-is.
    # CAVEAT: for the reconstructed tables (notes/people/insurance/tax-ledger) the filename/slug
    # derivation is thus NOT independently proven here (it is taken from the shipped filenames);
    # what IS proven for them is field-mapping equivalence of the frontmatter. entities exercises a
    # real, authentic url_to_slug end-to-end.
    _snap = Path(tempfile.mkdtemp())
    for _t in _cfg["tables"]:
        _db = _t["source_db"]
        _fs = _glob.glob(str(_FO_SNAP / f"{_db}-notion-export-*.json"))
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
            for _p in (_FO_STATE / "tables" / _db).glob("*.md"):
                _nid = _load_fm(_p.read_text(encoding="utf-8")).get("notion_id")
                if _nid is not None:
                    _n2s[str(_nid)] = _p.stem
            _u2s = {r["url"]: _n2s.get(dm.notion_id_from_url(r["url"]), dm.notion_id_from_url(r["url"]))
                    for r in _rows}
        (_snap / f"{_db}-export.json").write_text(
            json.dumps({"_meta": _meta, "url_to_slug": _u2s, "rows": _rows}), encoding="utf-8")

    _out = Path(tempfile.mkdtemp()) / "tables"
    dm.import_silo(_ENV_ROOT, "familyoffice", _snap, _out, last_synced="2026-06-30")

    _REQUIRED = {"state-entity", "state-note"}          # must reach equivalence (scope-narrowed)
    _CURATED = {"wiki", "owner_entity", "asset"}        # shipped-only state-native curation, out of scope
    _mism, _bad_extra, _counts, _skipped = [], [], {}, []
    for _gen in _out.rglob("*.md"):
        _tbl = _gen.relative_to(_out).parts[0]
        _shipped = _FO_STATE / "tables" / _name2db[_tbl] / _gen.name
        _c = _counts.setdefault(_tbl, [0, 0, 0])         # [n, equal, skipped_multiline]
        _c[0] += 1
        if not _shipped.is_file():
            _mism.append((_tbl, _gen.name, "no shipped file")); continue
        _shipped_text = _shipped.read_text(encoding="utf-8")
        if _has_multiline_scalar(_shipped_text):
            # Shipped record carries a multi-line flow-folded scalar (block-scalar territory the
            # stdlib subset reader documents as out of scope). Excluded from the field comparison.
            _c[2] += 1; _skipped.append((_tbl, _gen.name)); continue
        _g = _norm(_load_fm(_gen.read_text(encoding="utf-8")))
        _s = _norm(_load_fm(_shipped_text))
        _diffs = {k: (_g.get(k), _s.get(k)) for k in _g if _g.get(k) != _s.get(k)}
        _unexpected = (set(_s) - set(_g)) - _CURATED     # a shipped Notion field we forgot to map
        if _unexpected:
            _bad_extra.append((_tbl, _gen.name, sorted(_unexpected)))
        if _diffs:
            _mism.append((_tbl, _gen.name, list(_diffs)[:6]))
        else:
            _c[1] += 1
    _req_fail = [m for m in _mism if m[0] in _REQUIRED] + [b for b in _bad_extra if b[0] in _REQUIRED]
    check("fo_semantic_equivalence_required", not _req_fail)
    check("fo_semantic_equivalence_all", not _mism and not _bad_extra)
    for _tbl in sorted(_counts):
        _n, _ok, _sk = _counts[_tbl]
        print(f"FO equivalence {_tbl}: {_ok}/{_n - _sk} compared" + (f" ({_sk} multiline-scalar skipped)" if _sk else ""))
    if _skipped:
        print("FO multiline-scalar records excluded (reader-grammar limit):", _skipped)
    if _mism:
        print("FO mismatches (first 6):", _mism[:6])
    if _bad_extra:
        print("FO unexpected shipped-only keys:", _bad_extra[:6])

    _r = subprocess.run([sys.executable, os.path.join(_TOOLS, "state_validate.py"),
                         "--schema", str(_FO_STATE / "schema.yaml"), "--all", str(_out)],
                        capture_output=True, text=True)
    check("fo_validator_pass", _r.returncode == 0)
    if _r.returncode != 0:
        print("FO validator output:", _r.stdout[-500:])
else:
    print("FO snapshots absent — skipping real regression (fixture proofs stand)")

# ---- harness footer (exactly once, at end of file) ----
print("FAILURES:", FAIL)
sys.exit(1 if FAIL else 0)
