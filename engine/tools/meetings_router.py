#!/usr/bin/env python3
"""meetings_router.py — route Granola meeting notes from a drop-zone to per-silo homes.

Fact-free: drop-zone, dest-root, the folder->dest map, the default dest, and the log dir all
arrive as CLI args. No hardcoded paths, silo names, or Granola folder names. Stdlib only.

Each meeting is a note (type: note) + transcript (type: transcript) the Granola Obsidian plugin
wrote, sharing a `granola_id`. The note carries `folders:` (the Granola folder); the transcript
often does NOT. This tool reads each file's `folders:` leaf and maps it to a destination relpath
under dest-root; a file with no mapped folder of its own inherits the dest of the OTHER file
sharing its `granola_id` (its paired note), falling back to the default only if neither has a
mapped folder. It then relocates the file (copy -> verify -> remove-source), preserving the
drop-zone's relative year/month path, so note and transcript always co-locate. Deterministic
dest path => a re-dropped file overwrites in place (idempotent, never duplicated).

  python meetings_router.py --drop-zone <dir> --dest-root <dir> --map <json> --default <relpath> \
    --log-dir <dir> [--dry-run]
"""
import argparse
import json
import shutil
import sys
from pathlib import Path


def _unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def read_frontmatter(text):
    """Scalars -> str; a bare `key:` followed by `  - item` lines -> list[str]. Stdlib subset."""
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}
    fm = {}
    list_key = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        stripped = raw.strip()
        if stripped.startswith("- ") and list_key is not None:
            fm[list_key].append(_unquote(stripped[2:]))
            continue
        if ":" not in raw:
            continue
        key, _, val = raw.partition(":")
        key = key.strip()
        val = val.strip()
        if val == "":
            fm[key] = []
            list_key = key
        else:
            fm[key] = _unquote(val)
            list_key = None
    return fm


def route_one(fm, dest_map, default, id_dest=None):
    """Return the dest relpath (under dest-root) for a file. Never None.
    Precedence: own mapped `folders:` leaf -> inherited dest of the paired file sharing
    `granola_id` (id_dest) -> default."""
    folders = fm.get("folders")
    if isinstance(folders, list) and folders:
        leaf = folders[0].split("/")[-1].strip()
        if leaf in dest_map:
            return dest_map[leaf]
    if id_dest:
        gid = fm.get("granola_id")
        if gid in id_dest:
            return id_dest[gid]
    return default


def _own_dest(fm, dest_map):
    """The dest this file's OWN `folders:` leaf maps to, or None if absent/unmapped."""
    folders = fm.get("folders")
    if isinstance(folders, list) and folders:
        leaf = folders[0].split("/")[-1].strip()
        if leaf in dest_map:
            return dest_map[leaf]
    return None


def route(drop_zone, dest_root, dest_map, default, log_dir, dry_run):
    drop_zone = Path(drop_zone)
    dest_root = Path(dest_root)
    moved, defaulted, errors = [], [], []

    # --- pass 1: read + cache frontmatter for every file; build granola_id -> dest ---
    cache = {}                                       # rel (Path) -> fm dict
    id_dest = {}                                     # granola_id -> mapped dest_rel
    for src in sorted(drop_zone.rglob("*.md")):
        rel = src.relative_to(drop_zone)              # e.g. 2026/07/Deal Sync.md
        try:
            fm = read_frontmatter(src.read_text(encoding="utf-8"))
        except Exception as exc:                       # unreadable/mid-write -> skip this run
            errors.append(f"{rel}: {exc}")
            continue
        cache[rel] = fm
        own = _own_dest(fm, dest_map)
        gid = fm.get("granola_id")
        if own is not None and gid:
            id_dest[gid] = own

    # --- pass 2: resolve each file's dest (own -> inherited -> default) and relocate ---
    for rel, fm in cache.items():
        src = drop_zone / rel
        own = _own_dest(fm, dest_map)
        dest_rel = route_one(fm, dest_map, default, id_dest=id_dest)
        if own is None and id_dest.get(fm.get("granola_id")) is None:
            defaulted.append(str(rel))
        dest = dest_root / dest_rel / rel
        rec = {"src": str(rel), "dest": str(dest.relative_to(dest_root)),
               "folder": (fm.get("folders") or [None])[0], "granola_id": fm.get("granola_id"),
               "type": fm.get("type")}
        if dry_run:
            moved.append({**rec, "action": "would-move"})
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)                     # copy -> verify -> remove-source
        except Exception as exc:                         # e.g. permission/locked file -> skip, keep batch alive
            errors.append(f"{rel}: {exc}")
            continue
        if dest.is_file() and dest.stat().st_size == src.stat().st_size:
            src.unlink()
            moved.append({**rec, "action": "moved"})
        else:
            errors.append(f"{rel}: copy verify failed")
    # prune empty dirs left in the drop-zone (leave the drop-zone root)
    if not dry_run:
        for p in sorted(drop_zone.rglob("*"), reverse=True):
            if p.is_dir() and not any(p.iterdir()):
                p.rmdir()
    summary = {"moved": len([m for m in moved if m.get("action") == "moved"]),
               "would_move": len([m for m in moved if m.get("action") == "would-move"]),
               "defaulted": len(defaulted), "errors": errors, "items": moved}
    if log_dir:
        ld = Path(log_dir)
        ld.mkdir(parents=True, exist_ok=True)
        (ld / "last-run.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"meetings-router: {summary['moved']} moved, {summary['would_move']} would-move, "
          f"{summary['defaulted']} defaulted-to-default, {len(errors)} errors")
    for e in errors:
        print(f"  ERROR {e}")
    return summary


def main(argv):
    ap = argparse.ArgumentParser(prog="meetings_router.py")
    ap.add_argument("--drop-zone", required=True)
    ap.add_argument("--dest-root", required=True)
    ap.add_argument("--map", required=True, help="JSON: {granola-folder-leaf: relpath-under-dest-root}")
    ap.add_argument("--default", required=True)
    ap.add_argument("--log-dir", default="")
    ap.add_argument("--dry-run", action="store_true")
    try:
        a = ap.parse_args(argv)
        dest_map = json.loads(a.map)
    except SystemExit:
        return 2
    except json.JSONDecodeError as exc:
        print(f"usage error: --map is not valid JSON ({exc})")
        return 2
    route(a.drop_zone, a.dest_root, dest_map, a.default, a.log_dir, a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
