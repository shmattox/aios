#!/usr/bin/env python3
"""notion_writeback.py — the tactical Notion write-back, as tested code (A7).

The brief's write-back contract (skills/brief/references/write-back.md, G15e) existed only as
prose an interactive action thread followed by hand — nothing enforced it, and a headless run
had no write path at all. This tool IS the contract: the sibling of notion_gather.py (same
token resolution, same HTTP plumbing, fact-free — ids and paths come from the caller/profile),
with the four rules enforced in code, not prose:

  1. ALLOWLIST — writes only to a db passed via --writable (the profile's
     `notion.write.writable` groups). Anything else is read-only: exit 3.
  2. CONTENT GATE (pause_economic) — lane_policy.ECONOMIC_TRIPWIRE_RE over every outgoing
     string (title, field names, field values). A hit refuses the WHOLE call (exit 3) naming
     the offending fields — economic/ownership/Paper-Governs content NEVER rides the tactical
     path; it pauses for explicit approval. Recall-biased by design (a false positive only
     defers a row to a human, the right asymmetry).
  3. ACT-THEN-TELL — --change-log is REQUIRED; every write appends one receipt row
     {ts, db, page_id, field, old, new, by, run_id} (the undo anchor: Notion page history +
     this row). `flip` reads the CURRENT value first — it becomes `old`. Every write is
     read back and verified before the receipt is written.
  4. TYPED WRITES ONLY — page creation writes {title, rich_text, date, status, select,
     checkbox} properties; a number/people/relation/rollup property is refused BY TYPE
     (that's where amounts live). `flip` may target only {status, select, checkbox, date} —
     it can never rewrite a content field.

Fences fire BEFORE token resolution, so refusals are deterministic and offline-testable.

Usage:
  notion_writeback.py log-row --db <id> --writable <id> [--writable <id> ...]
      --title <text> [--field "Name=Value" ...] --change-log <path>
      [--by <who>] [--run-id <id>] [--token-env NAME]
      Create one row (page) in an allowlisted database. Prints JSON
      {ok, page_id, url, verified, receipt}.
  notion_writeback.py flip --page <id> --field <name> --to <value>
      --writable <id> [...] --change-log <path> [--by/--run-id/--token-env]
      Update ONE status/select/checkbox/date property on a page whose parent database is
      allowlisted. Prints JSON {ok, page_id, field, old, new, verified, receipt}.

Exit codes: 0 ok · 1 API/verify error · 2 no token · 3 fence refusal.
"""

import argparse
import json
import sys
import time

import lane_policy
import notion_gather as ng

WRITE_TYPES = {"title", "rich_text", "date", "status", "select", "checkbox"}
FLIP_TYPES = {"status", "select", "checkbox", "date"}


# ── pure layer (offline-tested) ──────────────────────────────────────────────

def content_gate(fields):
    """{name: value} -> sorted field names whose NAME or VALUE smells economic (rule 2).
    The title rides in as a field. Deliberately recall-biased — see lane_policy."""
    rx = lane_policy.ECONOMIC_TRIPWIRE_RE
    return sorted(name for name, val in fields.items()
                  if rx.search(str(name)) or rx.search(str(val)))


def _typed_value(ptype, value):
    """One (schema type, CLI string) -> the Notion property payload. Raises ValueError."""
    if ptype == "title":
        return {"title": [{"text": {"content": value}}]}
    if ptype == "rich_text":
        return {"rich_text": [{"text": {"content": value}}]}
    if ptype == "date":
        return {"date": {"start": value}}
    if ptype in ("status", "select"):
        return {ptype: {"name": value}}
    if ptype == "checkbox":
        low = str(value).strip().lower()
        if low not in ("true", "false"):
            raise ValueError(f"checkbox value must be true/false, got {value!r}")
        return {"checkbox": low == "true"}
    raise ValueError(f"type {ptype!r} is not writable")


def build_properties(schema_map, fields):
    """(name->type, name->value) -> (properties payload, refusals). Refusals carry
    {field, reason}; an unknown name or a non-WRITE_TYPES type is refused (rule 4)."""
    props, refusals = {}, []
    for name, value in fields.items():
        ptype = schema_map.get(name)
        if ptype is None:
            refusals.append({"field": name, "reason": f"unknown property {name!r} (not in the db schema)"})
            continue
        if ptype not in WRITE_TYPES:
            refusals.append({"field": name, "reason": f"type {ptype!r} is not tactically writable"})
            continue
        try:
            props[name] = _typed_value(ptype, value)
        except ValueError as e:
            refusals.append({"field": name, "reason": str(e)})
    return props, refusals


def flip_guard(prop_obj):
    """A page's property object -> None if flippable, else the refusal reason (rule 4)."""
    t = (prop_obj or {}).get("type")
    if t in FLIP_TYPES:
        return None
    return f"flip may target only {sorted(FLIP_TYPES)} properties, not {t!r} (content fields never flip)"


def append_receipt(path, **fields):
    """Append ONE change-log row (rule 3). Returns the row."""
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    row.update(fields)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def _refuse(msg):
    sys.stderr.write(f"REFUSED (fence): {msg}\n")
    return 3


def _in_writable(db_id, writable):
    ids = set()
    for w in writable:
        try:
            ids.add(ng.normalize_id(w))
        except ValueError:
            pass
    return db_id in ids


# ── API layer ────────────────────────────────────────────────────────────────

def parent_db_candidates(parent, token):
    """A page's `parent` object -> the set of normalized ids that IDENTIFY its container.
    A database and its data source are two ids for one container (Notion-Version 2025-09-03
    returns `data_source_id` parents; profiles' `collection://` ids may be either) — the
    allowlist check must accept whichever alias the caller listed, so resolve the sibling id
    via one GET and check the whole set. Resolution failures just leave the set smaller
    (fail-closed: fewer aliases can only REFUSE more, never allow more)."""
    ids = set()
    ds_id, db_id = parent.get("data_source_id"), parent.get("database_id")
    for raw in (ds_id, db_id):
        if raw:
            ids.add(ng.normalize_id(raw))
    if ds_id and not db_id:
        status, body = ng._request("GET", f"{ng.API_BASE}/data_sources/{ng.normalize_id(ds_id)}",
                                   token, ng.DS_VERSION)
        sibling = ((body.get("parent") or {}).get("database_id")) if status == 200 else None
        if sibling:
            ids.add(ng.normalize_id(sibling))
    elif db_id and not ds_id:
        status, body = ng._request("GET", f"{ng.API_BASE}/databases/{ng.normalize_id(db_id)}",
                                   token, ng.DB_VERSION)
        if status == 200:
            for ds in body.get("data_sources") or []:
                if ds.get("id"):
                    ids.add(ng.normalize_id(ds["id"]))
    return ids


def _schema_map(db_id, token):
    """GET the db schema -> (endpoint_kind, name->type) trying data-source then database."""
    for kind, url, version in (("data_sources", f"{ng.API_BASE}/data_sources/{db_id}", ng.DS_VERSION),
                               ("databases", f"{ng.API_BASE}/databases/{db_id}", ng.DB_VERSION)):
        status, body = ng._request("GET", url, token, version)
        if status == 200:
            props = body.get("properties") or {}
            return kind, {name: p.get("type") for name, p in props.items()}, None
        err = f"HTTP {status}: {body.get('message', str(body))}"
    return None, {}, err


def cmd_log_row(args):
    try:
        db_id = ng.normalize_id(args.db)
    except ValueError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 1
    if not _in_writable(db_id, args.writable):
        return _refuse(f"db {args.db} is not in the writable allowlist — read-only by default (rule 1)")
    bad = [kv for kv in args.field if "=" not in kv]
    if bad:
        sys.stderr.write(f"ERROR: --field must be NAME=VALUE, got {bad}\n")
        return 1
    fields = dict(kv.split("=", 1) for kv in args.field)
    hits = content_gate({"(title)": args.title, **fields})
    if hits:
        return _refuse(f"economic/Paper-Governs content in {hits} — tactical write-back never "
                       f"carries it; route through explicit approval (pause_economic, rule 2)")
    token, _src = ng.resolve_token(args.token_env)
    if not token:
        ng._no_token_exit(args.token_env)
    kind, schema, err = _schema_map(db_id, token)
    if err:
        sys.stderr.write(f"ERROR: schema read failed: {err}\n")
        return 1
    title_prop = next((n for n, t in schema.items() if t == "title"), None)
    if not title_prop:
        sys.stderr.write("ERROR: db has no title property\n")
        return 1
    props, refusals = build_properties(schema, {title_prop: args.title, **fields})
    if refusals:
        return _refuse("; ".join(f"{r['field']}: {r['reason']}" for r in refusals))
    parent = ({"type": "data_source_id", "data_source_id": db_id} if kind == "data_sources"
              else {"type": "database_id", "database_id": db_id})
    version = ng.DS_VERSION if kind == "data_sources" else ng.DB_VERSION
    status, body = ng._request("POST", f"{ng.API_BASE}/pages", token, version,
                               {"parent": parent, "properties": props})
    if status != 200:
        sys.stderr.write(f"ERROR: create failed HTTP {status}: {body.get('message', body)}\n")
        return 1
    page_id = body.get("id")
    # read-back verify (rule 3: tell only what actually landed)
    status2, back = ng._request("GET", f"{ng.API_BASE}/pages/{page_id}", token, version)
    got_title = ng._prop_value((back.get("properties") or {}).get(title_prop, {})) if status2 == 200 else None
    verified = got_title == args.title
    receipt = append_receipt(args.change_log, db=db_id, page_id=page_id, field=title_prop,
                             old=None, new=args.title, by=args.by, run_id=args.run_id)
    print(json.dumps({"ok": verified, "page_id": page_id, "url": body.get("url"),
                      "verified": verified, "receipt": receipt}, indent=1, ensure_ascii=False))
    return 0 if verified else 1


def cmd_flip(args):
    hits = content_gate({args.field: args.to})
    if hits:
        return _refuse(f"economic/Paper-Governs content in {hits} — tactical write-back never "
                       f"carries it; route through explicit approval (pause_economic, rule 2)")
    token, _src = ng.resolve_token(args.token_env)
    if not token:
        ng._no_token_exit(args.token_env)
    try:
        page_id = ng.normalize_id(args.page)
    except ValueError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 1
    status, page = ng._request("GET", f"{ng.API_BASE}/pages/{page_id}", token, ng.DS_VERSION)
    if status != 200:
        status, page = ng._request("GET", f"{ng.API_BASE}/pages/{page_id}", token, ng.DB_VERSION)
    if status != 200:
        sys.stderr.write(f"ERROR: page read failed HTTP {status}: {page.get('message', page)}\n")
        return 1
    parent = page.get("parent") or {}
    candidates = parent_db_candidates(parent, token)
    if not candidates or not any(_in_writable(c, args.writable) for c in candidates):
        return _refuse(f"page's parent db {sorted(candidates)} is not in the writable allowlist (rule 1)")
    parent_db = sorted(candidates)[0]
    prop = (page.get("properties") or {}).get(args.field)
    if prop is None:
        sys.stderr.write(f"ERROR: page has no property {args.field!r}\n")
        return 1
    reason = flip_guard(prop)
    if reason:
        return _refuse(reason)
    old = ng._prop_value(prop)
    payload = {"properties": {args.field: _typed_value(prop["type"], args.to)}}
    status, body = ng._request("PATCH", f"{ng.API_BASE}/pages/{page_id}", token, ng.DS_VERSION, payload)
    if status != 200:
        sys.stderr.write(f"ERROR: flip failed HTTP {status}: {body.get('message', body)}\n")
        return 1
    status2, back = ng._request("GET", f"{ng.API_BASE}/pages/{page_id}", token, ng.DS_VERSION)
    got = ng._prop_value((back.get("properties") or {}).get(args.field, {})) if status2 == 200 else None
    expect = (args.to.strip().lower() == "true") if prop["type"] == "checkbox" else args.to
    verified = got == expect
    receipt = append_receipt(args.change_log, db=ng.normalize_id(parent_db), page_id=page_id,
                             field=args.field, old=old, new=got, by=args.by, run_id=args.run_id)
    print(json.dumps({"ok": verified, "page_id": page_id, "field": args.field, "old": old,
                      "new": got, "verified": verified, "receipt": receipt},
                     indent=1, ensure_ascii=False))
    return 0 if verified else 1


def main(argv=None):
    ng._utf8_stdio()
    ap = argparse.ArgumentParser(description="Tactical Notion write-back with the G15e fence (A7).")
    ap.add_argument("--token-env", default=ng.DEFAULT_TOKEN_NAME)
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--writable", action="append", required=True,
                        help="allowlisted db/data-source id (the profile's notion.write.writable); repeatable")
    common.add_argument("--change-log", required=True,
                        help="receipt JSONL path (the profile's notion.write.change_log) — REQUIRED, rule 3")
    common.add_argument("--by", default=None)   # per-verb default resolved post-parse (see below)
    common.add_argument("--run-id", default=None)
    sp = sub.add_parser("log-row", parents=[common], help="create one row in an allowlisted db")
    sp.add_argument("--db", required=True)
    sp.add_argument("--title", required=True)
    sp.add_argument("--field", action="append", default=[], metavar="NAME=VALUE")
    # add-row (A95): the conclusion-write op — append one row to an allowlisted session_log /
    # decision_log group so every walk conclusion lands in a durable, gather-readable home. Same
    # create machinery + fences as log-row (allowlist rule 1, pause_economic rule 2, read-back
    # receipt rule 3); a distinct verb so the mandate reads intent-first and its receipts default
    # to a conclusion --by. An install whose profile lacks these groups simply never lists them
    # writable, so add-row refuses — the fact-free degrade to threads-only.
    arp = sub.add_parser("add-row", parents=[common],
                         help="append a conclusion row to an allowlisted session/decision log (A95)")
    arp.add_argument("--db", required=True)
    arp.add_argument("--title", required=True)
    arp.add_argument("--field", action="append", default=[], metavar="NAME=VALUE")
    # create-task (A96): on approve of a brief proposal, create the task row in the allowlisted
    # task_status group. Same create machinery + fences as log-row (allowlist rule 1, pause_economic
    # rule 2, read-back receipt rule 3); --field carries the proposal's Priority=/Due= etc. A distinct
    # verb so the gate-proposal path reads intent-first and its receipts default to that author.
    ctp = sub.add_parser("create-task", parents=[common],
                         help="create an approved proposal's task in an allowlisted task DB (A96)")
    ctp.add_argument("--db", required=True)
    ctp.add_argument("--title", required=True)
    ctp.add_argument("--field", action="append", default=[], metavar="NAME=VALUE")
    fp = sub.add_parser("flip", parents=[common], help="update one status/select/checkbox/date property")
    fp.add_argument("--page", required=True)
    fp.add_argument("--field", required=True)
    fp.add_argument("--to", required=True)
    args = ap.parse_args(argv)
    if args.by is None:   # per-verb receipt author (set_defaults can't be used — the --by action is
        # shared via parents=[common], so a subparser set_defaults would leak to every verb)
        args.by = {"add-row": "aios-gate-conclusion",
                   "create-task": "aios-gate-proposal"}.get(args.cmd, "aios-writeback")
    if args.cmd in ("log-row", "add-row", "create-task"):
        return cmd_log_row(args)
    return cmd_flip(args)


if __name__ == "__main__":
    sys.exit(main())
