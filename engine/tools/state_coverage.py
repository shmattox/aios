#!/usr/bin/env python3
"""state_coverage.py — C1 gate: every live Notion property must be mapped or declared ignored.

`state/domains/<silo>/` is a PROJECTION while `direction: import`; the SSOT flip makes it the only
copy, so an unmapped property stops being free and becomes silent loss. This check fails while any
property is neither mapped, Notion metadata, covered by a mapped flat twin, nor declared in
`notion_ignored:` with a reason. It is the gate on `direction: publish`.

Scanning nothing raises rather than passing — a vacuous green is the failure mode this exists to
prevent (see state_validate.scan_tree's zero-tracked-files rule for the same discipline).

Also folds in state_validate.check_direction_coherent: a silo declaring `direction: publish` while
an import task is still enabled is a write loop, and this gate — the one thing standing between a
silo and SSOT status — is the only fact-free home for that check (the engine itself cannot know
which scheduled tasks are enabled). A caller that passes no `tasks_enabled` gets `direction_unchecked:
true` rather than a silent pass.
"""
import argparse, json, os, sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain_mirror as dm  # noqa: E402
import state_validate  # noqa: E402

METADATA = {"Created on", "Created by", "Created time",
            "Last edit", "Last edited by", "Last edited time"}


class CoverageError(Exception):
    """Raised when the check could not actually check anything."""


def is_metadata(prop: str) -> bool:
    """Notion's own bookkeeping: never authored, never worth mirroring."""
    return prop in METADATA or prop.startswith("date:")


def check_scanned_or_raise(props_seen: int, silo: str):
    """Silo-level guard: zero properties seen anywhere means we scanned nothing.

    NOT applied per table — a table can be legitimately empty (personal `medications` has
    row_count 0 today), and failing the silo for that is a false alarm that gets the gate
    switched off. The broken-instrument case is a silo where NO table yielded any property."""
    if props_seen == 0:
        raise CoverageError("silo %r: zero snapshot properties seen — scanned nothing, "
                            "refusing to report covered" % silo)


def uncovered(table: dict, rows: list) -> list:
    """Sorted property names that are genuinely at risk if this silo becomes SSOT."""
    props = set()
    for r in rows:
        props |= {k for k in r.keys() if k != "url"}
    mapped = {spec[3] for spec in table["fields"]}
    ignored = table.get("ignored") or {}
    out = []
    for p in sorted(props - mapped):
        if is_metadata(p):
            continue
        if ("%s (filter)" % p) in mapped:      # the twin rule (spec §2)
            continue
        if p in ignored:
            continue
        out.append(p)
    return out


def check_silo(env_root, silo, snapshot_dir=None, tasks_enabled=()) -> dict:
    cfg = dm.load_silo_config(Path(env_root), silo)
    snap = Path(snapshot_dir) if snapshot_dir else cfg["state_dir"] / "_snapshots"
    result, total, checked, props_seen = {}, 0, 0, 0
    for t in cfg["tables"]:
        if t.get("local_only"):
            continue
        hits = list(snap.rglob("%s-export.json" % t["source_db"]))
        if not hits:
            continue
        rows = json.loads(hits[0].read_text(encoding="utf-8")).get("rows", [])
        checked += 1
        # An empty table is normal (personal `medications` is empty today) — it simply offers no
        # properties. The vacuous-pass guard is at silo level, below.
        props_seen += sum(len([k for k in r.keys() if k != "url"]) for r in rows)
        gaps = uncovered(t, rows)
        if gaps:
            result[t["name"]] = gaps
            total += len(gaps)
    if checked == 0:
        raise CoverageError("no snapshots found for silo %r — refusing to report covered" % silo)
    check_scanned_or_raise(props_seen, silo)
    out = {"silo": silo, "uncovered": result, "total": total, "tables_checked": checked}
    # direction/task-liveness coherence (Task 2's check_direction_coherent) lives here, not in
    # state_validate.main(): the engine is fact-free and cannot know which scheduled tasks are
    # enabled, so it has no honest source for tasks_enabled. A caller that supplies none gets an
    # explicit `direction_unchecked` flag rather than a coherence check that always reads clean.
    if tasks_enabled:
        out["direction_problems"] = state_validate.check_direction_coherent(cfg["schema"], tasks_enabled)
    else:
        out["direction_unchecked"] = True
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fail while any Notion property is uncovered.")
    ap.add_argument("--silo", required=True)
    ap.add_argument("--env-root", default=".")
    ap.add_argument("--snapshot-dir")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        r = check_silo(args.env_root, args.silo, args.snapshot_dir)
    except CoverageError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(r, indent=2))
    else:
        for tname, props in sorted(r["uncovered"].items()):
            for p in props:
                print("%s: %s" % (tname, p))
        print("%s — %d uncovered across %d table(s)." % (r["silo"], r["total"], r["tables_checked"]))
    return 1 if r["total"] else 0


if __name__ == "__main__":
    sys.exit(main())
