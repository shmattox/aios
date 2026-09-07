#!/usr/bin/env python3
"""notion_publish.py — C1: build a Notion property map from a state record. PURE.

One-way only: this module never reads Notion, and `notion_id` stays a back-reference set by a
publish, never a read source (GM's invariant). Transport lives elsewhere so the interactive
(first-party MCP) and unattended (notion_gather auth path) callers share one builder."""
import re

_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")

# Kinds with no dedicated branch below: they pass through unchanged. Anything else is a
# schema typo or a kind domain_mirror.coerce() grew that this module was never taught —
# raise rather than ship a wrong-shaped property (mirrors coerce()'s own fail-loud default).
_PASSTHROUGH_KINDS = frozenset(
    {"title", "text", "select", "url", "number", "json_multi_select"})


def _prop_key(prop: str) -> str:
    """Notion reserves `id` and `url`; a user property with that name needs the prefix."""
    return "userDefined:%s" % prop if prop.lower() in ("id", "url") else prop


def to_properties(fm: dict, table: dict) -> dict:
    """Record frontmatter -> Notion `update_properties` map. Absent/None values are OMITTED,
    never emitted as null — a null CLEARS the property, which is data loss, not a no-op."""
    out = {}
    for field, kind, _tmpl, prop, _rel in table["fields"]:
        v = fm.get(field)
        if v is None:
            continue
        if kind == "checkbox":
            out[_prop_key(prop)] = "__YES__" if v else "__NO__"
        elif kind == "date":
            # LOSSY: truncates to the date part. A full timestamp (e.g. "2026-05-29T19:10:00+00:00")
            # loses its time-of-day and UTC offset here — verified against
            # state/domains/personal/tables/attended/angels-at-rays.md. No fix planned; Notion's
            # `date` property is date-only in this schema.
            out["date:%s:start" % prop] = str(v)[:10]
        elif kind in ("relation", "json_relation"):
            # GAP: only strips "[[ ]]" from the local wikilink — this module has no slug->page-URL
            # table to rebuild an actual Notion page URL, and never will (it stays transport-neutral).
            # Task 8's transport layer owns resolving a slug to a page URL before/after this call.
            vals = v if isinstance(v, list) else [v]
            out[_prop_key(prop)] = [_WIKILINK.sub(r"\1", str(x)) for x in vals]
        elif kind == "multi_select":
            out[_prop_key(prop)] = list(v) if isinstance(v, list) else [v]
        elif kind in _PASSTHROUGH_KINDS:
            out[_prop_key(prop)] = v
        else:
            raise ValueError(f"unknown kind: {kind!r}")
    return out
