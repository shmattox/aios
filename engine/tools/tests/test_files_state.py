import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "dashboard"))
import files_state as f


def _env(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.md").write_bytes(b"hello\nworld\n")   # LF exactly (no CRLF translation)
    (tmp_path / "notes.txt").write_bytes(b"top\n")
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (tmp_path / "server.key").write_text("-----KEY-----\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("x\n", encoding="utf-8")
    (tmp_path / "pic.png").write_bytes(b"\x89PNG\x00\x00binary")
    return str(tmp_path)


# ---- tree ---------------------------------------------------------------
def test_tree_lists_dirs_first_and_hides_secrets_and_noise(tmp_path):
    env = _env(tmp_path)
    t = f.tree(env, "")
    names = [e["name"] for e in t["entries"]]
    assert names[0] == "sub" and "notes.txt" in names             # dirs first
    assert ".env" not in names and "server.key" not in names      # secrets hidden
    assert ".git" not in names and "node_modules" not in names    # noise hidden
    assert "pic.png" in names                                     # binaries listed, just not editable


def test_tree_unknown_and_escape_return_none(tmp_path):
    env = _env(tmp_path)
    assert f.tree(env, "nope") is None
    assert f.tree(env, "../..") is None
    assert f.tree(env, ".git") is None                            # skip dir not browsable


# ---- read ---------------------------------------------------------------
def test_read_text_ok(tmp_path):
    env = _env(tmp_path)
    d = f.read(env, "sub/a.md")
    assert d["content"] == "hello\nworld\n" and d["path"] == "sub/a.md"


def test_read_refuses_secret_binary_and_escape(tmp_path):
    env = _env(tmp_path)
    assert f.read(env, ".env").get("error")                       # secret
    assert f.read(env, "server.key").get("error")                 # key
    assert f.read(env, ".git/config").get("error")                # inside skip dir
    assert f.read(env, "pic.png").get("binary") is True           # binary refused
    assert f.read(env, "../../etc/passwd").get("error")           # traversal
    assert f.read(env, "missing.md").get("error")                 # not a file


def test_read_too_large_refused(tmp_path):
    env = _env(tmp_path)
    big = tmp_path / "big.txt"
    big.write_text("x" * (600 * 1024), encoding="utf-8")
    assert f.read(env, "big.txt").get("too_large") is True


# ---- write --------------------------------------------------------------
def test_write_overwrites_existing(tmp_path):
    env = _env(tmp_path)
    r = f.write(env, "notes.txt", "changed\n")
    assert r["ok"] is True
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "changed\n"


def test_write_refuses_create_secret_escape_and_oversize(tmp_path):
    env = _env(tmp_path)
    assert f.write(env, "brand-new.md", "x").get("ok") is False   # edit-only, no create
    assert not (tmp_path / "brand-new.md").exists()
    assert f.write(env, ".env", "PWNED=1").get("ok") is False     # secret
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "SECRET=1\n"   # untouched
    assert f.write(env, ".git/config", "x").get("ok") is False    # skip dir
    assert f.write(env, "../escape.md", "x").get("ok") is False   # traversal
    assert f.write(env, "notes.txt", "y" * (2 * 1024 * 1024)).get("ok") is False  # oversize


def test_write_refuses_symlink_escape(tmp_path):
    env = _env(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        return   # no symlink privilege (Windows without dev mode) — nothing to test
    assert f.write(env, "link.txt", "PWNED").get("ok") is False
    assert outside.read_text(encoding="utf-8") == "outside\n"     # target untouched
    assert f.read(env, "link.txt").get("error")                  # read through symlink refused too
