"""Tests for notion_publish.to_properties (C1 Task 7). Pure function — no network, no auth.
pytest-native (`def test_*`, `assert`) so this module can be collected DIRECTLY by pytest
(tools/tests/conftest.py only ignores files with no module-level `def test_*` — see
test_state_coverage.py / test_state_validate.py for the same shape).
"""
import os
import sys

_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _TOOLS)
import notion_publish as np  # noqa: E402

_T = {"name": "state-asset", "source_db": "assets", "ignored": {},
      "fields": [("name", "title", None, "Name", None),
                 ("qty", "number", None, "Qty", None),
                 ("active", "checkbox", None, "Active", None),
                 ("acquisition_date", "date", None, "Acquisition Date", None),
                 ("asset", "relation", "prices/{slug}", "Asset", "prices"),
                 ("url_field", "text", None, "URL", None)]}


def test_title_number_checkbox_date_userdefined():
    p = np.to_properties({"name": "BTC", "qty": 1.5, "active": True,
                           "acquisition_date": "2026-01-04",
                           "url_field": "https://example.test"}, _T)
    assert p["Name"] == "BTC"
    assert p["Qty"] == 1.5 and isinstance(p["Qty"], float)
    assert p["Active"] == "__YES__"
    assert p["date:Acquisition Date:start"] == "2026-01-04"
    assert "Acquisition Date" not in p  # split, never a bare date key
    assert p["userDefined:URL"] == "https://example.test"  # reserved-name prefix
    assert "Asset" not in p  # absent field omitted entirely


def test_checkbox_false_is_no():
    p = np.to_properties({"name": "X", "active": False}, _T)
    assert p["Active"] == "__NO__"


def test_relation_is_a_list():
    p = np.to_properties({"name": "X", "asset": "[[prices/btc]]"}, _T)
    assert isinstance(p["Asset"], list)


def test_none_is_omitted_not_cleared():
    # a None value must be OMITTED, never sent as a null that clears the property
    p = np.to_properties({"name": "X", "qty": None}, _T)
    assert "Qty" not in p


def test_reserved_id_prop_also_gets_prefix():
    table = {"fields": [("ext_id", "text", None, "id", None)], "ignored": {}}
    p = np.to_properties({"ext_id": "abc-123"}, table)
    assert p["userDefined:id"] == "abc-123"
    assert "id" not in p


# --- round-trip checks against domain_mirror.coerce (the inverse, Notion -> local) ---
# These use REAL field shapes drawn from state/domains/*/schema.yaml, not just "returns a dict".

import domain_mirror as dm  # noqa: E402

_RT_TABLE = {"fields": [
    ("title_f", "title", None, "Name", None),
    ("text_f", "text", None, "Notes", None),
    ("select_f", "select", None, "Status", None),
    ("url_f", "url", None, "Link", None),
    ("multi_f", "multi_select", None, "Tags", None),
    ("jmulti_f", "json_multi_select", None, "JTags", None),
    ("num_f", "number", None, "Qty", None),
    ("date_f", "date", None, "Due", None),
    ("chk_f", "checkbox", None, "Active", None),
    ("rel_f", "relation", "prices/{slug}", "Asset", "prices"),
], "ignored": {}}


def _round_trip(kind, incoming_value, **coerce_kw):
    local = dm.coerce(kind, incoming_value, **coerce_kw)
    fm = {}
    for field, k, _t, _p, _r in _RT_TABLE["fields"]:
        if k == kind:
            fm[field] = local
            break
    return local, np.to_properties(fm, _RT_TABLE)


def test_roundtrip_scalars_exact():
    # title/text/select/url survive coerce() then to_properties() byte-for-byte.
    _, p = _round_trip("title", "Acme Corp", url_to_slug={}, link_tmpl="x/{slug}")
    assert p["Name"] == "Acme Corp"
    _, p = _round_trip("text", "some note", url_to_slug={}, link_tmpl="x/{slug}")
    assert p["Notes"] == "some note"
    _, p = _round_trip("select", "Open", url_to_slug={}, link_tmpl="x/{slug}")
    assert p["Status"] == "Open"
    _, p = _round_trip("url", "https://a.test", url_to_slug={}, link_tmpl="x/{slug}")
    assert p["Link"] == "https://a.test"


def test_roundtrip_number_and_checkbox_exact():
    _, p = _round_trip("number", 42, url_to_slug={}, link_tmpl="x/{slug}")
    assert p["Qty"] == 42
    _, p = _round_trip("checkbox", "__YES__", url_to_slug={}, link_tmpl="x/{slug}")
    assert p["Active"] == "__YES__"
    _, p = _round_trip("checkbox", "__NO__", url_to_slug={}, link_tmpl="x/{slug}")
    assert p["Active"] == "__NO__"


def test_roundtrip_multi_select_exact():
    _, p = _round_trip("multi_select", ["a", "b"], url_to_slug={}, link_tmpl="x/{slug}")
    assert p["Tags"] == ["a", "b"]


def test_roundtrip_json_multi_select_exact():
    # coerce() decodes the JSON-string wire form into a real list; to_properties has no
    # dedicated branch for this kind, so it falls through to the generic pass-through — which
    # is correct here because the value is ALREADY a list by the time it reaches us.
    _, p = _round_trip("json_multi_select", '["x", "y"]', url_to_slug={}, link_tmpl="x/{slug}")
    assert p["JTags"] == ["x", "y"]


def test_roundtrip_date_LOSES_time_of_day():
    # KNOWN LOSSY CASE: a real local record can carry a full timestamp for a "date" field
    # (state/domains/personal/tables/attended/angels-at-rays.md: `date: 2026-05-29T19:10:00...`)
    # because coerce() stores the raw string verbatim. to_properties() truncates with [:10],
    # so the time-of-day and offset are silently dropped on the way back out.
    ts = "2026-05-29T19:10:00.000+00:00"
    local, p = _round_trip("date", ts, url_to_slug={}, link_tmpl="x/{slug}")
    assert local == ts  # incoming side kept the full timestamp
    assert p["date:Due:start"] == "2026-05-29"  # outgoing side only kept the date part
    assert p["date:Due:start"] != local


def test_roundtrip_relation_CANNOT_reconstruct_a_page_url():
    # KNOWN GAP: coerce() turns an incoming Notion page URL into a local wikilink via
    # url_to_slug/link_tmpl. to_properties() only strips the "[[ ]]" brackets — it has no
    # slug->url table to invert with, so it can never rebuild an actual page URL. The spec's
    # "relations as arrays of page URLs" contract is only satisfiable by a caller (Task 8) that
    # supplies its own slug->url map; this pure function alone cannot round-trip a relation.
    local = dm.coerce("relation", "https://notion.so/real-page-id",
                       url_to_slug={"https://notion.so/real-page-id": "btc"},
                       link_tmpl="prices/{slug}")
    assert local == "[[prices/btc]]"
    fm = {"rel_f": local}
    p = np.to_properties(fm, _RT_TABLE)
    assert p["Asset"] == ["prices/btc"]  # NOT the original page URL


def test_date_prop_already_flattened_is_not_rewrapped():
    # 34 of 35 real `date` fields in state/domains/*/schema.yaml declare `prop` as the wire
    # key gather already flattens it to (e.g. "date:Due:start", domain_sync._flatten_date) --
    # NOT a bare display name. Running plan_publish against real `personal` data surfaced that
    # to_properties() was re-wrapping it into "date:date:Due:start:start". The one schema
    # outlier (personal `created`, a bare "Created") still needs the wrap.
    table = {"fields": [("due", "date", None, "date:Due:start", None),
                         ("created", "date", None, "Created", None)], "ignored": {}}
    p = np.to_properties({"due": "2026-06-01", "created": "2026-01-01"}, table)
    assert p == {"date:Due:start": "2026-06-01", "date:Created:start": "2026-01-01"}


def test_unknown_kind_raises():
    table = {"fields": [("f", "totally_bogus_kind", None, "Weird", None)], "ignored": {}}
    try:
        np.to_properties({"f": "value"}, table)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "totally_bogus_kind" in str(e)


def test_all_eleven_real_kinds_accepted():
    # The eleven kinds domain_mirror.coerce() supports must all still pass without raising.
    table = {"fields": [
        ("title_f", "title", None, "Name", None),
        ("text_f", "text", None, "Notes", None),
        ("select_f", "select", None, "Status", None),
        ("url_f", "url", None, "Link", None),
        ("multi_f", "multi_select", None, "Tags", None),
        ("jmulti_f", "json_multi_select", None, "JTags", None),
        ("num_f", "number", None, "Qty", None),
        ("date_f", "date", None, "Due", None),
        ("chk_f", "checkbox", None, "Active", None),
        ("rel_f", "relation", "prices/{slug}", "Asset", "prices"),
        ("jrel_f", "json_relation", "prices/{slug}", "Asset2", "prices"),
    ], "ignored": {}}
    fm = {"title_f": "X", "text_f": "n", "select_f": "Open", "url_f": "https://a.test",
          "multi_f": ["a"], "jmulti_f": ["b"], "num_f": 1, "date_f": "2026-01-01",
          "chk_f": True, "rel_f": "[[prices/btc]]", "jrel_f": "[[prices/eth]]"}
    p = np.to_properties(fm, table)  # must not raise
    assert set(p.keys()) == {"Name", "Notes", "Status", "Link", "Tags", "JTags",
                              "Qty", "date:Due:start", "Active", "Asset", "Asset2"}


# ── Task 8: dry-run planner ───────────────────────────────────────────────────
import tempfile as _tf
import textwrap as _tw
from pathlib import Path as _P


def _new_root():
    root = _P(_tf.mkdtemp()).resolve()
    (root / "profile").mkdir()
    (root / "profile" / "domains.yaml").write_text("brief:\n  trigger: go\n", encoding="utf-8")
    return root


def test_plan_publish_dry_run():
    root = _new_root()
    sd = root / "state" / "domains" / "demo"
    (sd / "tables" / "things").mkdir(parents=True)
    (sd / "schema.yaml").write_text(_tw.dedent("""\
        state-thing:
          required: [name, type, notion_id]
          notion_source_db: things
          notion_fields:
            name: [Name, title]
        """), encoding="utf-8")
    (sd / "tables" / "things" / "widget.md").write_text(
        "---\ntype: state-thing\nname: Widget\nnotion_id: abc123\n---\n", encoding="utf-8")
    (sd / "tables" / "things" / "fresh.md").write_text(
        "---\ntype: state-thing\nname: Fresh\n---\n", encoding="utf-8")

    plan = {e["slug"]: e for e in np.plan_publish(root, "demo")}
    assert set(plan) == {"widget", "fresh"}
    assert plan["widget"]["operation"] == "update"
    assert plan["widget"]["notion_id"] == "abc123"
    assert plan["fresh"]["operation"] == "create"
    assert plan["fresh"]["notion_id"] is None
    assert plan["widget"]["properties"]["Name"] == "Widget"


def _tree_hashes(root: _P) -> dict:
    return {str(p.relative_to(root)): (p.stat().st_size, p.read_bytes())
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_dry_run_writes_nothing_real_check():
    # The old check (`not glob("*.tmp")`) can't fail: nothing in this codebase writes .tmp
    # files, so it passes even if plan_publish rewrites every file in the silo (verified by
    # wrapping plan_publish to clobber a record, write a new file, and overwrite schema.yaml --
    # the old assertion still passed). Hash the whole silo tree before/after instead.
    root = _new_root()
    sd = root / "state" / "domains" / "demo3"
    (sd / "tables" / "things").mkdir(parents=True)
    (sd / "schema.yaml").write_text(_tw.dedent("""\
        state-thing:
          required: [name, type, notion_id]
          notion_source_db: things
          notion_fields:
            name: [Name, title]
        """), encoding="utf-8")
    (sd / "tables" / "things" / "widget.md").write_text(
        "---\ntype: state-thing\nname: Widget\nnotion_id: abc123\n---\n", encoding="utf-8")

    before = _tree_hashes(sd)
    np.plan_publish(root, "demo3")
    assert _tree_hashes(sd) == before  # every file: same size, same bytes, nothing added/removed


def test_plan_publish_resolves_relations_and_flags_dates():
    # Task 7 left two documented gaps for Task 8 to own: a relation can't become a real page
    # URL inside the pure builder, and a `date` truncation must not look like drift. This
    # covers both: `holding` links a published target (resolves to a notion.so URL) and an
    # unpublished one (comes back UNRESOLVED, never a fake URL); `acquired` is a full timestamp
    # that must survive as a plain date with an explanatory note, not a silent change.
    root = _new_root()
    sd = root / "state" / "domains" / "demo2"
    (sd / "tables" / "prices").mkdir(parents=True)
    (sd / "tables" / "assets").mkdir(parents=True)
    (sd / "schema.yaml").write_text(_tw.dedent("""\
        state-price:
          required: [name, type]
          notion_source_db: prices
          notion_fields:
            name: [Name, title]
        state-asset:
          required: [name, type]
          notion_source_db: assets
          notion_fields:
            name: [Name, title]
            acquired: [Acquired, date]
            asset: [Asset, relation, "prices/{slug}"]
        """), encoding="utf-8")
    (sd / "tables" / "prices" / "btc.md").write_text(
        "---\ntype: state-price\nname: BTC\nnotion_id: pxid1\n---\n", encoding="utf-8")
    (sd / "tables" / "assets" / "holding.md").write_text(
        '---\ntype: state-asset\nname: Holding\n'
        'acquired: "2026-05-29T19:10:00.000+00:00"\n'
        'asset: "[[prices/btc]]"\n---\n', encoding="utf-8")
    (sd / "tables" / "assets" / "orphan.md").write_text(
        '---\ntype: state-asset\nname: Orphan\nasset: "[[prices/nope]]"\n---\n',
        encoding="utf-8")

    plan = {e["slug"]: e for e in np.plan_publish(root, "demo2")}
    assert plan["holding"]["properties"]["Asset"] == ["https://www.notion.so/pxid1"]
    assert plan["holding"]["properties"]["date:Acquired:start"] == "2026-05-29"
    assert any("date-only by design" in n for n in plan["holding"]["notes"])
    assert plan["orphan"]["properties"]["Asset"] == [f"{np._UNRESOLVED_MARK}:prices/nope"]
    assert any("not resolvable" in n for n in plan["orphan"]["notes"])
    # the diff artifact itself must be plain text a human can skim, not a repr dump
    text = np.format_diff(list(plan.values()))
    assert "## holding" in text and "## orphan" in text
    assert f"{np._UNRESOLVED_MARK}:prices/nope" in text


def test_unresolved_marker_does_not_collide_with_the_bare_word():
    # A bare "UNRESOLVED" string collides with the literal word inside real record prose
    # (verified: 3 hits in familyoffice session-log narrative, zero of them a real marker).
    # The marker itself must not be the plain word.
    assert np._UNRESOLVED_MARK != "UNRESOLVED"
    assert "UNRESOLVED" in np._UNRESOLVED_MARK  # still human-readable, just not bare


def test_date_note_only_fires_on_real_truncation():
    # MEDIUM fix: the note used to fire on mere presence of the date key, not on whether
    # truncation actually dropped anything -- measured 239 false-positive notes in personal
    # against 3 real ones, burying the genuine unresolved-relation warnings under the same `!`
    # prefix. A plain "YYYY-MM-DD" value truncates to itself, so no note should fire.
    table = {"fields": [("due", "date", None, "Due", None)], "ignored": {}}
    p = np.to_properties({"due": "2026-06-01"}, table)
    assert p["date:Due:start"] == "2026-06-01"

    root = _new_root()
    sd = root / "state" / "domains" / "demo4"
    (sd / "tables" / "things").mkdir(parents=True)
    (sd / "schema.yaml").write_text(_tw.dedent("""\
        state-thing:
          required: [name, type]
          notion_source_db: things
          notion_fields:
            name: [Name, title]
            due: [Due, date]
        """), encoding="utf-8")
    (sd / "tables" / "things" / "plain.md").write_text(
        '---\ntype: state-thing\nname: Plain\ndue: "2026-06-01"\n---\n', encoding="utf-8")
    plan = {e["slug"]: e for e in np.plan_publish(root, "demo4")}
    assert plan["plain"]["properties"]["date:Due:start"] == "2026-06-01"
    assert plan["plain"]["notes"] == []  # date-only-by-design note must NOT fire — no loss occurred


def test_date_key_handles_the_end_shape_too():
    # MEDIUM fix: _date_key's old predicate only recognized an already-flattened ":start" key.
    # state/domains/familyoffice/schema.yaml:290 (state-session's date_end) declares the third
    # real shape, "date:Date:end" -- unfixed, this corrupts into "date:date:Date:end:start"
    # exactly like the :start bug did (latent in prod only because every date_end is null today).
    assert np._date_key("date:Date:end") == "date:Date:end"
    assert np._date_key("date:Due:start") == "date:Due:start"
    assert np._date_key("Created") == "date:Created:start"


def test_elide_result_never_exceeds_the_cap():
    # A naive line[:500] + suffix leaves the RESULT over 500 chars too (verified: FO's real
    # artifact still showed 408/408 lines over the cap after a first-pass fixed cut, because
    # the suffix itself wasn't budgeted for). The elided line must actually fit.
    long_line = "x" * 10000
    out = np._elide(long_line)
    assert len(out) <= np._MAX_LINE
    assert "chars elided" in out
    assert np._elide("short") == "short"


def test_plan_publish_reports_a_missing_table_dir():
    # "Also fix" from review: a mapped table whose tables/<source_db> dir doesn't exist used to
    # be silently skipped -- in the sub-project whose whole stated risk is silent loss, that
    # table's absence from the artifact must be a visible line, not nothing.
    root = _new_root()
    sd = root / "state" / "domains" / "demo5"
    sd.mkdir(parents=True)
    (sd / "schema.yaml").write_text(_tw.dedent("""\
        state-ghost:
          required: [name, type]
          notion_source_db: ghosts
          notion_fields:
            name: [Name, title]
        """), encoding="utf-8")
    plan = np.plan_publish(root, "demo5")
    assert len(plan) == 1
    assert plan[0]["operation"] == "skip"
    assert "tables/ghosts" in plan[0]["notes"][0]
    text = np.format_diff(plan)
    assert "tables/ghosts not found" in text


def test_cli_survives_redirected_stdout_on_a_non_utf8_console():
    # Review HIGH: `print()` to a REDIRECTED stream uses the platform codec (cp1252 on native
    # Windows), not the console's. `format_diff()`-in-process tests above never touch print()
    # and stayed green while the real CLI crashed on `> out.txt` -- even on an all-ASCII silo,
    # because the module's own ⟦UNRESOLVED⟧ marker isn't cp1252-encodable either. Reproduce the
    # actual boundary: subprocess, real file redirect, and strip any UTF-8 opt-in from the env
    # so this fails the same way on a stock Windows console.
    import subprocess
    root = _new_root()
    sd = root / "state" / "domains" / "demo6"
    (sd / "tables" / "assets").mkdir(parents=True)
    (sd / "schema.yaml").write_text(_tw.dedent("""\
        state-asset:
          required: [name, type]
          notion_source_db: assets
          notion_fields:
            name: [Name, title]
            asset: [Asset, relation, "prices/{slug}"]
        """), encoding="utf-8")
    (sd / "tables" / "assets" / "orphan.md").write_text(
        '---\ntype: state-asset\nname: Orphan\nasset: "[[prices/nope]]"\n---\n',
        encoding="utf-8")

    out_file = root / "out.txt"
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    script = os.path.join(_TOOLS, "notion_publish.py")
    with open(out_file, "wb") as fh:
        proc = subprocess.run([sys.executable, script, "--env-root", str(root),
                                "--silo", "demo6"], stdout=fh, stderr=subprocess.PIPE, env=env)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    text = out_file.read_text(encoding="utf-8")
    assert np._UNRESOLVED_MARK in text


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("  ok  " + fn.__name__)
        except Exception:
            failed += 1
            print(" FAIL " + fn.__name__)
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
