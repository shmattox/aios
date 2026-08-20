import os, sys, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "dashboard"))
import git_state as g

SAMPLE = """worktree /repo/aios
HEAD 1111111111111111111111111111111111111111
branch refs/heads/main

worktree /repo/aios/.worktrees/drain-A1
HEAD 2222222222222222222222222222222222222222
branch refs/heads/factory/A1
locked

worktree /repo/aios/.worktrees/detached
HEAD 3333333333333333333333333333333333333333
detached
"""


def test_parse_worktrees_fields_and_primary():
    wts = g._parse_worktrees(SAMPLE, "aios", "/repo/aios")
    assert len(wts) == 3
    main, drain, det = wts
    assert main["branch"] == "main" and main["primary"] is True and main["head"] == "11111111"
    assert drain["branch"] == "factory/A1" and drain["locked"] is True and drain["primary"] is False
    assert det["branch"] == "detached" and det["locked"] is False


def test_parse_worktrees_empty():
    assert g._parse_worktrees("", "x", "/x") == []


def test_worktrees_on_nongit_dir_never_raises(tmp_path):
    (tmp_path / "state").mkdir(); (tmp_path / "profile").mkdir()
    assert g.worktrees(str(tmp_path)) == []          # not a git repo -> empty, no raise


def test_prs_shape_and_background_refresh(tmp_path):
    (tmp_path / "state").mkdir(); (tmp_path / "profile").mkdir()
    g.reset_pr_cache()
    first = g.prs(str(tmp_path))
    assert set(first.keys()) == {"items", "loading"}
    assert first["items"] == [] and first["loading"] is True     # cold: kicked a bg refresh
    # the bg thread scans a tmp with no github remotes -> finishes fast, empty, loaded
    for _ in range(40):
        if not g.prs(str(tmp_path))["loading"]:
            break
        time.sleep(0.1)
    done = g.prs(str(tmp_path))
    assert done["loading"] is False and done["items"] == []


def test_prs_thread_start_failure_never_raises_and_resets_flag(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir(); (tmp_path / "profile").mkdir()
    g.reset_pr_cache()

    class BoomThread:
        def __init__(self, *a, **k): pass
        def start(self): raise RuntimeError("can't start new thread")

    monkeypatch.setattr(g.threading, "Thread", BoomThread)
    r = g.prs(str(tmp_path))                       # must not raise
    assert r == {"items": [], "loading": True}
    assert g._PR_CACHE["refreshing"] is False      # flag reset -> a later call retries


def test_parse_vault_fields_and_auto_flag():
    raw = "aaa111\x001787234808\x00PlaceSchema vault reconcile — marketing sweep\n" \
          "bbb222\x001787224505\x00auto: sync (machine)\n" \
          "ccc333\x001787233227\x00chore(env-sync): flush 3 edits\n" \
          "malformed-line-no-nuls"
    v = g._parse_vault(raw)
    assert len(v) == 3
    assert v[0]["hash"] == "aaa111" and v[0]["ts"] == 1787234808 and v[0]["auto"] is False
    assert v[1]["auto"] is True and v[2]["auto"] is True   # auto: / chore(env-sync) = machinery


def test_vault_writes_on_nongit_never_raises(tmp_path):
    (tmp_path / "state").mkdir(); (tmp_path / "profile").mkdir()
    assert g.vault_writes(str(tmp_path)) == []      # no SecondBrain repo -> empty, no raise


def test_state_keys(tmp_path):
    (tmp_path / "state").mkdir(); (tmp_path / "profile").mkdir()
    g.reset_pr_cache()
    st = g.state(str(tmp_path))
    assert set(st.keys()) == {"worktrees", "prs", "vault"}
    assert isinstance(st["worktrees"], list) and set(st["prs"].keys()) == {"items", "loading"}
    assert isinstance(st["vault"], list)
