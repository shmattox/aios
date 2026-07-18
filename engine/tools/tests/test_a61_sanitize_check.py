# sanitize:allow-file — fixtures use synthetic/out-of-range ids by design (A79)
"""A61 — pre-tag sanitize guard (`sanitize_check.py`): instance-identifier leak scan.

The recurrence guard for the A59/A60 leak class (BACKLOG A61). Two tiers:
  1. STRUCTURAL id-format patterns shipped in the public tool (OI-<digits>, FO-<ALNUM>) —
     instance-agnostic, case-insensitive, suffix-tolerant.
  2. INSTANCE entity-NAME patterns loaded from an out-of-repo file — never hardcoded here.

FIXTURES ARE DELIBERATELY SYNTHETIC. A test for a leak-detector must never embed real instance
data (that would itself be the leak the tool exists to stop — caught in the A61 review). All ids
below (OI-000, FO-SAMPLE, …) and names (Acme, Zephyr, …) are invented, not real open-items/entities.

The load-bearing test is `test_real_repo_backlog_is_clean`: it runs the REAL checker against the
REAL BACKLOG.md and asserts zero findings — a wired signal, not inspectable prose.
"""
import os, sys, tempfile, shutil, subprocess

_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _TOOLS)
import sanitize_check as sc

_REPO = os.path.dirname(os.path.dirname(_TOOLS))  # engine/tools -> engine -> repo root
_TOOL = os.path.join(_TOOLS, "sanitize_check.py")


def _tmpfile(text, name="x.md"):
    d = tempfile.mkdtemp()
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return d, p


# ── structural tier (shipped, instance-agnostic) — SYNTHETIC ids only ──────────────────────────

def test_structural_flags_open_item_id():
    hits = sc.scan_text("next brief should put OI-000 into motion", sc.structural_patterns())
    assert [h[1] for h in hits] == ["OI-000"] and hits[0][2] == "open-item-id"


def test_structural_flags_synthetic_silo_id():
    hits = sc.scan_text("FO-SAMPLE went cold until hand-set", sc.structural_patterns())
    assert [h[1] for h in hits] == ["FO-SAMPLE"] and hits[0][2] == "synthetic-silo-id"


def test_structural_flags_ein():
    # A71: an EIN (tax id) format dd-ddddddd is high-signal PII. Build it at runtime so this
    # leak-detector's OWN source carries no matching literal (a tree-wide scan of the tests stays
    # EIN-clean, the same discipline as the synthetic ids above being the only tolerated hits).
    ein = "12" + "-" + "3456789"
    hits = sc.scan_text("filed under EIN %s last spring" % ein, sc.structural_patterns())
    assert [h[1] for h in hits] == [ein] and hits[0][2] == "ein"


def test_structural_ein_catches_label_glued():
    # label run straight onto the digits (no space between "EIN" and the number). \b would fail
    # here (letter->digit is no boundary), so the pattern uses a leading lookbehind — must catch it.
    ein = "12" + "-" + "3456789"
    hits = sc.scan_text("filed EIN%s today" % ein, sc.structural_patterns())
    assert [h[1] for h in hits] == [ein] and hits[0][2] == "ein"


def test_structural_ein_ignores_digit_then_letter_hash():
    # a "...-06-21-7805551e" style filename hash (digits glued to a trailing letter) must NOT match —
    # this is the trailing-\b case that a naive lookahead would wrongly accept.
    assert sc.scan_text("record claude-code-2026-06-21-7805551e.md here", sc.structural_patterns()) == []


def test_structural_ein_ignores_letter_placeholder():
    # the synthetic placeholder uses letters (XX-XXXXXXX) so it survives the scrub, exactly as the
    # generic OI-N placeholder escapes the open-item pattern.
    assert sc.scan_text("EIN XX-XXXXXXX in the fixture", sc.structural_patterns()) == []


def test_structural_ein_ignores_dates_and_longer_runs():
    # a date (dddd-dd-dd), a 3-lead run, and the subtle 2-lead/8-tail case (12-34567890, where
    # \d{2} matches the lead so only the trailing lookahead prevents a hit) must NOT look like an EIN
    for s in ("on 2026-07-14 today", "id 123-45678901 changed", "ref 12-34567890 here"):
        assert sc.scan_text(s, sc.structural_patterns()) == [], "must not flag %r" % s


def test_structural_catches_reworded_variants():
    # lowercase, alnum-suffix, digit-suffix — the LOW review finding: a trivial rewording must trip
    for s in ("oi-000", "OI-000a", "fo-sample", "FO-XY2"):
        assert sc.scan_text("see %s here" % s, sc.structural_patterns()), "must catch variant %r" % s


def test_structural_ignores_generic_placeholder():
    # the algorithm's generic placeholder `OI-N` is NOT a real id and must survive the scrub
    hits = sc.scan_text("an OI-N thread owns only its own id; slug threads own referenced OIs",
                        sc.structural_patterns())
    assert hits == [], "placeholder OI-N / OIs must not trip the format scan, got %r" % hits


def test_structural_ignores_bare_silo_abbrev_and_substrings():
    # "FO" the silo abbrev (no -ALNUM suffix) and an embedded 'fo-' (info-foo) must not match
    assert sc.scan_text("carry live FO operational item-ids", sc.structural_patterns()) == []
    assert sc.scan_text("the info-foo config value", sc.structural_patterns()) == []


def test_clean_engineering_prose_no_findings():
    txt = "deterministic join writes in_motion {thread_id,status,next_action,court}; wired before validate_cache"
    assert sc.scan_text(txt, sc.structural_patterns()) == []


# ── instance tier (out-of-repo names; SYNTHETIC names only) ────────────────────────────────────

def test_instance_patterns_flag_names_only_with_file():
    d, _ = _tmpfile("")
    try:
        pf = os.path.join(d, "sanitize-patterns.txt")
        with open(pf, "w", encoding="utf-8") as f:
            f.write("# instance entity names (out-of-repo)\n\nAcme\nZephyr\n")
        pats = sc.structural_patterns() + sc.load_instance_patterns(pf)
        assert sc.scan_text("put Acme into motion; same Zephyr docs", pats), "names flagged WITH the file"
        # WITHOUT the instance file, the same names are not flagged (structural-only)
        assert sc.scan_text("put Acme into motion; same Zephyr docs", sc.structural_patterns()) == []
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_patterns_file_ignores_comments_and_blanks():
    d, _ = _tmpfile("")
    try:
        pf = os.path.join(d, "p.txt")
        with open(pf, "w", encoding="utf-8") as f:
            f.write("# comment\n\n   \nZephyr\n")
        loaded = sc.load_instance_patterns(pf)
        assert len(loaded) == 1 and loaded[0][0] == "instance:Zephyr"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_missing_patterns_file_is_fail_soft():
    assert sc.load_instance_patterns("/no/such/file.txt") == []
    assert sc.load_instance_patterns("") == []


def test_bad_regex_in_patterns_fails_loud():
    d, _ = _tmpfile("")
    try:
        pf = os.path.join(d, "p.txt")
        with open(pf, "w", encoding="utf-8") as f:
            f.write("Acme\n[unclosed\n")
        try:
            sc.load_instance_patterns(pf)
            assert False, "a malformed regex must raise, not silently pass"
        except ValueError:
            pass
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── env_root + instance-path resolution (production wiring — the review's coverage gap) ─────────

def test_resolve_env_root_finds_markers_and_none_branch():
    root = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(root, "state"))
        os.makedirs(os.path.join(root, "profile"))
        sub = os.path.join(root, "Projects", "x")
        os.makedirs(sub)
        assert sc.resolve_env_root(sub) == os.path.abspath(root), "walks up to the state/+profile/ dir"
        # a bare dir with no markers resolves to None (reaches fs root)
        bare = tempfile.mkdtemp()
        try:
            assert sc.resolve_env_root(bare) is None
        finally:
            shutil.rmtree(bare, ignore_errors=True)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_resolve_instance_path_explicit_and_autodiscover():
    # explicit wins and is marked must_exist
    p, must = sc.resolve_instance_path("/some/explicit.txt", ".")
    assert p == "/some/explicit.txt" and must is True
    # auto-discovers <env_root>/state/sanitize-patterns.txt, optional (must_exist False)
    root = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(root, "state"))
        os.makedirs(os.path.join(root, "profile"))
        p, must = sc.resolve_instance_path(None, root)
        assert p == os.path.join(root, sc.DEFAULT_INSTANCE_PATTERNS) and must is False
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── file scan + CLI (incl. the fail-loud fixes) ────────────────────────────────────────────────

def test_scan_file_reports_lineno():
    d, p = _tmpfile("clean line\nleak OI-000 here\n")
    try:
        assert sc.scan_file(p, sc.structural_patterns()) == [(2, "OI-000", "open-item-id")]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _cli(*a):
    return subprocess.run([sys.executable, _TOOL, *a], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def test_cli_exit_codes_dirty_and_clean():
    d, dirty = _tmpfile("has FO-DEMO leak\n", "dirty.md")
    try:
        clean = os.path.join(d, "clean.md")
        with open(clean, "w", encoding="utf-8") as f:
            f.write("no ids here, just prose\n")
        rd = _cli(dirty)
        assert rd.returncode == 1 and "FO-DEMO" in rd.stdout
        rc = _cli(clean)
        assert rc.returncode == 0 and "clean" in rc.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_cli_explicit_missing_patterns_fails_loud():
    d, clean = _tmpfile("just prose\n", "c.md")
    try:
        r = _cli(clean, "--patterns", os.path.join(d, "does-not-exist.txt"))
        assert r.returncode == 2 and "not found" in (r.stdout + r.stderr)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_cli_zero_files_is_error_not_clean():
    # run in a dir with no BACKLOG.md and no positional files -> usage error, never a false "clean"
    d = tempfile.mkdtemp()
    try:
        r = _cli("--root", d)
        assert r.returncode == 2 and "no files to scan" in (r.stdout + r.stderr)
        assert "clean" not in r.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_real_repo_backlog_is_clean():
    """WIRED acceptance: run the real checker on the real BACKLOG.md — must be structurally clean.
    Proves both that the A61 scrub holds AND that the guard actually fires on the real artifact."""
    bl = os.path.join(_REPO, "BACKLOG.md")
    assert os.path.isfile(bl), bl
    hits = sc.scan_file(bl, sc.structural_patterns())
    assert hits == [], "BACKLOG.md carries structural instance-id leaks: %r" % hits


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print("  ok  " + fn.__name__)
        except Exception:
            failed += 1; print(" FAIL " + fn.__name__); traceback.print_exc()
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    sys.exit(1 if failed else 0)
