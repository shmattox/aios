#!/usr/bin/env python3
"""domain_mirror.py — generic, fact-free Notion-snapshot -> state/domains/<silo>/ importer.

import verb: transform a per-silo Notion export snapshot (on disk) into markdown state records,
using declarative per-table config (Notion DB ids from profile/domains.yaml; field maps from the
silo's schema.yaml `notion_fields:`). NEVER reads live Notion (engine runs headless, no MCP grant).
stdlib only. Deterministic + idempotent. Values copied verbatim (Paper-Governs faithfulness).

  python domain_mirror.py import --silo <silo> [--snapshot-dir DIR] [--out DIR] [--dry-run]
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

_CHECKBOX = {"__YES__": True, "__NO__": False}


def notion_id_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def _link(url: str, url_to_slug: dict, link_tmpl: str) -> str:
    slug = url_to_slug[url]                       # KeyError => fail loud (dangling relation)
    return "[[" + link_tmpl.format(slug=slug) + "]]"


def coerce(kind, value, *, url_to_slug, link_tmpl):
    if kind in ("title", "text", "select", "url"):
        return None if value in (None, "") else str(value)
    if kind == "multi_select":
        return None if not value else [str(v) for v in value]
    if kind == "number":
        return None if value is None else value
    if kind == "date":
        return None if value in (None, "") else str(value)
    if kind == "checkbox":
        if value is None:
            return None
        if value not in _CHECKBOX:
            raise ValueError(f"unexpected checkbox value: {value!r}")
        return _CHECKBOX[value]
    if kind == "relation":
        if not value:
            return None
        if isinstance(value, list):
            return [_link(u, url_to_slug, link_tmpl) for u in value]
        return _link(value, url_to_slug, link_tmpl)
    raise ValueError(f"unknown kind: {kind!r}")


def _emit_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    has_ctrl = ("\n" in s) or ("\r" in s) or ("\t" in s)
    needs_quote = (
        s == "" or s.strip() != s or s.startswith("[[") or s[:1] in "[]{}#&*!|>'\"%@`,-?:"
        or ": " in s or " #" in s or s.lower() in ("null", "true", "false", "yes", "no", "~")
        or _looks_number(s) or has_ctrl
    )
    if needs_quote:
        # Escape backslash first, then the quote char, then control chars — a raw newline/CR/tab
        # inside a scalar would otherwise break the value across physical frontmatter lines.
        esc = (s.replace("\\", "\\\\").replace('"', '\\"')
                .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
        return '"' + esc + '"'
    return s


def _looks_number(s: str) -> bool:
    try:
        float(s); return True
    except ValueError:
        return False


def emit_frontmatter(fields: dict) -> str:
    lines = ["---"]
    for k, v in fields.items():
        if isinstance(v, list):
            inner = ", ".join(_emit_scalar(x) for x in v)
            lines.append(f"{k}: [{inner}]")
        else:
            lines.append(f"{k}: {_emit_scalar(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


sys.path.insert(0, str(Path(__file__).resolve().parent))
from state_validate import _parse_yaml, _extract_frontmatter  # reuse the engine's YAML-subset reader


def find_env_root(start: Path) -> Path:
    p = Path(start).resolve()
    for cand in (p, *p.parents):
        if (cand / "profile" / "domains.yaml").is_file():
            return cand
    raise FileNotFoundError(f"no profile/domains.yaml above {start}")


# Emitted by build_record itself, never read off the snapshot: `type` opens the record and the
# notion_* trio closes it. A schema cannot hand these to `state_native:` (see load_silo_config).
_RESERVED = frozenset({"type", "notion_id", "notion_url", "last_synced"})


def load_silo_config(env_root: Path, silo: str) -> dict:
    state_dir = (Path(env_root) / "state" / "domains" / silo).resolve()
    schema_path = state_dir / "schema.yaml"
    if not schema_path.is_file():
        raise FileNotFoundError(f"no schema.yaml for silo {silo!r}: {schema_path}")
    schema = _parse_yaml(schema_path.read_text(encoding="utf-8"))
    tables = []
    for tname, tdef in schema.items():
        if not isinstance(tdef, dict) or "notion_fields" not in tdef:
            continue                                   # non-importable table (e.g. state-price manual)
        fields = []
        for field, spec in tdef["notion_fields"].items():
            prop = spec[0]
            kind = spec[1]
            link_tmpl = spec[2] if len(spec) > 2 else None
            # OPTIONAL 4th flow-list arg on a `relation`: the source_db of a DIFFERENT export whose
            # url_to_slug resolves this relation (a CROSS-export relation, e.g. assets.asset -> the
            # prices export). None => same-export (the row's own export slug map).
            rel_source = spec[3] if len(spec) > 3 else None
            fields.append((field, kind, link_tmpl, prop, rel_source))
        # OPTIONAL `computed_fields:` — record fields DERIVED by a declarative rule rather than read
        # off a Notion property (state-native). Kept fact-free: the rule + any lookup facts live in
        # the schema, never the engine. Each entry is (field_name, rule_spec_dict).
        computed = list((tdef.get("computed_fields") or {}).items())
        # OPTIONAL `state_native:` (A80) — fields NO rule can reproduce (a hand-set link, not a
        # snapshot property and not derivable from one), so the importer must carry them forward
        # rather than rebuild them away. Fact-free: which fields qualify is the silo's call.
        state_native = list(tdef.get("state_native") or [])
        # `_RESERVED` are emitted by build_record itself, so declaring one state_native is always a
        # schema error — and a silently BROKEN one either way: `type` is carried from disk and then
        # overrides the schema-derived value (and state_validate dispatches on `type`), while the
        # notion_* trio is rebuilt AFTER the carry-forward and silently ignores the declaration.
        derived = ({f[0] for f in fields} | {c[0] for c in computed} | _RESERVED)
        clash = sorted(set(state_native) & derived)
        if clash:
            raise ValueError(f"[{tname}] state_native fields are also derived from the snapshot: "
                             f"{clash} — a field cannot be both unreproducible and computed")
        tables.append({"name": tname, "source_db": tdef["notion_source_db"],
                       "fields": fields, "computed": computed, "state_native": state_native})
    return {"state_dir": state_dir, "schema": schema, "tables": tables}


def compute_field(spec: dict, fm: dict):
    """Evaluate a declarative computed (state-native) field against the record fields built so far.
    Fact-free: the engine knows only the RULE shape; all specifics (source field, mapping, slug
    templates) come from the schema `spec`.

    Supported rule (minimal, closed set):
      lookup — map the value of another record field (`from`) through a declared `table`, then
               optionally wrap the result as a relation wikilink via `link` (e.g. "companies/{slug}").
               A None source yields None (field omitted-as-null). An unmapped key uses `default`
               if declared, else FAILS LOUD (a content/contract error, like an unknown checkbox)."""
    rule = spec.get("rule")
    if rule != "lookup":
        raise ValueError(f"unknown computed-field rule: {rule!r}")
    src = fm.get(spec["from"])
    if src is None:
        return None
    table = spec.get("table") or {}
    if src in table:
        out = table[src]
    elif "default" in spec:
        out = spec["default"]
    else:
        raise ValueError(f"computed lookup {spec['from']}={src!r}: no mapping and no default")
    link = spec.get("link")
    return "[[" + link.format(slug=out) + "]]" if link else out


def _read_state_native(dest, keys) -> dict:
    """The declared state-native keys present on an EXISTING record at `dest` (A80).

    Absent file or absent key -> that key is simply not carried, so build_record omits it. That is
    deliberate and evidence-led: a table's state-native field is routinely present on only SOME of
    its records, and emitting `null` for the rest would rewrite records that are already correct.
    A genuinely unreadable file raises out of read_text — failing loud is right, because carrying
    nothing from a corrupt record is exactly the silent deletion this exists to prevent. The limit
    of that guarantee: `_parse_yaml` is permissive, so a TORN write that leaves a colonless line
    inside intact `---` fences parses to {} and the key is dropped silently rather than loudly. That
    is the reader's pre-existing behaviour across the whole pipeline, not a rule this function can
    fix locally — stated here so the guarantee is not read as stronger than it is."""
    if not keys or not dest.is_file():
        return {}
    fm = _extract_frontmatter(dest.read_text(encoding="utf-8"))
    return {k: fm[k] for k in keys if k in fm}


def build_record(table, row, url_to_slug, slug_maps, last_synced=None, preserved=None) -> tuple[str, str]:
    slug = url_to_slug[row["url"]]
    fm = {"type": table["name"]}
    for field, kind, link_tmpl, prop, rel_source in table["fields"]:
        # A cross-export relation resolves against the NAMED export's slug map; everything else
        # (incl. a same-export relation) against the row's own export slug map.
        u2s = slug_maps[rel_source] if rel_source else url_to_slug
        fm[field] = coerce(kind, row.get(prop), url_to_slug=u2s, link_tmpl=link_tmpl)
    # Computed (state-native) fields run AFTER the Notion-property fields so a rule can read them.
    for cfield, cspec in table["computed"]:
        fm[cfield] = compute_field(cspec, fm)
    # state_native (A80): carried verbatim from the existing record — no rule can reproduce these,
    # so rebuilding without them DELETES them. Emitted HERE, in the computed slot, because that is
    # where they already sit on disk; any other position would churn every such record for nothing.
    # `preserved` holds only keys that were actually present, so an absent one stays absent.
    for _k, _v in (preserved or {}).items():
        fm[_k] = _v
    notion_id = notion_id_from_url(row["url"])
    fm["notion_id"] = notion_id
    # Derivation, not a copy: the canonical notion_url is https://www.notion.so/<notion_id>.
    # (The snapshot row's own `url` uses a different host + id and is NOT reused here.)
    fm["notion_url"] = "https://www.notion.so/" + notion_id
    if last_synced is not None:
        fm["last_synced"] = last_synced
    body = (row.get("Description") or "").strip()
    text = emit_frontmatter(fm) + ("\n" + body + "\n" if body else "")
    return slug, text


def import_silo(env_root, silo, snapshot_dir, out_dir=None, *, dry_run=False, last_synced=None):
    cfg = load_silo_config(env_root, silo)
    out = Path(out_dir) if out_dir else cfg["state_dir"] / "tables"
    snapshot_dir = Path(snapshot_dir)
    # Pre-load every mapped export's data + url_to_slug ONCE, keyed by source_db, so a cross-export
    # relation in one table can resolve against another table's export slug map. Fail loud here if a
    # mapped export is missing (before any write) — deterministic, atomic-ish.
    exports, slug_maps = {}, {}
    for table in cfg["tables"]:
        db = table["source_db"]
        if db in exports:
            continue
        export = snapshot_dir / f"{db}-export.json"
        if not export.is_file():
            raise FileNotFoundError(f"[{silo}/{table['name']}] missing snapshot: {export}")
        exports[db] = json.loads(export.read_text(encoding="utf-8"))
        slug_maps[db] = exports[db]["url_to_slug"]
    # Pre-flight the cross-export relations too (same atomic fail-loud as the missing-snapshot check
    # above): a relation naming a source_db that is NOT a mirrored table would otherwise KeyError
    # mid-write, leaving an earlier table's files on disk (a partial import). Validate before any write.
    for table in cfg["tables"]:
        for field, _kind, _tmpl, _prop, rel_source in table["fields"]:
            if rel_source and rel_source not in slug_maps:
                raise KeyError(f"[{silo}/{table['name']}] field {field!r}: cross-export relation "
                               f"source_db {rel_source!r} is not a mirrored table (no export loaded)")
    written = []
    for table in cfg["tables"]:
        data = exports[table["source_db"]]
        url_to_slug = slug_maps[table["source_db"]]
        # last_synced precedence: the snapshot's own export date (`_meta.exported`) wins; the CLI
        # value is the fallback for snapshots that carry no export date; else the field is omitted.
        eff_last_synced = (data.get("_meta") or {}).get("exported") or last_synced
        # Records land under the table's SEMANTIC name (its notion_source_db), not the type key.
        # The type key stays in each record's `type:` frontmatter, which is what state_validate
        # reads — dir names are for humans and for the sync's reap scoping.
        tdir = out / table["source_db"]
        for row in data["rows"]:
            # The slug is derived here as well as in build_record because the DESTINATION path is
            # needed BEFORE the record is built: state_native carries forward from whatever is
            # already there. Same expression, same fail-loud on an unmapped row url.
            dest = tdir / f"{url_to_slug[row['url']]}.md"
            preserved = _read_state_native(dest, table["state_native"])
            slug, text = build_record(table, row, url_to_slug, slug_maps,
                                      last_synced=eff_last_synced, preserved=preserved)
            dest = tdir / f"{slug}.md"
            if not dry_run:
                tdir.mkdir(parents=True, exist_ok=True)
                dest.write_text(text, encoding="utf-8")
            written.append(dest)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(prog="domain_mirror.py")
    sub = ap.add_subparsers(dest="verb", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("--silo", required=True)
    imp.add_argument("--snapshot-dir")
    imp.add_argument("--out")
    imp.add_argument("--last-synced", help="fallback last_synced date (YYYY-MM-DD) when a "
                     "snapshot carries no _meta.exported date")
    imp.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    env_root = find_env_root(Path(__file__))
    snap = Path(args.snapshot_dir) if args.snapshot_dir else \
        env_root / "state" / "domains" / args.silo / "_snapshots"
    written = import_silo(env_root, args.silo, snap, args.out, dry_run=args.dry_run,
                          last_synced=args.last_synced)
    print(f"{'[dry-run] ' if args.dry_run else ''}{len(written)} records for silo {args.silo!r}")
    for p in written:
        print("  ", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
