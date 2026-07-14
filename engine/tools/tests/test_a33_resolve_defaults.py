import os, sys, tempfile, shutil, subprocess

_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _TOOLS)
import resolve_sweep as rs
import seed_resolve_defaults as srd

_TEMPLATE = os.path.join(os.path.dirname(_TOOLS), "templates", "resolve-defaults.yaml")
_TOOL = os.path.join(_TOOLS, "seed_resolve_defaults.py")


def _resolve_key_lines(text):
    """Count real top-level `resolve:` KEY lines (ignoring the template's comment mentions)."""
    return sum(1 for ln in text.splitlines() if ln.rstrip() == "resolve:" or
               (ln.startswith("resolve:") and not ln.lstrip().startswith("#")))


def _try_yaml_load(text):
    try:
        import yaml
    except ImportError:
        return None
    return yaml.safe_load(text)


# --- the shipped template is a well-formed, functional default -------------------------------

def test_template_file_exists():
    assert os.path.isfile(_TEMPLATE), "shipped default block must exist for setup to lift"


def test_template_parses_and_has_required_resolve_fields():
    with open(_TEMPLATE, encoding="utf-8") as f:
        text = f.read()
    data = _try_yaml_load(text)
    if data is None:  # yaml not installed on this machine — degrade to a structural check
        for key in ("resolve:", "economic_keywords:", "leaf_model:", "deep_model:", "cache_dir:"):
            assert key in text, "template missing " + key
        return
    r = data["resolve"]
    assert isinstance(r["economic_keywords"], list) and r["economic_keywords"], "keywords must be a non-empty list"
    assert all(isinstance(k, str) for k in r["economic_keywords"])
    assert isinstance(r["leaf_model"], str) and r["leaf_model"]
    assert isinstance(r["deep_model"], str) and r["deep_model"]
    assert isinstance(r["cache_dir"], str) and r["cache_dir"]


def test_seeded_keywords_actually_flag_a_figure_and_an_economic_task():
    # acceptance leg 2: resolve_sweep run against the SEEDED defaults flags real tasks
    kws = srd.template_keywords(_TEMPLATE)
    assert kws, "must be able to extract the default keyword list"
    figure = {"id": "f1", "title": "Pay the property insurance $4,200"}
    keyword = {"id": "k1", "title": "Renew the mortgage note before it lapses"}
    neutral = {"id": "n1", "title": "Call mom about the weekend"}
    out = rs.sweep([figure, keyword, neutral], kws, set())
    ids = {f["id"] for f in out}
    assert "f1" in ids and "k1" in ids, "seeded defaults must flag figure + economic tasks"
    assert "n1" not in ids, "neutral task must not flag"


# --- the seeder tool: idempotent, Refresh-safe, absent-block degrades to a clean no-op --------

def _scratch_domains(body):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "domains.yaml")
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)
    return d, p


def test_seed_appends_block_to_a_fresh_domains_yaml():
    # acceptance leg 1: a fresh scaffolded domains.yaml gets a resolve: block
    d, p = _scratch_domains("brief:\n  trigger: wake up\n")
    try:
        changed = srd.seed(p, _TEMPLATE)
        assert changed is True
        with open(p, encoding="utf-8") as f:
            text = f.read()
        assert "\nresolve:" in text, "block must be appended at top level"
        assert "economic_keywords:" in text
        # appended content must not have merged onto the file's last line
        assert "wake upresolve" not in text and "wake up# " not in text
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_seed_is_idempotent_refresh_safe():
    # acceptance leg 3 (part a): a second run (Refresh) does NOT double-append; existing block wins
    d, p = _scratch_domains("resolve:\n  economic_keywords: [custom, tuned]\n")
    try:
        changed = srd.seed(p, _TEMPLATE)
        assert changed is False, "existing resolve: block must be preserved, not duplicated"
        with open(p, encoding="utf-8") as f:
            text = f.read()
        assert _resolve_key_lines(text) == 1
        assert "custom" in text, "the person's tuned keywords must survive a Refresh"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_absent_block_sweep_degrades_to_clean_noop():
    # acceptance leg 3 (part b): with NO resolve block (keywords absent), the sweep still runs —
    # figures flag via the money regex, keyword flagging goes quiet, nothing crashes.
    tasks = [
        {"id": "f1", "title": "Wire $9,000 to escrow"},
        {"id": "k1", "title": "Renew the mortgage"},   # economic word, but no keywords supplied
        {"id": "n1", "title": "Buy milk"},
    ]
    out = rs.sweep(tasks, None, set())          # economic_keywords absent
    ids = {f["id"] for f in out}
    assert ids == {"f1"}, "figure still flags; keyword-only + neutral tasks stay quiet"
    assert rs.sweep([], None, None) == []       # fully empty -> clean no-op, no crash


def test_seed_no_trailing_newline_does_not_merge_lines():
    # exercises the sep="\n" branch: a domains.yaml whose last line lacks a trailing newline must NOT
    # get the appended block glued onto it. (Previously every fixture ended in \n, so this branch was
    # never hit and the anti-merge assertions passed vacuously — the A31 vacuous-test trap.)
    d, p = _scratch_domains("brief:\n  trigger: go")   # NO trailing newline
    try:
        assert srd.seed(p, _TEMPLATE) is True
        with open(p, encoding="utf-8") as f:
            text = f.read()
        lines = text.splitlines()
        assert "  trigger: go" in lines, "original last line must survive intact"
        assert "go#" not in text and "goresolve" not in text, "block must not merge onto 'go'"
        assert _resolve_key_lines(text) == 1
        gi = lines.index("  trigger: go")
        assert lines[gi + 1].strip() == "" or lines[gi + 1].startswith("#"), lines[gi:gi + 3]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_template_keywords_returns_empty_when_marker_or_brackets_absent():
    # pins the parser's failure contract: no economic_keywords: marker, or a block/dashed list with no
    # inline [...], both yield [] (so a future template edit to that shape fails loud in the sweep test).
    d = tempfile.mkdtemp()
    try:
        p1 = os.path.join(d, "no_marker.yaml")
        with open(p1, "w", encoding="utf-8") as f:
            f.write("resolve:\n  leaf_model: x\n")
        assert srd.template_keywords(p1) == []
        p2 = os.path.join(d, "block_list.yaml")
        with open(p2, "w", encoding="utf-8") as f:
            f.write("resolve:\n  economic_keywords:\n    - insurance\n    - tax\n")
        assert srd.template_keywords(p2) == []
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_tool_cli_runs_and_is_idempotent():
    # acceptance: exit codes shown — the checked-in tool works from the command line
    d, p = _scratch_domains("brief:\n  trigger: go\n")
    try:
        r1 = subprocess.run([sys.executable, _TOOL, p], capture_output=True, text=True)
        assert r1.returncode == 0, r1.stderr
        assert "SEED" in r1.stdout.upper()
        r2 = subprocess.run([sys.executable, _TOOL, p], capture_output=True, text=True)
        assert r2.returncode == 0, r2.stderr
        assert "SKIP" in r2.stdout.upper(), "second run must be a no-op"
        with open(p, encoding="utf-8") as f:
            assert _resolve_key_lines(f.read()) == 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print("  ok  " + fn.__name__)
        except Exception:
            failed += 1; print(" FAIL " + fn.__name__); traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
