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


def test_read_refuses_secret_at_any_depth(tmp_path):
    env = _env(tmp_path)
    (tmp_path / "credentials").mkdir()                            # a secret-NAMED directory
    (tmp_path / "credentials" / "prod.json").write_bytes(b"{}\n")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / ".npmrc").write_bytes(b"registry=x\n")
    (tmp_path / "app" / "id_ecdsa").write_bytes(b"key\n")
    assert f.read(env, "credentials/prod.json").get("error")      # secret at a parent segment
    assert f.read(env, "app/.npmrc").get("error")                 # expanded denylist
    assert f.read(env, "app/id_ecdsa").get("error")               # more key names


def test_read_too_large_refused(tmp_path):
    env = _env(tmp_path)
    big = tmp_path / "big.txt"
    big.write_text("x" * (600 * 1024), encoding="utf-8")
    assert f.read(env, "big.txt").get("too_large") is True


# ---- read-only contract --------------------------------------------------
def test_module_is_read_only():
    assert not hasattr(f, "write") and not hasattr(f, "create")


def test_search_finds_files_and_skips_secrets(tmp_path):
    env = _env(tmp_path)
    r = f.search(env, "notes")
    assert "notes.txt" in r["results"]
    r2 = f.search(env, "env")               # .env is secret — must not surface
    assert not any(x.endswith(".env") for x in r2["results"])
    assert f.search(env, "a")["results"] == []   # <2 chars → no-op


