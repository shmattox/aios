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


def _date_key(prop: str) -> str:
    """Of the 35 real `date` fields across state/domains/*/schema.yaml, 33 already declare
    `prop` as the flattened wire key domain_sync._flatten_date produces on gather/import, e.g.
    "date:Due:start" (see stable_slugs.py / domain_sync.py, same convention); 1 uses its ":end"
    counterpart (state-session's `date_end`, "date:Date:end" —
    state/domains/familyoffice/schema.yaml:290); 1 is the schema outlier (personal `created`,
    a bare "Created"). Re-wrapping an already-flattened key — verified against real data — was
    silently corrupting every real date field into "date:date:Due:start:start"; the predicate
    below accepts both flattened suffixes so `date_end` doesn't suffer the same corruption.
    Wrap only the bare-name outlier and Task 7's own synthetic test props, which use plain
    display names."""
    return (prop if prop.startswith("date:") and prop.endswith((":start", ":end"))
            else "date:%s:start" % prop)


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
            out[_date_key(prop)] = str(v)[:10]
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


# ── Task 8: publish-OUT dry run ─────────────────────────────────────────────
# Everything below reads local state and builds a plan; nothing here ever opens a network
# connection or a file in write mode. That is the whole point of a dry run.
import datetime as _dt
import os as _os
import sys as _sys
from pathlib import Path

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import domain_mirror as dm  # noqa: E402
from state_validate import _extract_frontmatter  # noqa: E402

_REL_KINDS = ("relation", "json_relation")


# Bracketed with a symbol pair that plain-English record prose does not produce (a bare
# "UNRESOLVED" collides with the literal word inside real session-log narrative -- verified:
# 3 hits in familyoffice, zero of them a real marker), so grepping this exact string on the
# rendered artifact finds only genuine unresolved relations.
_UNRESOLVED_MARK = "⟦UNRESOLVED⟧"


def _resolve_relation(state_dir: Path, rel_path: str) -> tuple:
    """`rel_path` is a stripped wikilink like "prices/btc" -- exactly what to_properties()
    already leaves after removing the `[[ ]]` brackets, which doubles as the record's location
    on disk (state_dir/tables/<rel_path>.md). Resolve it to the TARGET record's own notion_url,
    using the same "https://www.notion.so/<id>" shape domain_mirror.py derives on import --
    read straight off disk, no network call. A target that doesn't exist yet or hasn't been
    published (no notion_id) comes back marked unresolved rather than as a fabricated URL."""
    target = state_dir / "tables" / f"{rel_path}.md"
    if target.is_file():
        nid = _extract_frontmatter(target.read_text(encoding="utf-8")).get("notion_id")
        if nid:
            return "https://www.notion.so/" + nid, True
    return f"{_UNRESOLVED_MARK}:{rel_path}", False


def plan_publish(env_root, silo) -> list:
    """What a publish WOULD send, per record. Writes nothing anywhere -- this is the diff
    artifact a human reads before anything is ever sent to Notion.

    Each entry: {"slug", "table", "notion_id", "operation", "properties", "notes"}.
    `notes` carries the two Task-7-documented gaps inline, so a reader sees them as expected
    behavior rather than mistaking them for real drift: a `date` field is truncated to
    YYYY-MM-DD by design ONLY when that truncation actually drops something (never "this
    record changed" when it doesn't), and an unresolved relation is called out by name (its
    target hasn't been published yet) rather than silently sent as a slug.

    Iteration order is sorted everywhere it touches output (`rel_props`/`date_fields` were
    sets keyed by Python's per-process-randomized string hash -- same input, different note
    order every run; verified: 6 consecutive personal runs diffed 24/0/22/20/32 lines against
    the first). A dry-run artifact's only use is diffing today's against yesterday's, so
    non-determinism here defeats the point."""
    cfg = dm.load_silo_config(Path(env_root), silo)
    plan = []
    for t in cfg["tables"]:
        tdir = cfg["state_dir"] / "tables" / t["source_db"]
        if not tdir.is_dir():
            # Not a data-loss risk today (no records means nothing is being dropped), but a
            # mapped table that silently stops resolving would otherwise vanish from the
            # artifact with no line saying so -- in the one sub-project whose whole stated
            # risk is silent loss. Say so.
            plan.append({"slug": f"<{t['source_db']}>", "table": t["name"], "notion_id": None,
                         "operation": "skip", "properties": {},
                         "notes": [f"table dir tables/{t['source_db']} not found -- "
                                   f"nothing in this table published"]})
            continue
        rel_props = sorted({_prop_key(prop) for _f, kind, _tmpl, prop, _rel in t["fields"]
                            if kind in _REL_KINDS})
        date_fields = sorted((field, _date_key(prop)) for field, kind, _tmpl, prop, _rel
                              in t["fields"] if kind == "date")
        for path in sorted(tdir.glob("*.md")):
            fm = _extract_frontmatter(path.read_text(encoding="utf-8"))
            nid = fm.get("notion_id")
            props = to_properties(fm, t)
            notes = []
            for prop_key in rel_props:
                vals = props.get(prop_key)
                if vals is None:
                    continue
                resolved = []
                for v in vals:
                    url, ok = _resolve_relation(cfg["state_dir"], v)
                    resolved.append(url)
                    if not ok:
                        notes.append(f"{prop_key}: {v!r} not resolvable "
                                      f"(target not published yet)")
                props[prop_key] = resolved
            for field, date_key in date_fields:
                if date_key not in props:
                    continue
                raw = fm.get(field)
                if raw is not None and str(raw)[:10] != str(raw):
                    notes.append(f"{date_key} is date-only by design "
                                  f"(Notion drops time-of-day/offset here)")
            plan.append({"slug": path.stem, "table": t["name"], "notion_id": nid,
                         "operation": "update" if nid else "create",
                         "properties": props, "notes": notes})
    return plan


_MAX_LINE = 500


def _elide(line: str) -> str:
    """A whole session-log narrative repr'd onto one line can run thousands of characters --
    verified: familyoffice's real artifact had 408 lines over 500 chars, longest 8279. Cut
    long lines with an explicit marker rather than let one record's prose swamp the diff.
    The cut point backs off until content+suffix together still fit under the cap -- a
    first-pass fixed cut (line[:500] + suffix) leaves the RESULT over 500 chars too, since the
    suffix itself takes space the cap didn't budget for."""
    if len(line) <= _MAX_LINE:
        return line
    cut = _MAX_LINE
    while cut > 0:
        suffix = f"…[+{len(line) - cut} chars elided]"
        if cut + len(suffix) <= _MAX_LINE:
            return line[:cut] + suffix
        cut -= 1
    return line[:_MAX_LINE]


def format_diff(plan: list, *, silo: str = None, generated_at: str = None) -> str:
    """Render plan_publish()'s output as a human-skimmable diff artifact. One block per
    record; `notes` are printed inline so the known-lossy/unresolved cases read as
    documented behavior, not alarming drift.

    `silo`/`generated_at` are optional purely for the CLI's header line (silo, generation
    date, record count, create/update tally) -- callers doing the byte-stability comparison
    (this is otherwise a pure function of `plan`) should omit them, or pass a fixed
    `generated_at`, to keep the header itself comparable run to run. Day-granularity (not a
    full timestamp) by design: the header states when the snapshot was taken, and the whole
    point of this artifact is comparing same-day runs byte-for-byte."""
    lines = []
    if silo is not None:
        creates = sum(1 for e in plan if e["operation"] == "create")
        updates = sum(1 for e in plan if e["operation"] == "update")
        ts = generated_at if generated_at is not None else _dt.date.today().isoformat()
        lines.append(f"# silo={silo} generated={ts} records={len(plan)} "
                     f"create={creates} update={updates}")
        lines.append("")
    for e in plan:
        header = f"## {e['slug']}  [{e['operation']}]"
        if e["notion_id"]:
            header += f"  notion_id={e['notion_id']}"
        lines.append(header)
        for k, v in sorted(e["properties"].items()):
            lines.append(_elide(f"    {k}: {v!r}"))
        for note in e.get("notes", []):
            lines.append(_elide(f"    ! {note}"))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None):
    """CLI: preview a publish. Prints the diff artifact to stdout. Never writes anything --
    there is no flag on this command that sends anything to Notion."""
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env-root", default=".")
    ap.add_argument("--silo", required=True)
    args = ap.parse_args(argv)
    plan = plan_publish(Path(args.env_root), args.silo)
    print(format_diff(plan, silo=args.silo))


if __name__ == "__main__":
    main()
