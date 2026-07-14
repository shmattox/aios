import os, sys, textwrap, pathlib, json as _json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import reflect

def _write(p, text):
    pathlib.Path(p).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(p).write_text(textwrap.dedent(text), encoding="utf-8")

def test_discover_finds_day_records_and_journals(tmp_path):
    vault = tmp_path / "vault"
    kb_map = {"gm": "03_GeneralManagement", "personal": "01_Personal"}
    # a session record for the target day
    _write(vault / "03_GeneralManagement/raw/sessions/claude-code-2026-07-11-abcd1234.md", """\
        ---
        type: session-record
        id: abcd1234
        domain: gm
        project: general
        started_utc: 2026-07-11T15:07:00Z
        conflict_key: gm/wiki/journal/2026-07-11.md
        ---
        Focus/Outcome/Why body.
        """)
    # an evidence file that must be ignored (not type: session-record)
    _write(vault / "03_GeneralManagement/raw/sessions/intents-abcd1234.md",
           "---\ntype: intents\n---\n- hi\n")
    # a record from a DIFFERENT day, must be excluded
    _write(vault / "03_GeneralManagement/raw/sessions/claude-code-2026-07-10-eeee0000.md", """\
        ---
        type: session-record
        id: eeee0000
        domain: gm
        started_utc: 2026-07-10T09:00:00Z
        conflict_key: gm/wiki/journal/2026-07-10.md
        ---
        old day.
        """)
    # the target day's journal note
    _write(vault / "03_GeneralManagement/wiki/journal/2026-07-11.md", "# 2026-07-11\n")

    out = reflect.discover(str(vault), kb_map, "2026-07-11")
    ids = sorted(r["id"] for r in out["records"])
    assert ids == ["abcd1234"]
    assert out["records"][0]["kb"] == "gm"
    assert out["records"][0]["conflict_key"] == "gm/wiki/journal/2026-07-11.md"
    assert len(out["journals"]) == 1
    assert out["journals"][0]["date"] == "2026-07-11"

def test_lessons_anchor_finds_block_and_rules(tmp_path):
    md = tmp_path / "CLAUDE.md"
    md.write_text(
        "# Title\n\nintro\n\n**Lessons**\n"
        "- First rule here.\n"
        "- Second rule here.\n\n"
        "## Next section\n", encoding="utf-8")
    a = reflect.lessons_anchor(str(md))
    assert a["exists"] is True
    assert a["existing_rules"] == ["First rule here.", "Second rule here."]
    # line 7 is "- Second rule here." (1-based): 1=#Title 2=blank 3=intro 4=blank 5=**Lessons** 6=First 7=Second
    assert a["insert_after_line"] == 7

def test_lessons_anchor_absent(tmp_path):
    md = tmp_path / "CLAUDE.md"
    md.write_text("# Title\n\nno lessons block here\n", encoding="utf-8")
    a = reflect.lessons_anchor(str(md))
    assert a["exists"] is False
    assert a["insert_after_line"] is None

def test_dedup_context_surfaces_overlapping_knowledge(tmp_path):
    vault = tmp_path / "vault"
    kb_map = {"gm": "03_GeneralManagement"}
    _write(vault / "03_GeneralManagement/wiki/knowledge/living-knowledge-graph.md",
           "---\ntitle: Living knowledge graph\ntype: source\n---\n# Living knowledge graph\n")
    _write(vault / "03_GeneralManagement/wiki/knowledge/unrelated-topic.md",
           "---\ntitle: Unrelated topic\n---\n# Unrelated topic\n")
    out = reflect.dedup_context(str(vault), kb_map, "gm", "living knowledge graph obsidian")
    slugs = [c["slug"] for c in out["candidates"]]
    assert "living-knowledge-graph" in slugs
    assert "unrelated-topic" not in slugs
    assert "living-knowledge-graph" in out["existing_slugs"]
    assert "unrelated-topic" in out["existing_slugs"]

def test_verify_flags_missing_type_and_empty(tmp_path):
    vault = tmp_path / "vault"
    kb_map = {"gm": "03_GeneralManagement"}
    good = vault / "03_GeneralManagement/wiki/staging/a-concept.md"
    _write(good, "---\ntype: source\ntitle: A concept\n---\nbody\n")
    bad_empty = vault / "03_GeneralManagement/wiki/staging/empty.md"
    _write(bad_empty, "")
    r_ok = reflect.verify([str(good)], str(vault), kb_map)
    assert r_ok["ok"] is True and r_ok["problems"] == []
    r_bad = reflect.verify([str(bad_empty)], str(vault), kb_map)
    assert r_bad["ok"] is False and any("empty" in p for p in r_bad["problems"])
    # a valid-looking draft OUTSIDE any live KB folder (and NOT under /wiki/staging/) must be caught —
    # the KB-containment guard applies to every draft path, not only staging ones.
    rogue = vault / "07_Unmapped/wiki/decisions/2026-07-11-x.md"
    _write(rogue, "---\ntype: decision\n---\nbody\n")
    r_rogue = reflect.verify([str(rogue)], str(vault), kb_map)
    assert r_rogue["ok"] is False and any("outside a live KB" in p for p in r_rogue["problems"])

def test_write_atomic_roundtrip(tmp_path):
    p = tmp_path / "sub" / "f.md"
    reflect.write_atomic(str(p), "hello\n")
    assert p.read_text(encoding="utf-8") == "hello\n"

def test_cli_lessons_anchor(tmp_path, capsys):
    md = tmp_path / "CLAUDE.md"
    md.write_text(
        "# Title\n\nintro\n\n**Lessons**\n"
        "- First rule here.\n\n"
        "## Next section\n", encoding="utf-8")
    rc = reflect.main(["lessons-anchor", str(md)])
    assert rc == 0
    out = _json.loads(capsys.readouterr().out)
    assert out["exists"] is True
    assert out["existing_rules"] == ["First rule here."]

def test_cli_dedup_context(tmp_path, capsys):
    vault = tmp_path / "vault"
    kb_map = {"gm": "03_GeneralManagement"}
    _write(vault / "03_GeneralManagement/wiki/knowledge/living-knowledge-graph.md",
           "---\ntitle: Living knowledge graph\ntype: source\n---\n# Living knowledge graph\n")
    rc = reflect.main([
        "dedup-context",
        "--vault", str(vault),
        "--kb-map", _json.dumps(kb_map),
        "--kb", "gm",
        "living knowledge graph",
    ])
    assert rc == 0
    out = _json.loads(capsys.readouterr().out)
    assert "living-knowledge-graph" in out["existing_slugs"]
    assert any(c["slug"] == "living-knowledge-graph" for c in out["candidates"])
