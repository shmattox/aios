#!/usr/bin/env python3
"""frontmatter.py — the ONE guarded flat-frontmatter reader for the aios engine tools.

A51 win (ii): capture / capture_router / sort / garden_distill each carried their own copy of the
"leading `---`…`---` top-level `key: value`" reader. Three were byte-identical; garden_distill's had
drifted — it crashed on `None`, mis-read nested keys, and skipped the quote-strip (the A49 minor).
Four copies is four chances to re-drift, so they share this one reader instead.

Contract (the A49-hardened shape — do not soften without updating test_frontmatter.py):
  - `None` / empty / no leading `---` / no closing `---`  -> `{}` (never raises)
  - only TOP-LEVEL scalar `key: value` lines are read; an indented/nested line (`  aliases: [...]`),
    a comment (`# ...`), a list item, or a key carrying an internal space is SKIPPED by its leading
    char / the space guard — so a nested block is never misread as a top-level key
  - the value is split on the FIRST colon (values may contain `:`, e.g. a URL) and quote-stripped
    (both `"` and `'`) — values stay raw strings otherwise (no type coercion, no pyyaml)

Stdlib only (matches the rest of the engine tools — portable to a stranger's python). Imported by
sibling scripts as `from frontmatter import read_frontmatter`; the running script's own directory is
first on sys.path, so this resolves ahead of any installed `python-frontmatter` package.
"""


def read_frontmatter(text):
    """Minimal leading `---`…`---` top-level `key: value` reader. See the module docstring for the
    exact (A49-hardened) contract. Returns a flat `{key: raw-string-value}` dict, `{}` on any
    missing/malformed block."""
    fm = {}
    if not text or not text.startswith("---"):
        return fm
    end = text.find("\n---", 3)
    if end == -1:
        return fm
    for line in text[3:end].splitlines():
        if line[:1] in (" ", "\t", "#") or ":" not in line:
            continue  # nested / comment / non-kv
        k, v = line.split(":", 1)
        if k.strip() and " " not in k.strip():
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm
