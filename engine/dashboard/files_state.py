"""File browser + guarded editor backend for the dashboard.

Reads and (over)writes text files anywhere under the env root, with hard guards:
  - the resolved realpath must stay INSIDE the env root (symlink- and ..-safe);
  - noise/danger dirs (.git, node_modules, __pycache__, …) are never listed/opened/written;
  - secret files (.env*, private keys, certs, credentials) are never listed/opened/written;
  - binaries / non-utf-8 are refused (never shown or edited as text);
  - reads over 512 KB are refused (too big to edit sanely);
  - writes only OVERWRITE an existing regular file — never create, never follow a symlink.

Reads are best-effort (return an {"error": …} dict, never raise). Writes return
{"ok": bool, …}. Path containment is enforced by realpath comparison, not string prefixing
of the raw input, so encoded traversal and symlink escapes both fail closed."""

import io
import os

# directories never traversed — noise + danger (.git corruption, huge dep trees)
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
              ".pytest_cache", "dist", "build", ".next", ".turbo", "target", ".idea", ".vscode",
              ".gradle", ".cache"}
# files never read or written — secrets. Matched case-insensitively on the basename.
_SECRET_NAMES = {"credentials", "credentials.json", "id_rsa", "id_ed25519", ".netrc", ".htpasswd",
                 ".pgpass", ".dockercfg"}
_SECRET_SUFFIXES = (".key", ".pem", ".pfx", ".p12", ".keystore", ".crt", ".cer", ".jks")

_READ_MAX = 512 * 1024        # 512 KB — refuse to open bigger as editable text
_WRITE_MAX = 1024 * 1024      # 1 MB — also bounded by the POST body cap


def _is_secret(name):
    n = name.lower()
    return n.startswith(".env") or n in _SECRET_NAMES or n.endswith(_SECRET_SUFFIXES)


def _root(env_root):
    return os.path.realpath(env_root)


def _resolve(env_root, rel):
    """Resolve a caller-supplied path (relative to env root) to an absolute realpath that is
    provably INSIDE the root and not inside a skip dir / not a secret. Returns the abs path, or
    None if it escapes or is denied. rel '' -> the root itself."""
    root = _root(env_root)
    rel = (rel or "").replace("\\", "/").strip().lstrip("/")
    if rel in ("", "."):
        return root
    # cheap pre-checks before touching disk: no drive letters, no explicit parent hops
    if ":" in rel or rel == ".." or rel.startswith("../") or "/../" in rel or rel.endswith("/.."):
        return None
    target = os.path.realpath(os.path.join(root, rel))
    # realpath comparison (symlink/.. safe) — must be the root or strictly beneath it
    if target != root and not target.startswith(root + os.sep):
        return None
    relparts = os.path.relpath(target, root).replace("\\", "/").split("/")
    if any(p in _SKIP_DIRS for p in relparts):
        return None
    if _is_secret(relparts[-1]):
        return None
    return target


def _rel(root, p):
    return os.path.relpath(p, root).replace("\\", "/") if p != root else ""


def tree(env_root, rel):
    """List one directory (relative to env root). Skip dirs, secrets, and symlinks are omitted.
    Returns {"path": <reldir>, "entries": [{name, path, dir, size}]} or None (→ 404)."""
    d = _resolve(env_root, rel)
    if d is None or not os.path.isdir(d):
        return None
    root = _root(env_root)
    try:
        names = os.listdir(d)
    except OSError:
        return None
    entries = []
    for name in names:
        if name in _SKIP_DIRS or _is_secret(name):
            continue
        full = os.path.join(d, name)
        if os.path.islink(full):          # never surface symlinks (their target may be anywhere)
            continue
        try:
            isdir = os.path.isdir(full)
            size = 0 if isdir else os.path.getsize(full)
        except OSError:
            continue
        entries.append({"name": name, "path": _rel(root, full), "dir": isdir, "size": size})
    entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
    return {"path": _rel(root, d), "entries": entries}


def read(env_root, rel):
    """Read a text file. Returns {"path","content","size"} or {"error": …[, flags]}."""
    f = _resolve(env_root, rel)
    if f is None:
        return {"error": "path not allowed"}
    if os.path.islink(f) or not os.path.isfile(f):
        return {"error": "not a file"}
    try:
        size = os.path.getsize(f)
    except OSError:
        return {"error": "cannot stat file"}
    if size > _READ_MAX:
        return {"error": "file too large to edit", "size": size, "too_large": True}
    try:
        with io.open(f, "rb") as fh:
            raw = fh.read()
    except OSError:
        return {"error": "cannot read file"}
    if b"\x00" in raw:
        return {"error": "binary file — not editable here", "binary": True}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"error": "not utf-8 text — not editable here", "binary": True}
    return {"path": _rel(_root(env_root), f), "content": text, "size": size}


def write(env_root, rel, content):
    """Overwrite an existing text file. Returns {"ok": bool, …}. Edit-only: refuses to create a
    new file, write through a symlink, or exceed the size cap."""
    if not isinstance(content, str):
        return {"ok": False, "error": "content must be a string"}
    if len(content.encode("utf-8")) > _WRITE_MAX:
        return {"ok": False, "error": "content too large"}
    f = _resolve(env_root, rel)
    if f is None:
        return {"ok": False, "error": "path not allowed"}
    if os.path.islink(f):
        return {"ok": False, "error": "refusing to write through a symlink"}
    if not os.path.isfile(f):
        return {"ok": False, "error": "can only edit an existing file"}
    try:
        with io.open(f, "w", encoding="utf-8", newline="") as fh:   # preserve the file's own newlines
            fh.write(content)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "path": _rel(_root(env_root), f), "bytes": len(content.encode("utf-8"))}
