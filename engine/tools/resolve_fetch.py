#!/usr/bin/env python3
"""resolve_fetch.py — read an entity's `links:` crosswalk into evidence CANDIDATES.

The top-level frontmatter reader in the engine (capture_router.read_frontmatter) deliberately skips
nested blocks; the crosswalk IS nested (a mapping of lists), so this tool parses the `links:` block
directly. It enumerates candidates only — it does NOT pick the governing doc (model relevance) or
extract figures (model). Stdlib-only, fact-free.
"""
import argparse, csv, json, sys

SEP = " — "   # em dash separating "ref — human description" in each crosswalk entry


def _unquote(s):
    s = s.strip()
    if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
        return s[1:-1]
    return s


def read_links_block(text):
    """Parse the entity's `links:` frontmatter sub-block into {subkey: [items]}.
    Handles block lists (indented `- item`) and inline lists (`key: [a, b]`). {} if absent."""
    if not text or not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out, in_links, cur = {}, False, None
    for line in text[3:end].splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_links = (stripped.endswith(":") and stripped[:-1].strip() == "links")
            cur = None
            continue
        if not in_links:
            continue
        if stripped.startswith("- "):
            if cur is not None:
                out.setdefault(cur, []).append(_unquote(stripped[2:]))
            continue
        if ":" in stripped:
            k, v = stripped.split(":", 1)
            k, v = k.strip(), v.strip()
            if v.startswith("[") and v.endswith("]"):
                # quote-aware split so a comma INSIDE a quoted description doesn't split the entry
                items = next(csv.reader([v[1:-1]], skipinitialspace=True), [])
                out[k] = [_unquote(x) for x in items if x.strip()]
                cur = None
            elif v == "":
                out.setdefault(k, [])
                cur = k
            else:
                out[k] = [_unquote(v)]
                cur = None
    return out


def _top_level_aliases(text):
    """Read an Obsidian-native TOP-LEVEL `aliases:` frontmatter key (inline `[a, b]` or a block list
    of `- a`). Column-0 only, so a nested `links.aliases` (read by read_links_block) is not double-read.
    A39: the entity page used to carry a DUPLICATE `links.aliases` purely to satisfy candidates_for; read
    the native top-level key too so that duplicate can be dropped."""
    if not text or not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    if end == -1:
        return []
    out, in_aliases = [], False
    for line in text[3:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1] not in (" ", "\t"):                       # a top-level (column-0) line
            stripped = line.strip()
            if stripped.startswith("aliases:"):
                v = stripped[len("aliases:"):].strip()
                if v.startswith("[") and v.endswith("]"):
                    items = next(csv.reader([v[1:-1]], skipinitialspace=True), [])
                    return [_unquote(x) for x in items if x.strip()]
                in_aliases = (v == "")                         # a block list follows on indented lines
            else:
                in_aliases = False
        elif in_aliases and line.lstrip().startswith("- "):
            out.append(_unquote(line.lstrip()[2:]))
    return out


def candidates_for(entity_text):
    """Entity page text -> {has_crosswalk, aliases[], candidates[{source,ref,desc}]}. Aliases are the
    UNION of the nested `links.aliases` block and the Obsidian-native top-level `aliases:` key (A39),
    order-preserving and de-duplicated."""
    blk = read_links_block(entity_text)
    block_aliases = blk.pop("aliases", [])
    seen, aliases = set(), []
    for a in list(block_aliases) + _top_level_aliases(entity_text):
        if a not in seen:
            seen.add(a)
            aliases.append(a)
    candidates = []
    for source, items in (blk or {}).items():
        for item in items:
            ref, _, desc = item.partition(SEP)
            candidates.append({"source": source, "ref": ref.strip(), "desc": desc.strip()})
    return {"has_crosswalk": bool(candidates), "aliases": aliases, "candidates": candidates}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read an entity crosswalk into evidence candidates")
    ap.add_argument("entity_path", help="path to the entity .md page")
    args = ap.parse_args(argv)
    with open(args.entity_path, encoding="utf-8") as f:
        print(json.dumps(candidates_for(f.read()), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
