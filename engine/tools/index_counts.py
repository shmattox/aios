#!/usr/bin/env python3
"""index_counts.py — recompute a wiki `index.md`'s count frontmatter from disk (A7926).

The KB rule is "the index never goes stale", and env-ops H92 gave it teeth: a standing check reds
when `journal_count` drifts from the files under `journal/`. It had teeth but no hand — nothing in
the engine ever wrote those keys. `garden_hygiene` checks index *reachability*, and ingest ships
journal pages without touching the index, so the numbers were hand-written in a session months ago
and rotted from there (76 vs 88 on 2026-09-03). A check that can only ever go red is noise.

This is the hand. Deterministic, stdlib-only, zero-LLM:

  journal_count  files matching `journal/*.md`
  page_count     the same count summed over every immediate subdirectory of `wiki/` except the
                 structural ones (`staging/` by default) — an empty folder contributes 0 rather
                 than being special-cased, so adding a page to `people/` is picked up for free
  last_updated   the run date

It rewrites ONLY those three frontmatter lines. Everything else in the file — key order, spacing,
body prose, the trailing newline — is preserved byte for byte, because this runs unattended over a
human-authored catalog and a writer that reflows prose is a writer nobody dares schedule.

The body's `**Counts (as of …):** …= **N content pages**` sentence is refreshed too, but only its
NUMBERS, and only when it matches the expected shape exactly. Its editorial clauses are somebody's
words; a regenerated sentence would silently drop them. When the line does not match, it is left
alone and reported, never guessed at.

  python index_counts.py recompute --wiki <path/to/wiki> [--apply] [--today YYYY-MM-DD]

Prints a JSON summary. Without `--apply` it is a dry run and writes nothing. Exit 0 on success,
1 when the index is missing or has no frontmatter (fail loud — a silent no-op would let the check
stay red with nothing to explain it).
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from frontmatter import read_frontmatter          # noqa: E402  (path set above)

STRUCTURAL = ("staging",)                          # scaffolding, not content pages
COUNT_KEYS = ("journal_count", "page_count", "last_updated")

# `**Counts (as of 2026-07-21):** 76 journal + 31 entities + … = **165 content pages** (…)`
_COUNTS_RE = re.compile(
    r"(?P<head>\*\*Counts \(as of )(?P<date>\d{4}-\d{2}-\d{2})(?P<mid>\)[:*]*\*\*\s*)"
    r"(?P<sum>[0-9][^=]*?)(?P<eq>=\s*\*\*)(?P<total>[\d,]+)(?P<tail> content pages\*\*)")


def folder_counts(wiki, structural=STRUCTURAL):
    """{folder: n markdown pages} for every immediate subdirectory of `wiki`, structural excluded."""
    out = {}
    for name in sorted(os.listdir(wiki)):
        p = os.path.join(wiki, name)
        if not os.path.isdir(p) or name in structural or name.startswith("."):
            continue
        out[name] = len(glob.glob(os.path.join(p, "*.md")))
    return out


def compute(wiki, today=None, structural=STRUCTURAL):
    counts = folder_counts(wiki, structural)
    return {
        "journal_count": counts.get("journal", 0),
        "page_count": sum(counts.values()),
        "last_updated": today or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }, counts


def _rewrite_frontmatter(text, values):
    """Replace only the COUNT_KEYS lines inside the leading `---`…`---`. Keys absent from the block
    are NOT inserted — this reconciles an existing catalog, it does not invent a schema."""
    end = text.find("\n---", 3)
    if not text.startswith("---") or end == -1:
        return text, []
    head, rest = text[:end], text[end:]
    changed, lines = [], head.split("\n")
    for i, line in enumerate(lines):
        if line[:1] in (" ", "\t", "#") or ":" not in line:
            continue
        k = line.split(":", 1)[0].strip()
        if k in values and str(values[k]) != line.split(":", 1)[1].strip():
            lines[i] = "%s: %s" % (k, values[k])
            changed.append(k)
    return "\n".join(lines) + rest, changed


def _rewrite_counts_line(text, counts, today):
    """Refresh the numbers in the body Counts sentence, preserving its wording. Returns
    (text, refreshed: bool) — a line that does not match the expected shape is left untouched."""
    m = _COUNTS_RE.search(text)
    if not m:
        return text, False
    ordered = [("journal", counts.get("journal", 0))] + \
              [(k, v) for k, v in sorted(counts.items()) if k != "journal" and v]
    expr = " + ".join("%d %s" % (v, k) for k, v in ordered)
    new = "%s%s%s%s = **%d%s" % (m.group("head"), today, m.group("mid"), expr,
                                 sum(counts.values()), m.group("tail"))
    return text[:m.start()] + new + text[m.end():], True


def recompute(wiki, apply=False, today=None):
    index = os.path.join(wiki, "index.md")
    if not os.path.isfile(index):
        raise SystemExit("index_counts: no index.md under %s" % wiki)
    with open(index, encoding="utf-8") as fh:
        before = fh.read()
    if not read_frontmatter(before):
        raise SystemExit("index_counts: %s has no readable frontmatter — refusing to guess" % index)
    values, counts = compute(wiki, today)
    after, changed = _rewrite_frontmatter(before, values)
    after, line_ok = _rewrite_counts_line(after, counts, values["last_updated"])
    if apply and after != before:
        with open(index, "w", encoding="utf-8", newline="") as fh:
            fh.write(after)
    return {"ok": True, "index": index.replace(os.sep, "/"), "applied": bool(apply),
            "changed_keys": changed, "counts_line_refreshed": line_ok,
            "dirty": after != before, **values, "by_folder": counts}


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="op", required=True)
    r = sub.add_parser("recompute")
    r.add_argument("--wiki", required=True, help="path to the KB's wiki/ directory")
    r.add_argument("--apply", action="store_true", help="write; default is a dry run")
    r.add_argument("--today", default=None, help="override the run date (tests)")
    a = ap.parse_args(argv)
    print(json.dumps(recompute(a.wiki, a.apply, a.today), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
