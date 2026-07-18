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

# Inline allowlist marker (A79): a line carrying this literal (in a comment) is exempt — the repo
# legitimately holds anonymized example ids in reference docs/tests. Deliberately NEVER honored
# for the EIN pattern: a real tax-id format is never an acceptable example (use XX-XXXXXXX).
ALLOW_MARKER = "sanitize:allow"
# File-level form (A79 §2): in the first N lines, exempts the whole file (fixture-dense tests,
# archived example docs). Neither form ever exempts the EIN pattern.
ALLOW_FILE_MARKER = ALLOW_MARKER + "-file"
_ALLOW_FILE_HEAD_LINES = 10
_BINARY_SNIFF_BYTES = 8192


def _file_allowed(text):
    """True when the first _ALLOW_FILE_HEAD_LINES lines carry the file-level allow marker."""
    head = text.splitlines()[:_ALLOW_FILE_HEAD_LINES]
    return any(ALLOW_FILE_MARKER in line for line in head)


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
    """Return [(lineno, match_text, pattern_name)] for every pattern hit, in file order.

    A line carrying ALLOW_MARKER is exempt for every pattern EXCEPT "ein" (A79 §2)."""
    out = []
    file_allowed = _file_allowed(text)
    for lineno, line in enumerate(text.splitlines(), 1):
        allowed = file_allowed or ALLOW_MARKER in line
        for name, rx in patterns:
            if allowed and name != "ein":
                continue
            for m in rx.finditer(line):
                out.append((lineno, m.group(0), name))
    return out


# ── A79: tree + history tiers ──────────────────────────────────────────────────────────────────
# Both reuse the SAME compiled patterns as the file tier — no git-regex translation layer (the
# 2026-07-15 incident traps all came from -S/-G quirks). Fail-loud rules: any git failure or a
# scan that inspected nothing is an ERROR, never "clean".

class ScanError(Exception):
    """A guard failure that must surface as exit 2 — never as a false 'clean'."""


def _git(root, *args):
    """Run git in `root`; raise ScanError on any failure (a broken guard must not pass)."""
    import subprocess
    r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise ScanError("git %s failed (%d): %s" % (" ".join(args), r.returncode,
                                                    (r.stderr or r.stdout).strip()[:400]))
    return r.stdout


def _is_binary(path):
    """Null-byte sniff on the first _BINARY_SNIFF_BYTES."""
    try:
        with open(path, "rb") as f:
            return b"\x00" in f.read(_BINARY_SNIFF_BYTES)
    except OSError:
        return True   # unreadable -> skip rather than crash; the tracked list is git's, not ours


def scan_tree(root, patterns):
    """Scan every git-TRACKED text file under `root` (the actual published surface).

    Returns (findings, files_scanned) where findings = [(relpath, lineno, match, name)].
    Raises ScanError when git fails or zero files are tracked (never a false 'clean')."""
    out = _git(root, "ls-files", "-z")
    files = [f for f in out.split("\0") if f]
    if not files:
        raise ScanError("zero tracked files — scanned nothing, refusing to report clean")
    findings, scanned = [], 0
    for rel in files:
        path = os.path.join(root, rel)
        # a tracked file absent from the working tree (staged delete) has no bytes to scan here;
        # its committed content is the --history tier's job
        if not os.path.isfile(path) or _is_binary(path):
            continue
        scanned += 1
        for lineno, match, name in scan_file(path, patterns):
            findings.append((rel, lineno, match, name))
    return findings, scanned


def scan_history(root, range_spec, patterns):
    """Scan the ADDED lines of every commit in `range_spec` (e.g. 'A..B', a sha, or '--all').

    Streams one `git log -p` pass and applies the SAME compiled patterns as the file tier.
    Returns (findings, commits, added_lines) with findings = [(sha7, file, match, name)].
    Raises ScanError on git failure or when zero commits were scanned (empty range)."""
    # --end-of-options stops a leading-dash range being parsed as a git option (argument-injection
    # hardening); it cannot wrap the literal --all mode, which IS an option by design.
    range_args = ["--all"] if range_spec == "--all" else ["--end-of-options", range_spec]
    out = _git(root, "log", "-p", "--no-color", "--format=commit %H", *range_args)
    findings, commits, added = [], 0, 0
    sha7, cur_file = "?", "?"
    allowed_files = {}   # relpath -> file-level marker in the CURRENT tree (a reviewed declaration)

    def _cur_file_allowed(rel):
        if rel not in allowed_files:
            p = os.path.join(root, rel)
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    allowed_files[rel] = _file_allowed(f.read())
            except OSError:
                allowed_files[rel] = False   # absent from the tree -> no exemption
        return allowed_files[rel]

    for line in out.splitlines():
        if line.startswith("commit ") and len(line) >= 47 and " " not in line[7:47]:
            sha7, cur_file = line[7:14], "?"
            commits += 1
        elif line.startswith("+++ "):
            target = line[4:]
            cur_file = target[2:] if target.startswith("b/") else target
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
            for _, match, name in scan_text(line[1:], patterns):
                if name != "ein" and cur_file != "?" and _cur_file_allowed(cur_file):
                    continue
                findings.append((sha7, cur_file, match, name))
    if commits == 0:
        raise ScanError("scanned 0 commits for range %r — refusing to report clean" % range_spec)
    return findings, commits, added


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
    ap.add_argument("--tree", action="store_true",
                    help="scan every git-TRACKED text file under --root (A79)")
    ap.add_argument("--history", metavar="RANGE",
                    help="scan ADDED lines of commits in RANGE (e.g. A..B, a sha, or --history=--all)")
    args = ap.parse_args(argv)

    patterns = structural_patterns()
    pat_path, must_exist = resolve_instance_path(args.patterns, args.root)
    if pat_path and must_exist and not os.path.isfile(pat_path):
        # explicit --patterns that doesn't exist: fail loud, don't silently drop the name tier
        print("error: --patterns file not found: %s" % pat_path, file=sys.stderr)
        return 2
    patterns += load_instance_patterns(pat_path)

    modes = sum([bool(args.tree), bool(args.history), bool(args.files)])
    if modes > 1:
        print("error: pass files OR --tree OR --history, not a combination", file=sys.stderr)
        return 2

    if args.tree:
        try:
            findings, scanned = scan_tree(args.root, patterns)
        except ScanError as e:
            print("error: %s" % e, file=sys.stderr)
            return 2
        for rel, lineno, match, name in findings:
            print("%s:%d: [%s] %s" % (rel, lineno, name, match))
        if findings:
            print("\n%d instance-identifier leak(s) in the tracked tree — scrub before pushing."
                  % len(findings), file=sys.stderr)
            return 1
        print("clean — no instance-identifier leaks in %d tracked file(s)." % scanned)
        return 0

    if args.history:
        try:
            findings, commits, added = scan_history(args.root, args.history, patterns)
        except ScanError as e:
            print("error: %s" % e, file=sys.stderr)
            return 2
        for sha7, fname, match, name in findings:
            print("%s:%s: [%s] %s" % (sha7, fname, name, match))
        summary = "scanned %d commit(s) / %d added line(s)" % (commits, added)
        if findings:
            print("\n%s — %d leak(s) in committed history (tree-clean does NOT clear this; "
                  "a purge or history rewrite is required)." % (summary, len(findings)),
                  file=sys.stderr)
            return 1
        print("%s — clean." % summary)
        return 0

    files = list(args.files)
    if not files:
        bl = os.path.join(args.root, "BACKLOG.md")
        if os.path.isfile(bl):
            files = [bl]
            print("note: default single-file mode — prefer --tree to scan the whole tracked "
                  "surface (A79)", file=sys.stderr)
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
