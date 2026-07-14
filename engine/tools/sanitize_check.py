#!/usr/bin/env python3
"""sanitize_check.py — pre-tag/pre-release static scan for instance-identifier leaks.

AIOS ships as a PUBLIC plugin; a personal install's operational identifiers (Open-Item ids,
Family-Office synthetic ids, entity names) must never ride into the public repo's tracked text.
The 2026-07-07 history scrub and the A42 pass were one-off manual sweeps; this is the durable,
tested guard that keeps the class from recurring (BACKLOG A61). Run it before tagging a release.

Two tiers of pattern:

  1. STRUCTURAL (defined here, instance-agnostic) — ID *formats*, not names, so they are safe to
     live in the public repo and catch the highest-signal leak vector:
       - OI-<digits>[suffix]   an Open-Item id (the generic placeholder ``OI-N`` is NOT matched)
       - FO-<ALNUM..>          a synthetic silo id (case-insensitive; e.g. a tax/refi item id)
     Matching is case-insensitive and tolerates alphanumeric suffixes so a trivially-reworded id
     (lowercase, ``-2`` suffix) still trips the guard.

  2. INSTANCE (optional, NEVER shipped here) — entity NAMES are install-specific and would
     themselves be a leak if hardcoded in this public file, so they load from an out-of-repo
     patterns file (``--patterns`` or ``<env_root>/state/sanitize-patterns.txt``): one regex per
     line, ``#`` comments and blank lines ignored.

Fail-soft vs fail-loud: an AUTO-discovered instance file that is absent -> structural-only (soft).
An EXPLICIT ``--patterns`` file that is missing -> hard error (the operator asked to scan names).
Zero files scanned is a hard error, never a false "clean".

Scope boundary: this guard covers instance IDs + names. Economic FIGURES (the other historical leak
class) are covered at runtime by the a35 economic-header sweep, not here.

CLI: ``python sanitize_check.py [files...] [--patterns FILE] [--root DIR]``
Exit 0 = clean, 1 = at least one match (prints ``path:line: [pattern] match``), 2 = usage error.
"""
import argparse
import os
import re
import sys

# Instance-agnostic ID-FORMAT patterns — safe to ship in the public repo (formats, not names).
# Case-insensitive + alphanumeric-suffix tolerant so reworded ids (lowercase, -2 suffix) still trip.
STRUCTURAL = [
    ("open-item-id", re.compile(r"\bOI-\d+[A-Za-z0-9]*\b", re.IGNORECASE)),
    ("synthetic-silo-id", re.compile(r"\bFO-[A-Z0-9]{2,}\b", re.IGNORECASE)),
    # EIN (employer id number) format dd-ddddddd — a tax id is high-signal PII; the format is
    # instance-agnostic so it ships publicly. A synthetic placeholder uses letters (XX-XXXXXXX),
    # which does not match, the same way the generic OI-N placeholder escapes the open-item pattern.
    # Leading (?<!\d) instead of \b so a label glued straight onto the digits (EIN + the number, no
    # space) is still caught — \b fails there since letter->digit is word-to-word. Trailing \b (NOT
    # a lookahead) is kept so a digit-then-letter run like a "...-06-21-7805551e" filename hash and a
    # longer digit run are both rejected.
    ("ein", re.compile(r"(?<!\d)\d{2}-\d{7}\b")),
]

DEFAULT_INSTANCE_PATTERNS = os.path.join("state", "sanitize-patterns.txt")


def structural_patterns():
    """The shipped, instance-agnostic id-format patterns as [(name, compiled_regex)]."""
    return list(STRUCTURAL)


def load_instance_patterns(path):
    """Read an out-of-repo patterns file: one regex per line, '#' comments + blanks ignored.

    Returns [(name, compiled_regex)]; [] if `path` is falsy or absent (fail-soft -> structural-only).
    A malformed regex raises ValueError (fail loud — a broken guard must not silently pass)."""
    pats = []
    if not path or not os.path.isfile(path):
        return pats
    with open(path, encoding="utf-8") as f:
        for i, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                pats.append(("instance:" + line, re.compile(line)))
            except re.error as e:
                raise ValueError("%s:%d: bad regex %r: %s" % (path, i, line, e))
    return pats


def resolve_env_root(start):
    """Walk up from `start` for the first dir holding BOTH state/ and profile/ (the env_root).

    Returns the absolute path, or None if the filesystem root is reached without finding it."""
    cur = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(cur, "state")) and os.path.isdir(os.path.join(cur, "profile")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def resolve_instance_path(explicit, root):
    """Resolve the instance patterns file. Returns (path_or_None, must_exist).

    An explicit --patterns value MUST exist (must_exist=True -> caller fails loud if absent).
    The auto-discovered <env_root>/state/sanitize-patterns.txt is optional (must_exist=False)."""
    if explicit:
        return explicit, True
    env = resolve_env_root(root)
    if env:
        return os.path.join(env, DEFAULT_INSTANCE_PATTERNS), False
    return None, False


def scan_text(text, patterns):
    """Return [(lineno, match_text, pattern_name)] for every pattern hit, in file order."""
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for name, rx in patterns:
            for m in rx.finditer(line):
                out.append((lineno, m.group(0), name))
    return out


def scan_file(path, patterns):
    """scan_text over a file's contents."""
    with open(path, encoding="utf-8", errors="replace") as f:
        return scan_text(f.read(), patterns)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Scan files for instance-identifier leaks (pre-tag release guard).")
    ap.add_argument("files", nargs="*", help="files to scan (default: ./BACKLOG.md if present)")
    ap.add_argument("--patterns", help="out-of-repo instance patterns file (entity names)")
    ap.add_argument("--root", default=".", help="dir to resolve default target + env_root from")
    args = ap.parse_args(argv)

    patterns = structural_patterns()
    pat_path, must_exist = resolve_instance_path(args.patterns, args.root)
    if pat_path and must_exist and not os.path.isfile(pat_path):
        # explicit --patterns that doesn't exist: fail loud, don't silently drop the name tier
        print("error: --patterns file not found: %s" % pat_path, file=sys.stderr)
        return 2
    patterns += load_instance_patterns(pat_path)

    files = list(args.files)
    if not files:
        bl = os.path.join(args.root, "BACKLOG.md")
        if os.path.isfile(bl):
            files = [bl]
    if not files:
        # a guard that scanned nothing must never report "clean"
        print("error: no files to scan (pass files, or run where ./BACKLOG.md exists)", file=sys.stderr)
        return 2

    findings = []
    for p in files:
        for lineno, match, name in scan_file(p, patterns):
            findings.append((p, lineno, match, name))
            print("%s:%d: [%s] %s" % (p, lineno, name, match))
    if findings:
        print("\n%d instance-identifier leak(s) found — scrub before tagging." % len(findings),
              file=sys.stderr)
        return 1
    print("clean — no instance-identifier leaks in %d file(s)." % len(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
