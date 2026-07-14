"""Tests for the shared fact-free state validator (engine/tools/state_validate.py).

House convention (see conftest.py / suite_test.py): this is a standalone script — the
`def test_*` functions run under the `__main__` block and `sys.exit(1 if FAIL else 0)`, and
`suite_test.py` runs it as a subprocess under `python -m pytest tools/tests/`. Run it directly
for granular output: `python tools/tests/test_state_validate.py`.

Covers the CLI contract (usage error, PASS/FAIL exit codes, --all README skip) AND the
stdlib YAML-subset parser parity rules the validator depends on (quoted enum values with
parens, comma-respecting flow-list split, null->None required check, true/false bool check,
inline `# comment` stripping, a URL value with `#` that must NOT be truncated, and nested
`enums:` schema map parsing).
"""
import os
import subprocess
import sys
import tempfile
import textwrap
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS = os.path.dirname(_HERE)
TOOL = os.path.join(_TOOLS, "state_validate.py")

sys.path.insert(0, _TOOLS)
import state_validate as sv  # noqa: E402


def _run(args):
    r = subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, r.stdout + r.stderr


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _schema(tmp):
    """A schema shaped like the REAL state/schema.yaml: type names at the TOP level (no
    `types:` wrapper — the validator does `fm['type'] in schema`). The plan's draft wrapped
    these under `types:`, which the validator logic rejects; corrected here."""
    p = os.path.join(tmp, "schema.yaml")
    _write(p, textwrap.dedent("""
        # a comment line
        demo:
          required: [type, slug, name]
          enums:
            status: [open, closed]
            tax_classification: ["Partnership (1065)", "S-Corp (1120-S)"]
            csv_edge: ["a, b", c]
            flag: [true, false]
          bools: [active]
          relations: [parent]
          dates: [since]
    """).lstrip())
    return p


# ─────────────────────────── CLI contract ───────────────────────────

def test_usage_error_without_schema():
    code, _ = _run(["foo.md"])
    assert code == 2


def test_valid_note_passes():
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        note = os.path.join(d, "ok.md")
        _write(note, "---\ntype: demo\nslug: x\nname: X\nstatus: open\nsince: 2026-07-08\n---\nbody\n")
        code, out = _run(["--schema", schema, note])
        assert code == 0, out
        assert "1/1 PASS" in out


def test_bad_enum_fails():
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        note = os.path.join(d, "bad.md")
        _write(note, "---\ntype: demo\nslug: x\nname: X\nstatus: nope\n---\nbody\n")
        code, out = _run(["--schema", schema, note])
        assert code == 1, out
        assert "FAIL" in out


def test_all_mode_skips_readme():
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        tables = os.path.join(d, "tables")
        os.mkdir(tables)
        _write(os.path.join(tables, "README.md"), "# not a row\n")
        _write(os.path.join(tables, "row.md"), "---\ntype: demo\nslug: a\nname: A\n---\n")
        code, out = _run(["--schema", schema, "--all", tables])
        assert code == 0, out
        assert "1/1 PASS" in out  # README skipped


def test_all_mode_skips_views_dir():
    # _views/ holds rendered-dashboard output (no frontmatter), not state records — --all
    # DISCOVERY must skip any path with a `_views` path component, same as README.md.
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        tables = os.path.join(d, "tables")
        x = os.path.join(tables, "x")
        views = os.path.join(x, "_views")
        os.makedirs(views)
        _write(os.path.join(x, "row.md"), "---\ntype: demo\nslug: a\nname: A\n---\n")
        _write(os.path.join(views, "dash.md"), "# rendered dashboard, no frontmatter\n")
        code, out = _run(["--schema", schema, "--all", tables])
        assert code == 0, out
        assert "1/1 PASS" in out, out  # _views/dash.md skipped by discovery


def test_all_mode_explicit_views_file_still_validated():
    # Discovery skips _views/, but an EXPLICITLY passed _views file is still a real argument
    # and must still be validated (and FAIL, since it has no frontmatter).
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        views = os.path.join(d, "tables", "x", "_views")
        os.makedirs(views)
        dash = os.path.join(views, "dash.md")
        _write(dash, "# rendered dashboard, no frontmatter\n")
        code, out = _run(["--schema", schema, dash])
        assert code == 1, out
        assert "no YAML frontmatter" in out, out


def test_all_mode_validates_ndjson():
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        tables = os.path.join(d, "tables")
        os.mkdir(tables)
        _write(os.path.join(tables, "rows.ndjson"),
               '{"type": "demo", "slug": "a", "name": "A"}\n'
               '{"type": "demo", "slug": "b", "name": "B", "status": "bogus"}\n')
        code, out = _run(["--schema", schema, "--all", tables])
        assert code == 1, out           # second row's bad enum must FAIL the ndjson file
        assert "line 2" in out, out     # per-line error reporting from validate_ndjson


# ─────────────────────────── parser parity ───────────────────────────

def test_parse_quoted_enum_value_with_parens():
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        good = os.path.join(d, "g.md")
        _write(good, '---\ntype: demo\nslug: x\nname: X\ntax_classification: "S-Corp (1120-S)"\n---\n')
        code, out = _run(["--schema", schema, good])
        assert code == 0, out           # exact string incl. parens preserved -> enum member
        bad = os.path.join(d, "b.md")
        _write(bad, '---\ntype: demo\nslug: x\nname: X\ntax_classification: "S-Corp"\n---\n')
        code, out = _run(["--schema", schema, bad])
        assert code == 1, out


def test_flow_list_respects_quoted_comma():
    # csv_edge enum is ["a, b", c]; a value of "a, b" (comma inside quotes) is ONE member.
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        note = os.path.join(d, "n.md")
        _write(note, '---\ntype: demo\nslug: x\nname: X\ncsv_edge: "a, b"\n---\n')
        code, out = _run(["--schema", schema, note])
        assert code == 0, out


def test_null_required_check():
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        note = os.path.join(d, "n.md")
        _write(note, "---\ntype: demo\nslug: x\nname: null\n---\n")  # name explicitly null
        code, out = _run(["--schema", schema, note])
        assert code == 1, out
        assert "required key missing or null: name" in out, out


def test_bool_check():
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        ok = os.path.join(d, "ok.md")
        _write(ok, "---\ntype: demo\nslug: x\nname: X\nactive: true\n---\n")
        code, out = _run(["--schema", schema, ok])
        assert code == 0, out
        bad = os.path.join(d, "bad.md")
        _write(bad, "---\ntype: demo\nslug: x\nname: X\nactive: sometimes\n---\n")
        code, out = _run(["--schema", schema, bad])
        assert code == 1, out
        assert "must be a bool" in out, out


def test_inline_comment_after_value():
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        note = os.path.join(d, "n.md")
        _write(note, "---\ntype: demo\nslug: x\nname: X\nstatus: open  # trailing comment\n---\n")
        code, out = _run(["--schema", schema, note])
        assert code == 0, out  # 'open' parsed, comment stripped -> enum member


# --- parser internals (strongest parity assertions) ------------------

def test_url_with_hash_not_truncated():
    fm = sv._extract_frontmatter("---\ndataroom_url: https://x.com/a/b#frag?y=1\nother: v\n---\n")
    assert fm["dataroom_url"] == "https://x.com/a/b#frag?y=1"  # '#' not preceded by ws -> not a comment


def test_parse_null_and_empty_to_none():
    fm = sv._extract_frontmatter("---\na: null\nb:\nc: ~\n---\n")
    assert fm["a"] is None and fm["b"] is None and fm["c"] is None


def test_parse_bool_to_python_bool():
    fm = sv._extract_frontmatter("---\nt: true\nf: false\n---\n")
    assert fm["t"] is True and fm["f"] is False


def test_parse_quoted_wikilink_preserved():
    fm = sv._extract_frontmatter('---\nparent: "[[entities/example-holdings-llc]]"\n---\n')
    assert fm["parent"] == "[[entities/example-holdings-llc]]"


def test_parse_quoted_iso_date_stripped():
    fm = sv._extract_frontmatter("---\nsince: '2024-05-29'\n---\n")
    assert fm["since"] == "2024-05-29"


def test_load_schema_nested_enums_and_quoted_parens():
    with tempfile.TemporaryDirectory() as d:
        schema = sv.load_schema(_schema(d))
        assert set(schema["demo"]["required"]) == {"type", "slug", "name"}
        assert schema["demo"]["enums"]["status"] == ["open", "closed"]
        assert schema["demo"]["enums"]["tax_classification"] == ["Partnership (1065)", "S-Corp (1120-S)"]
        assert schema["demo"]["enums"]["csv_edge"] == ["a, b", "c"]  # quoted comma kept as one element
        assert schema["demo"]["bools"] == ["active"]


# ─────────────────────────── hardening: fix #1 unquoted wikilink ───────────────────────────

def test_unquoted_wikilink_passes():
    # `parent: [[entities/x]]` (no quotes) must parse as the scalar wikilink string, NOT a flow list.
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        note = os.path.join(d, "n.md")
        _write(note, "---\ntype: demo\nslug: x\nname: X\nparent: [[entities/example-holdings-llc]]\n---\n")
        code, out = _run(["--schema", schema, note])
        assert code == 0, out


def test_unquoted_wikilink_internal():
    assert sv._parse_value("[[entities/x]]") == "[[entities/x]]"


# ─────────────────────────── hardening: fix #2 block sequences ───────────────────────────

def test_block_sequence_relation_parsed_and_validated():
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        good = os.path.join(d, "g.md")
        _write(good, '---\ntype: demo\nslug: x\nname: X\nparent:\n  - "[[entities/a]]"\n  - "[[entities/b]]"\n---\n')
        code, out = _run(["--schema", schema, good])
        assert code == 0, out
        bad = os.path.join(d, "b.md")
        _write(bad, '---\ntype: demo\nslug: x\nname: X\nparent:\n  - "[[entities/a]]"\n  - not-a-link\n---\n')
        code, out = _run(["--schema", schema, bad])
        assert code == 1, out
        assert "must be a wikilink" in out, out


def test_block_sequence_same_indent_as_key():
    # Real records put `- ` items at the SAME indent as the key (metropolis-townhomes.md shape).
    fm = sv._extract_frontmatter('---\nparent:\n- "[[entities/a]]"\n- "[[entities/b]]"\n---\n')
    assert fm["parent"] == ["[[entities/a]]", "[[entities/b]]"]


def test_block_sequence_internal_list():
    fm = sv._extract_frontmatter('---\nrelated:\n  - "[[a]]"\n  - "[[b]]"\n---\n')
    assert fm["related"] == ["[[a]]", "[[b]]"]


# ─────────────────────────── hardening: fix #3 flow-list coercion ───────────────────────────

def test_flow_list_bool_coercion_internal():
    assert sv._parse_value("[true, false]") == [True, False]


def test_flow_list_enum_bool_coercion_passes():
    # schema enum `flag: [true, false]` coerces to [True, False]; a record `flag: true` -> True must match.
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        note = os.path.join(d, "n.md")
        _write(note, "---\ntype: demo\nslug: x\nname: X\nflag: true\n---\n")
        code, out = _run(["--schema", schema, note])
        assert code == 0, out


# ─────────────────────────── hardening: fix #4 malformed file in batch ───────────────────────────

def test_malformed_file_in_batch_continues():
    # A record whose `type` is a list is unhashable in `ptype not in schema`; it must FAIL cleanly
    # and NOT abort the batch — a later valid file must still be validated.
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        tables = os.path.join(d, "tables")
        os.mkdir(tables)
        _write(os.path.join(tables, "a_bad.md"), "---\ntype: [a, b]\nslug: x\nname: X\n---\n")
        _write(os.path.join(tables, "z_good.md"), "---\ntype: demo\nslug: y\nname: Y\n---\n")
        code, out = _run(["--schema", schema, "--all", tables])
        assert code == 1, out
        assert "a_bad.md" in out, out            # bad file reported as FAIL
        assert "z_good.md" not in out, out        # good file validated (not in FAIL list)
        assert "1/2 PASS" in out, out             # batch continued: both files counted


# ─────────────────────────── hardening: fix #5 missing schema -> exit 2 ───────────────────────────

def test_missing_schema_exit_2():
    with tempfile.TemporaryDirectory() as d:
        note = os.path.join(d, "n.md")
        _write(note, "---\ntype: demo\n---\n")
        missing = os.path.join(d, "does-not-exist.yaml")
        code, out = _run(["--schema", missing, note])
        assert code == 2, out
        assert "Traceback" not in out, out        # clean invocation error, not a crash


# ─────────────────────────── hardening: fix #6 empty --all dir -> 0 ───────────────────────────

def test_empty_all_dir_returns_0():
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        empty = os.path.join(d, "empty")
        os.mkdir(empty)
        code, out = _run(["--schema", schema, "--all", empty])
        assert code == 0, out


# ─────────────────────────── hardening: fix #7 '---' inside a value ───────────────────────────

def test_dashes_in_value_does_not_truncate():
    # A value containing '---' must NOT end frontmatter parsing; later keys stay validated.
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        note = os.path.join(d, "n.md")
        _write(note, '---\ntype: demo\nslug: x\nname: "a --- b"\nstatus: bogus\n---\nbody\n')
        code, out = _run(["--schema", schema, note])
        assert code == 1, out                     # status parsed past the '---'-in-value -> bad enum FAILs
        assert "status" in out, out


def test_dashes_in_value_internal():
    fm = sv._extract_frontmatter('---\nname: "a --- b"\nstatus: open\n---\nbody\n')
    assert fm["name"] == "a --- b" and fm["status"] == "open"


# ─────────────────────────── quote-style-aware escape decoding (additive) ───────────────────────────

def test_single_quoted_apostrophe_escape_decoded():
    # PyYAML writes a real apostrophe inside a single-quoted scalar as `''`; the reader must
    # decode it back to one `'` (this is why free-text records mis-read before the fix).
    fm = sv._extract_frontmatter("---\nnote: 'it''s a ''test'''\n---\n")
    assert fm["note"] == "it's a 'test'"


def test_double_quoted_escapes_decoded():
    # A double-quoted scalar decodes \n \t \" \\ (and \r); PyYAML emits multi-line free text this
    # way when width is large, so `a\nb` must come back as a real newline.
    fm = sv._extract_frontmatter('---\nbody: "line1\\nline2\\ttab \\"q\\" back\\\\slash"\n---\n')
    assert fm["body"] == 'line1\nline2\ttab "q" back\\slash'


def test_escape_decoding_is_additive_no_change_to_clean_values():
    # Values with no escape carry through byte-identical under BOTH quote styles (regression guard
    # that the additive decoder never alters already-correct enums / wikilinks / dates).
    assert sv._parse_value('"[[entities/x]]"') == "[[entities/x]]"
    assert sv._parse_value("'2024-05-29'") == "2024-05-29"
    assert sv._parse_value('"S-Corp (1120-S)"') == "S-Corp (1120-S)"
    assert sv._parse_value("plain unquoted \\n stays literal") == "plain unquoted \\n stays literal"


def test_unknown_double_quote_escape_left_literal():
    # `\p` is not a defined escape -> backslash preserved (additive, no data loss).
    fm = sv._extract_frontmatter('---\npath: "C:\\path\\\\to"\n---\n')
    assert fm["path"] == "C:\\path\\to"


# ─────────────────────────── enforcement-path coverage (gate flagged zero coverage) ───────────────────────────

def test_bad_relation_fails():
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        note = os.path.join(d, "n.md")
        _write(note, "---\ntype: demo\nslug: x\nname: X\nparent: not-a-wikilink\n---\n")
        code, out = _run(["--schema", schema, note])
        assert code == 1, out
        assert "must be a wikilink" in out, out


def test_valid_quoted_relation_passes():
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        note = os.path.join(d, "n.md")
        _write(note, '---\ntype: demo\nslug: x\nname: X\nparent: "[[entities/x]]"\n---\n')
        code, out = _run(["--schema", schema, note])
        assert code == 0, out


def test_bad_date_fails():
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        note = os.path.join(d, "n.md")
        _write(note, "---\ntype: demo\nslug: x\nname: X\nsince: 07/08/2026\n---\n")
        code, out = _run(["--schema", schema, note])
        assert code == 1, out
        assert "ISO YYYY-MM-DD" in out, out


def test_valid_date_passes():
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        note = os.path.join(d, "n.md")
        _write(note, "---\ntype: demo\nslug: x\nname: X\nsince: 2026-07-08\n---\n")
        code, out = _run(["--schema", schema, note])
        assert code == 0, out


def test_missing_type_fails():
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        note = os.path.join(d, "n.md")
        _write(note, "---\nslug: x\nname: X\n---\n")   # no type: key
        code, out = _run(["--schema", schema, note])
        assert code == 1, out
        assert "unknown type" in out, out


def test_unknown_type_value_fails():
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        note = os.path.join(d, "n.md")
        _write(note, "---\ntype: bogus\nslug: x\nname: X\n---\n")
        code, out = _run(["--schema", schema, note])
        assert code == 1, out
        assert "unknown type" in out, out


def test_no_frontmatter_fails():
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        note = os.path.join(d, "n.md")
        _write(note, "# just a heading\nno frontmatter here\n")
        code, out = _run(["--schema", schema, note])
        assert code == 1, out
        assert "no YAML frontmatter" in out, out


def test_unterminated_frontmatter_fails():
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        note = os.path.join(d, "n.md")
        _write(note, "---\ntype: demo\nslug: x\nname: X\n")   # no closing fence
        code, out = _run(["--schema", schema, note])
        assert code == 1, out
        assert "unterminated" in out, out


def test_ndjson_invalid_json_line_fails():
    with tempfile.TemporaryDirectory() as d:
        schema = _schema(d)
        note = os.path.join(d, "rows.ndjson")
        _write(note, '{"type": "demo", "slug": "a", "name": "A"}\nnot valid json\n')
        code, out = _run(["--schema", schema, note])
        assert code == 1, out
        assert "invalid JSON" in out, out


def test_schema_flag_with_no_value_exit_2():
    code, out = _run(["--schema"])   # --schema is the last token, no value follows
    assert code == 2, out


# ─────────────────────────── multi-line flow-folded scalar reading (A53) ───────────────────────────

def test_multiline_single_quoted_flow_fold():
    # A single-quoted scalar OPENED on one line and CLOSED several physical lines later (the shape
    # PyYAML emits for long free text). YAML flow-folding: a lone line break folds to a space;
    # each blank line contributes one '\n'; leading indentation on continuation lines is trimmed;
    # `''` decodes to one `'`. This is the exact `notes:` shape found on the FO john-doe asset.
    text = (
        "---\n"
        "type: t\n"
        "notes: 'Workout, 2 received per Doe''s sheet.\n"
        "\n"
        "  RECONCILIATION 2026-05-07 (Notion vs Drive):\n"
        "\n"
        "  - Balance was $100,000 (understated ~$26K).\n"
        "\n"
        "  - Kept as-is per Seth''s ''current'' workout instruction.'\n"
        "after: tail\n"
        "---\n"
    )
    fm = sv._extract_frontmatter(text)
    assert fm["notes"] == (
        "Workout, 2 received per Doe's sheet.\n"
        "RECONCILIATION 2026-05-07 (Notion vs Drive):\n"
        "- Balance was $100,000 (understated ~$26K).\n"
        "- Kept as-is per Seth's 'current' workout instruction."
    ), repr(fm.get("notes"))
    # The parser must RESUME cleanly at the key after the multi-line scalar closes.
    assert fm["after"] == "tail", repr(fm.get("after"))


def test_multiline_single_break_folds_to_space():
    # No blank line between continuation lines => a single line break folds to a single space.
    text = "---\nq: 'one\n  two\n  three'\nk: v\n---\n"
    fm = sv._extract_frontmatter(text)
    assert fm["q"] == "one two three", repr(fm.get("q"))
    assert fm["k"] == "v"


def test_multiline_double_quoted_flow_fold():
    # Double-quoted multi-line scalar folds the same way; `\"`/`\\` escapes still decode.
    text = '---\nb: "part \\"one\\"\n  part two"\nk: v\n---\n'
    fm = sv._extract_frontmatter(text)
    assert fm["b"] == 'part "one" part two', repr(fm.get("b"))
    assert fm["k"] == "v"


def test_multiline_does_not_strip_inner_hash():
    # A continuation line carrying ` #NNN` must NOT be truncated as a comment (it is inside the
    # quote). This is why continuation lines are consumed RAW, not comment-stripped.
    text = "---\nn: 'see\n  item #38 for detail'\n---\n"
    fm = sv._extract_frontmatter(text)
    assert fm["n"] == "see item #38 for detail", repr(fm.get("n"))


if __name__ == "__main__":
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
