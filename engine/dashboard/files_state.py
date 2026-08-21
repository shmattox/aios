"""Read-only file browser backend for the dashboard.

Lists and reads text files anywhere under the env root, with hard guards:
  - the resolved realpath must stay INSIDE the env root (symlink- and ..-safe);
  - noise/danger dirs (.git, node_modules, __pycache__, …) are never listed/opened;
  - secret files (.env*, private keys, certs, credentials) are never listed/opened;
  - binaries / non-utf-8 are refused (never shown as text);
  - reads over 512 KB are refused (too big to view sanely).

Reads are best-effort (return an {"error": …} dict, never raise). Path containment is
enforced by realpath comparison, not string prefixing of the raw input, so encoded
traversal and symlink escapes both fail closed."""

import io
import os

# directories never traversed — noise + danger (.git corruption, huge dep trees, credential stores).
# Compared case-INSENSITIVELY (lowercased at match time) so `.GIT` can't slip through on a
# case-insensitive filesystem whose realpath doesn't fold case (macOS APFS).
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
              ".pytest_cache", "dist", "build", ".next", ".turbo", "target", ".idea", ".vscode",
              ".gradle", ".cache", ".ssh", ".aws", ".gnupg", ".docker", ".azure", ".kube"}
# files never read or written — secrets. Matched case-insensitively on EVERY path segment (not
# just the basename), so `credentials/prod.json` is refused as well as a file named `credentials`.
_SECRET_NAMES = {"credentials", "credentials.json", "credentials.yaml", "credentials.yml",
                 "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", ".netrc", ".htpasswd", ".pgpass",
                 ".dockercfg", ".npmrc", ".pypirc", "known_hosts", ".git-credentials"}
_SECRET_SUFFIXES = (".key", ".pem", ".pfx", ".p12", ".keystore", ".crt", ".cer", ".jks")

_READ_MAX = 512 * 1024        # 512 KB — refuse to open bigger as editable text


def _is_secret(name):
    n = name.lower()
    return (n.startswith(".env") or n.startswith("secrets.")    # secrets.json/.yaml/.yml/.env…
            or n in _SECRET_NAMES or n.endswith(_SECRET_SUFFIXES))


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
    if os.path.islink(os.path.join(root, rel)):     # refuse a symlinked final component outright
        return None
    target = os.path.realpath(os.path.join(root, rel))
    # realpath comparison (symlink/.. safe) — must be the root or strictly beneath it
    if target != root and not target.startswith(root + os.sep):
        return None
    relparts = os.path.relpath(target, root).replace("\\", "/").split("/")
    if any(p.lower() in _SKIP_DIRS for p in relparts):    # case-insensitive (.GIT on macOS)
        return None
    if any(_is_secret(p) for p in relparts):              # a secret at ANY depth, not just basename
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


def search(env_root, q, limit=250):
    """Recursive filename search under the env root (skip/secret dirs pruned). Returns matching
    env-relative file paths, newest-scanned first is not guaranteed — order is walk order."""
    q = (q or "").strip().lower()
    if len(q) < 2:
        return {"query": q, "results": []}
    root = _root(env_root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in _SKIP_DIRS and not _is_secret(d)
                       and not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            if _is_secret(name) or q not in name.lower():
                continue
            out.append(_rel(root, os.path.join(dirpath, name)))
            if len(out) >= limit:
                return {"query": q, "results": out, "truncated": True}
    return {"query": q, "results": out}
