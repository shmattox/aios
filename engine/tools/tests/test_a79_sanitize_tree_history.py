"""A79 — sanitize_check tree + history tiers + pre-push hook.

Spec: docs/superpowers/specs/2026-07-18-sanitize-tree-history-design.md.
Fail-loud rules under test: a git failure or an empty scan is an ERROR (exit 2), never "clean".
FIXTURES ARE SYNTHETIC and leak tokens are RUNTIME-BUILT (string concat) so this file itself
scans clean under --tree — the same discipline as test_a61's runtime-built EIN.
"""
import os, sys, subprocess, tempfile, shutil

_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _TOOLS)
import sanitize_check as sc

_REPO = os.path.dirname(os.path.dirname(_TOOLS))
_TOOL = os.path.join(_TOOLS, "sanitize_check.py")
_HOOK_SRC = os.path.join(_TOOLS, "hooks", "pre-push")

_OI = "OI-" + "000"          # runtime-built so this test file never carries a literal token
_MARK = "sanitize" + ":allow"


def _git(repo, *a):
    r = subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, "git %s failed: %s%s" % (a, r.stdout, r.stderr)
    return r


def _fixture_repo():
    d = tempfile.mkdtemp()
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    return d


def _write(repo, name, text, binary=False):
    p = os.path.join(repo, name)
    if binary:
        with open(p, "wb") as f:
            f.write(b"\x00\x01PNGish" + os.urandom(64))
    else:
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    return p


def _cli(*a, cwd=None):
    return subprocess.run([sys.executable, _TOOL, *a], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=cwd)


# ── sanitize:allow marker ──────────────────────────────────────────────────────────────────────

def test_allow_marker_suppresses_structural_finding():
    line = "example id %s in reference prose  <!-- %s -->" % (_OI, _MARK)
    assert sc.scan_text(line, sc.structural_patterns()) == []


def test_allow_marker_never_suppresses_ein():
    ein = "12" + "-" + "3456789"
    line = "EIN %s stays flagged  # %s" % (ein, _MARK)
    hits = sc.scan_text(line, sc.structural_patterns())
    assert [h[2] for h in hits] == ["ein"], "the marker must not whitelist a real tax-id format"


def test_file_level_marker_exempts_whole_file_in_tree():
    repo = _fixture_repo()
    try:
        _write(repo, "fixtures.md", "<!-- %s-file: synthetic fixture doc -->\nexample %s\nand %s again\n"
               % (_MARK, _OI, _OI))
        _git(repo, "add", "fixtures.md"); _git(repo, "commit", "-qm", "c1")
        r = _cli("--tree", "--root", repo)
        assert r.returncode == 0, r.stdout + r.stderr
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_file_level_marker_never_exempts_ein():
    repo = _fixture_repo()
    try:
        ein = "12" + "-" + "3456789"
        _write(repo, "fixtures.md", "<!-- %s-file: fixtures -->\nEIN %s must still flag\n" % (_MARK, ein))
        _git(repo, "add", "fixtures.md"); _git(repo, "commit", "-qm", "c1")
        r = _cli("--tree", "--root", repo)
        assert r.returncode == 1 and "ein" in r.stdout
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_history_respects_file_level_marker():
    repo = _fixture_repo()
    try:
        _write(repo, "fixtures.md", "<!-- %s-file: fixtures -->\nexample %s\n" % (_MARK, _OI))
        _git(repo, "add", "fixtures.md"); _git(repo, "commit", "-qm", "c1")
        r = _cli("--history", "HEAD", "--root", repo)
        assert r.returncode == 0, r.stdout + r.stderr
    finally:
        shutil.rmtree(repo, ignore_errors=True)


# ── --tree ─────────────────────────────────────────────────────────────────────────────────────

def test_tree_scans_tracked_only_and_skips_binaries():
    repo = _fixture_repo()
    try:
        _write(repo, "clean.md", "just prose\n")
        _write(repo, "dirty.md", "leak %s here\n" % _OI)
        _write(repo, "img.bin", "", binary=True)
        _git(repo, "add", "clean.md", "dirty.md", "img.bin")
        _git(repo, "commit", "-qm", "c1")
        _write(repo, "untracked-leak.md", "worse leak %s\n" % _OI)   # NOT added
        r = _cli("--tree", "--root", repo)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "dirty.md" in r.stdout and _OI in r.stdout
        assert "untracked-leak.md" not in r.stdout, "tracked surface only — untracked is not published"
        assert "img.bin" not in r.stdout, "binaries skipped"
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_tree_clean_repo_exits_zero_with_count():
    repo = _fixture_repo()
    try:
        _write(repo, "a.md", "prose only\n")
        _git(repo, "add", "a.md")
        _git(repo, "commit", "-qm", "c1")
        r = _cli("--tree", "--root", repo)
        assert r.returncode == 0 and "clean" in r.stdout
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_tree_zero_tracked_files_is_error():
    repo = _fixture_repo()
    try:
        r = _cli("--tree", "--root", repo)
        assert r.returncode == 2 and "clean" not in r.stdout
        assert "unrecognized arguments" not in r.stderr
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_tree_outside_git_repo_is_error_not_clean():
    d = tempfile.mkdtemp()
    try:
        r = _cli("--tree", "--root", d)
        assert r.returncode == 2 and "clean" not in r.stdout
        assert "unrecognized arguments" not in r.stderr
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── --history ──────────────────────────────────────────────────────────────────────────────────

def _leak_then_scrub_repo():
    """c1 clean -> c2 adds a leak -> c3 scrubs it. Tree at HEAD is clean; history is dirty."""
    repo = _fixture_repo()
    _write(repo, "doc.md", "clean start\n")
    _git(repo, "add", "doc.md"); _git(repo, "commit", "-qm", "c1")
    _write(repo, "doc.md", "clean start\nleak %s here\n" % _OI)
    _git(repo, "add", "doc.md"); _git(repo, "commit", "-qm", "c2")
    _write(repo, "doc.md", "clean start\nscrubbed\n")
    _git(repo, "add", "doc.md"); _git(repo, "commit", "-qm", "c3")
    return repo


def test_history_finds_leak_tree_is_clean():
    repo = _leak_then_scrub_repo()
    try:
        rt = _cli("--tree", "--root", repo)
        assert rt.returncode == 0, "tree at HEAD must be clean (that is the trap)"
        rh = _cli("--history", "HEAD", "--root", repo)
        assert rh.returncode == 1, rh.stdout + rh.stderr
        assert _OI in rh.stdout and "doc.md" in rh.stdout
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_history_clean_reports_scanned_commits():
    repo = _fixture_repo()
    try:
        _write(repo, "a.md", "prose\n")
        _git(repo, "add", "a.md"); _git(repo, "commit", "-qm", "c1")
        r = _cli("--history", "HEAD", "--root", repo)
        assert r.returncode == 0 and "scanned" in r.stdout and "1 commit" in r.stdout
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_history_removed_lines_do_not_flag():
    # only ADDED lines are the leak vector for a push; a removal (the scrub itself) must not flag
    repo = _leak_then_scrub_repo()
    try:
        head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        prev = _git(repo, "rev-parse", "HEAD~1").stdout.strip()
        r = _cli("--history", "%s..%s" % (prev, head), "--root", repo)   # c3 only: removes the leak
        assert r.returncode == 0, "the scrub commit's removed lines must not flag: %s" % r.stdout
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_history_invalid_range_is_error_never_clean():
    repo = _fixture_repo()
    try:
        _write(repo, "a.md", "x\n"); _git(repo, "add", "a.md"); _git(repo, "commit", "-qm", "c1")
        r = _cli("--history", "no-such-ref..HEAD", "--root", repo)
        assert r.returncode == 2 and "clean" not in r.stdout, \
            "a git ERROR must never read as clean (the 2026-07-15 trap)"
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_history_empty_range_is_error():
    repo = _fixture_repo()
    try:
        _write(repo, "a.md", "x\n"); _git(repo, "add", "a.md"); _git(repo, "commit", "-qm", "c1")
        r = _cli("--history", "HEAD..HEAD", "--root", repo)
        assert r.returncode == 2 and "clean" not in r.stdout
        assert "unrecognized arguments" not in r.stderr
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_history_allow_marker_applies_to_added_lines():
    repo = _fixture_repo()
    try:
        _write(repo, "ref.md", "anonymized example %s  <!-- %s -->\n" % (_OI, _MARK))
        _git(repo, "add", "ref.md"); _git(repo, "commit", "-qm", "c1")
        r = _cli("--history", "HEAD", "--root", repo)
        assert r.returncode == 0, r.stdout
    finally:
        shutil.rmtree(repo, ignore_errors=True)


# ── pre-push hook ──────────────────────────────────────────────────────────────────────────────

def _install_hook(repo):
    hooks = os.path.join(repo, ".git", "hooks")
    dst = os.path.join(hooks, "pre-push")
    shutil.copyfile(_HOOK_SRC, dst)
    os.chmod(dst, 0o755)
    # the fixture repo has no engine/tools — point the hook at the real tool via env var override
    return dst


def test_prepush_hook_blocks_dirty_history_even_when_tree_clean():
    bare = tempfile.mkdtemp(); repo = None
    try:
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        repo = _leak_then_scrub_repo()                       # tree clean, history dirty
        _git(repo, "remote", "add", "origin", bare)
        _install_hook(repo)
        env = dict(os.environ, SANITIZE_CHECK_TOOL=_TOOL)
        r = subprocess.run(["git", "-C", repo, "push", "-q", "origin", "main"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env)
        assert r.returncode != 0, "push must be BLOCKED (outgoing history carries the leak)"
        assert _OI in (r.stdout + r.stderr)
        # the explicit human override still works
        r2 = subprocess.run(["git", "-C", repo, "push", "-q", "--no-verify", "origin", "main"],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", env=env)
        assert r2.returncode == 0, "--no-verify is the deliberate override: %s" % (r2.stdout + r2.stderr)
    finally:
        shutil.rmtree(bare, ignore_errors=True)
        if repo:
            shutil.rmtree(repo, ignore_errors=True)


def test_prepush_hook_missing_tool_blocks_loudly():
    # the guard being ABSENT must block, never silently wave the push through (spec §4 fail-loud;
    # the fixture repo has no engine/tools and we deliberately do NOT set SANITIZE_CHECK_TOOL)
    bare = tempfile.mkdtemp(); repo = None
    try:
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        repo = _fixture_repo()
        _write(repo, "a.md", "prose\n")
        _git(repo, "add", "a.md"); _git(repo, "commit", "-qm", "c1")
        _git(repo, "remote", "add", "origin", bare)
        _install_hook(repo)
        env = {k: v for k, v in os.environ.items() if k != "SANITIZE_CHECK_TOOL"}
        r = subprocess.run(["git", "-C", repo, "push", "-q", "origin", "main"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env)
        assert r.returncode != 0, "missing guard must BLOCK, not silently pass"
        assert "BLOCKED" in (r.stdout + r.stderr)
    finally:
        shutil.rmtree(bare, ignore_errors=True)
        if repo:
            shutil.rmtree(repo, ignore_errors=True)


def test_prepush_hook_passes_clean_push():
    bare = tempfile.mkdtemp(); repo = None
    try:
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        repo = _fixture_repo()
        _write(repo, "a.md", "prose\n")
        _git(repo, "add", "a.md"); _git(repo, "commit", "-qm", "c1")
        _git(repo, "remote", "add", "origin", bare)
        _install_hook(repo)
        env = dict(os.environ, SANITIZE_CHECK_TOOL=_TOOL)
        r = subprocess.run(["git", "-C", repo, "push", "-q", "origin", "main"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env)
        assert r.returncode == 0, r.stdout + r.stderr
    finally:
        shutil.rmtree(bare, ignore_errors=True)
        if repo:
            shutil.rmtree(repo, ignore_errors=True)


# ── wired acceptance: the REAL repo ────────────────────────────────────────────────────────────

def test_real_repo_tree_is_clean():
    """The real repo's whole tracked surface must scan clean (known-benign example lines carry
    the allow marker). This is the wired signal that keeps the class from recurring."""
    r = _cli("--tree", "--root", _REPO)
    assert r.returncode == 0, "real-repo tree scan not clean:\n%s%s" % (r.stdout, r.stderr)


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
