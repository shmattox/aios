import json, os, re, sys
from pathlib import Path

_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _TOOLS)
import stable_slugs as ss
import domain_mirror as dm

FAIL = []
def check(name, cond):
    if not cond: FAIL.append(name)


# ── unit rules ────────────────────────────────────────────────────────────

# generic rule (exercised via "insurance") — plain slugify(Name)
check("generic_basic",
      ss.stable_slugs("insurance", [{"url": "https://n/a", "Name": "Term Life Policy"}], {})
      == {"https://n/a": "term-life-policy"})

# generic rule — empty/None name falls back to the table default word
check("generic_empty_name_fallback",
      ss.stable_slugs("insurance", [{"url": "https://n/a", "Name": None}], {})
      == {"https://n/a": "insurance"})

# prices — Name.lower() verbatim, no dash-substitution, no collision suffix
check("prices_simple_lower",
      ss.stable_slugs("prices", [{"url": "https://n/p1", "Name": "Bitcoin"}], {})
      == {"https://n/p1": "bitcoin"})
check("prices_no_dash_sub",
      ss.stable_slugs("prices", [{"url": "https://n/p2", "Name": "US Dollar Cash"}], {})
      == {"https://n/p2": "us dollar cash"})

# assets — collision-suffix rule: two rows with the same slugified Name collide;
# first keeps the bare slug, second gets notion_id[-6:] appended.
_assets_rows = [
    {"url": "https://app.notion.com/aaa000111222", "Name": "Cash Account"},
    {"url": "https://app.notion.com/bbb000333444", "Name": "Cash Account"},
]
_assets_out = ss.stable_slugs("assets", _assets_rows, {})
check("assets_collision_first_bare", _assets_out["https://app.notion.com/aaa000111222"] == "cash-account")
check("assets_collision_second_suffixed",
      _assets_out["https://app.notion.com/bbb000333444"] == "cash-account-333444")

# sessions — date+title composite
check("sessions_composite",
      ss.stable_slugs("sessions", [{
          "url": "https://n/s1", "date:Date:start": "2026-05-05",
          "Session": "SES-27 — A&L consolidation", "Session ID": 27,
      }], {}) == {"https://n/s1": "2026-05-05-a-l-consolidation"})

# sessions — date-only (no title) falls back to bare date
check("sessions_date_only",
      ss.stable_slugs("sessions", [{
          "url": "https://n/s2", "date:Date:start": "2026-06-01",
          "Session": "", "Session ID": 5,
      }], {}) == {"https://n/s2": "2026-06-01"})

# sessions — collision breaks on Session ID, not notion_id[-6:]
_sess_rows = [
    {"url": "https://n/sA", "date:Date:start": "2026-05-05", "Session": "Daily Sync", "Session ID": 1},
    {"url": "https://n/sB", "date:Date:start": "2026-05-05", "Session": "Daily Sync", "Session ID": 2},
]
_sess_out = ss.stable_slugs("sessions", _sess_rows, {})
check("sessions_collision_first_bare", _sess_out["https://n/sA"] == "2026-05-05-daily-sync")
check("sessions_collision_second_id_suffixed", _sess_out["https://n/sB"] == "2026-05-05-daily-sync-002")

# ── first-seen-wins invariant (A84) ─────────────────────────────────────────
# a row whose notion_id is already in the slugmap keeps its OLD slug even
# though its title/name has since changed in Notion. Synthetic fixture data
# (aios is a PUBLIC repo — never embed real FamilyOffice content/ids here;
# see A61/A71/Plan-2b scrub precedent).
_nid = dm.notion_id_from_url("https://app.notion.com/00000000000000000000000000000001")
_slugmap = {_nid: "widget-co-100k-note-v1-old-title"}
_renamed_row = {
    "url": "https://app.notion.com/00000000000000000000000000000001",
    "Note": "Widget Co note, renamed with new synthetic terms",
}
_out = ss.stable_slugs("notes", [_renamed_row], _slugmap)
check("first_seen_wins_keeps_old_slug",
      _out[_renamed_row["url"]] == "widget-co-100k-note-v1-old-title")
check("first_seen_wins_map_unchanged",
      _slugmap[_nid] == "widget-co-100k-note-v1-old-title")

# ── stable_slugs mutates AND returns the slugmap for persistence ───────────
_map2 = {}
_ret = ss.stable_slugs("prices", [{"url": "https://n/p3", "Name": "Ether"}], _map2)
_new_nid = dm.notion_id_from_url("https://n/p3")
check("mutates_slugmap_in_place", _map2.get(_new_nid) == "ether")
check("returns_url_to_slug_not_slugmap", _ret == {"https://n/p3": "ether"})

# ── load_slugmap / save_slugmap round-trip ──────────────────────────────────
import tempfile
_tmp = Path(tempfile.mkdtemp()) / "_slugmap.json"
check("load_missing_file_is_empty_dict", ss.load_slugmap(_tmp) == {})
ss.save_slugmap(_tmp, {"abc123": "foo-bar", "xyz789": "baz"})
check("save_creates_file", _tmp.is_file())
check("roundtrip_equal", ss.load_slugmap(_tmp) == {"abc123": "foo-bar", "xyz789": "baz"})

# ── nested table_name normalizes on basename ────────────────────────────────
check("nested_table_name_dispatches_by_basename",
      ss.stable_slugs("logs/sessions", [{
          "url": "https://n/s9", "date:Date:start": "2026-01-01",
          "Session": "Kickoff", "Session ID": 9,
      }], {}) == {"https://n/s9": "2026-01-01-kickoff"})

# ── unknown table raises ────────────────────────────────────────────────────
try:
    ss.stable_slugs("bogus-table", [{"url": "https://n/x", "Name": "x"}], {})
    check("unknown_table_raises", False)
except ValueError:
    check("unknown_table_raises", True)


# ══════════════════════════════════════════════════════════════════════════
# ── Step 5 correctness gate: reproduce the FO golden notion_id -> slug map ──
# Hermetic: both sides frozen (the dated FO exports + the golden/ records
# extracted from a frozen commit). For every mapped FO table,
# stable_slugs(table, frozen_export_rows, {}) must equal the golden's
# notion_id -> slug map exactly. Guard: the family-office clone's absence
# (e.g. a laptop without it) skips this block; the unit proofs above stand.
_FO_MIG = Path(_TOOLS).resolve().parents[2] / "family-office" / "state-mirror" / "migration"
_GOLDEN = _FO_MIG / "golden"

# table key (as passed to stable_slugs) -> (export json glob prefix, golden subdir)
_FO_TABLES = {
    "entities": ("entities", "entities"),
    "insurance": ("insurance", "insurance"),
    "notes": ("notes", "notes"),
    "people": ("people", "people"),
    "tax-ledger": ("tax-ledger", "tax-ledger"),
    "tasks": ("tasks", "tasks"),
    "manifest": ("manifest", "manifest"),
    "change-log": ("change-log", "logs/change-log"),
    "decision-log": ("decision-log", "logs/decision-log"),
    "sessions": ("sessions", "logs/sessions"),
    "prices": ("prices", "prices"),
    "projects": ("projects", "projects"),
    "assets": ("assets", "assets"),
}

_reproduced = []
if _GOLDEN.is_dir():
    import glob as _glob
    for _table, (_export_prefix, _golden_sub) in _FO_TABLES.items():
        _matches = _glob.glob(str(_FO_MIG / f"{_export_prefix}-notion-export-*.json"))
        _gdir = _GOLDEN / _golden_sub
        if not _matches or not _gdir.is_dir():
            continue
        _export = json.loads(Path(_matches[0]).read_text(encoding="utf-8"))
        _rows = _export["rows"]

        # expected notion_id -> slug, reconstructed from golden filenames + frontmatter
        _n2s_expected = {}
        for _p in _gdir.glob("*.md"):
            _fm = dm._extract_frontmatter(_p.read_text(encoding="utf-8"))
            _nid = _fm.get("notion_id")
            if _nid is not None:
                _n2s_expected[str(_nid)] = _p.stem

        _got_slugmap = {}
        ss.stable_slugs(_table, _rows, _got_slugmap)

        _mismatches = [nid for nid, slug in _got_slugmap.items()
                       if _n2s_expected.get(nid) != slug]
        _coverage_gap = set(_n2s_expected) - set(_got_slugmap)  # golden ids the export never produced

        check(f"golden_reproduce_{_table}_no_mismatches", not _mismatches)
        check(f"golden_reproduce_{_table}_full_coverage", not _coverage_gap)
        check(f"golden_reproduce_{_table}_nonempty", len(_got_slugmap) > 0)
        if not _mismatches and not _coverage_gap and _got_slugmap:
            _reproduced.append(_table)
        else:
            print(f"stable_slugs golden mismatch [{_table}]: "
                  f"{len(_mismatches)} mismatched, {len(_coverage_gap)} missing from export. "
                  f"first few mismatches: {_mismatches[:5]}")

    check("golden_reproduce_all_13_tables", sorted(_reproduced) == sorted(_FO_TABLES))
    print("golden-reproduced tables:", sorted(_reproduced))
else:
    print("FO golden absent — skipping Step 5 hermetic reproduction proof (unit proofs stand)")


# ---- harness footer (exactly once, at end of file) ----
print("FAILURES:", FAIL)
sys.exit(1 if FAIL else 0)
