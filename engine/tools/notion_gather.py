#!/usr/bin/env python3
"""notion_gather.py — native Notion API reader for HEADLESS gathers (A18).

Headless `claude -p` fleet runs have no interactive-grant Notion MCP, so the brief
precompute ran Notion-blind (carried-forward urgencies). This tool is the API-key
read path: a stdlib-only REST reader the task body calls when MCP is absent, so an
unattended gather is live instead of a date-roll.

Fact-free: no database ids, property names, or people facts live here — the caller
passes the profile's `collection://` ids on the CLI. Read-only by construction
(query/GET endpoints only; no write endpoint is ever called).

Token resolution (first hit wins; NEVER stored in the repo or profile):
  1. env var (default AIOS_NOTION_TOKEN; override with --token-env)
  2. Windows Credential Manager generic credential with the same name
     (store once: cmdkey /generic:AIOS_NOTION_TOKEN /user:aios /pass:<secret>)
Exit 2 with a setup hint when neither yields a token — callers treat that as
"no API path configured" and fall back to the SKILL's Degraded-gathers rule.

API shape: tries the current data-source endpoint first
(POST /v1/data_sources/{id}/query, Notion-Version 2025-09-03) and falls back to the
classic database endpoint (POST /v1/databases/{id}/query, 2022-06-28) — the
`collection://` ids in profiles are data-source ids, but older workspaces resolve
them at the database endpoint. Filtering is CLIENT-SIDE (--status-exclude) so the
tool never assumes a property schema.

Usage:
  notion_gather.py check
      Verify the token: GET /v1/users/me; print the bot identity. Exit 0/1/2.
  notion_gather.py tasks --db <id> [--db <id> ...] [--status-exclude Done ...]
      [--page-size 100] [--token-env NAME]
      Query each db/data-source, normalize pages, print ONE JSON doc to stdout:
      {gathered_utc, live, sources:[{db, ok, endpoint, count, error, items:[...]}]}
      Per-db errors do not kill the run (ok:false + error); live:true only if at
      least one source succeeded. Exit 0 if any source ok, 1 if all failed.
"""

import argparse
import io
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

API_BASE = "https://api.notion.com/v1"
DS_VERSION = "2025-09-03"   # data-source endpoint
DB_VERSION = "2022-06-28"   # classic database endpoint
DEFAULT_TOKEN_NAME = "AIOS_NOTION_TOKEN"


def _utf8_stdio():
    # Rewrap only when needed (cp1252 console): an already-UTF-8 stream — including
    # pytest's capture streams — is left alone so output stays observable.
    for stream in ("stdout", "stderr"):
        s = getattr(sys, stream)
        enc = (getattr(s, "encoding", "") or "").lower().replace("-", "")
        if hasattr(s, "buffer") and enc != "utf8":
            setattr(sys, stream, io.TextIOWrapper(s.buffer, encoding="utf-8", errors="replace"))


# ── token resolution ─────────────────────────────────────────────────────────

def _cred_read_windows(target):
    """Read a generic credential's secret from Windows Credential Manager.
    Returns None (never raises) off-Windows or when the credential is absent."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", wt.DWORD), ("Type", wt.DWORD), ("TargetName", wt.LPWSTR),
                ("Comment", wt.LPWSTR), ("LastWritten", ctypes.c_ulonglong),
                ("CredentialBlobSize", wt.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wt.DWORD), ("AttributeCount", wt.DWORD),
                ("Attributes", ctypes.c_void_p), ("TargetAlias", wt.LPWSTR),
                ("UserName", wt.LPWSTR),
            ]

        adv = ctypes.windll.advapi32
        pcred = ctypes.POINTER(CREDENTIAL)()
        CRED_TYPE_GENERIC = 1
        if not adv.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
            return None
        try:
            cred = pcred.contents
            blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
            # cmdkey writes the secret as UTF-16LE; tolerate UTF-8 writers too. A Notion
            # secret is printable ASCII — a UTF-16 decode yielding anything else is a
            # UTF-8-stored blob mis-read as UTF-16 (would send a garbage token -> 401).
            text = None
            try:
                t16 = blob.decode("utf-16-le").strip()
                if t16 and all(32 <= ord(c) < 127 for c in t16):
                    text = t16
            except UnicodeDecodeError:
                pass
            if text is None:
                text = blob.decode("utf-8", "replace").strip()
            return text or None
        finally:
            adv.CredFree(pcred)
    except Exception:
        return None


def resolve_token(token_name=DEFAULT_TOKEN_NAME):
    tok = os.environ.get(token_name, "").strip()
    if tok:
        return tok, "env"
    tok = _cred_read_windows(token_name)
    if tok:
        return tok, "credential-manager"
    return None, None


def _no_token_exit(token_name):
    sys.stderr.write(
        f"ERROR: no Notion token found (env var {token_name} unset; no Credential Manager "
        f"generic credential '{token_name}').\n"
        f"Setup (owner, once): create an INTERNAL integration at notion.so/my-integrations, "
        f"share the relevant teamspaces/databases with it, then store the secret:\n"
        f"  cmdkey /generic:{token_name} /user:aios /pass:<secret>\n"
    )
    sys.exit(2)


# ── HTTP ─────────────────────────────────────────────────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects: urllib forwards the Authorization header on 30x (only
    Content-* is stripped), which could carry the token cross-origin."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# AV / corporate TLS inspection (e.g. Norton Web/Mail Shield, verified live on the
# reference install) presents a MITM chain whose root violates RFC 5280 strictness
# (Basic Constraints not marked critical). Python 3.13+ enables VERIFY_X509_STRICT by
# default and rejects it with CERTIFICATE_VERIFY_FAILED. Drop ONLY the strict flag —
# full chain validation against the system trust store and hostname checks stay on
# (the interceptor's root must still be trusted by the OS to pass).
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.verify_flags &= ~ssl.VERIFY_X509_STRICT

_OPENER = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=_SSL_CTX), _NoRedirect)


def _request(method, url, token, version, body=None, timeout=30):
    """Return (status_code, parsed_json_or_None). Network/HTTP errors -> status + detail."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Notion-Version": version,
        "Content-Type": "application/json",
    })
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8"))
        except Exception:
            detail = {"message": str(e)}
        return e.code, detail
    except Exception as e:  # DNS, timeout, TLS
        return 0, {"message": f"network error: {e}"}


# ── normalization (pure — unit-tested) ───────────────────────────────────────

def normalize_id(raw):
    """Accept a bare uuid (with/without dashes) or a profile 'collection://<uuid>' /
    'view://<uuid>' ref; return the dashed uuid Notion endpoints take."""
    s = raw.strip()
    if "://" in s:
        s = s.split("://", 1)[1]
    s = s.strip("/").replace("-", "")
    if len(s) != 32 or any(c not in "0123456789abcdefABCDEF" for c in s):
        raise ValueError(f"not a Notion id: {raw!r}")
    s = s.lower()
    return f"{s[0:8]}-{s[8:12]}-{s[12:16]}-{s[16:20]}-{s[20:32]}"


def _prop_value(prop):
    """Collapse one Notion property object to a plain value (or None)."""
    t = prop.get("type")
    v = prop.get(t)
    if v is None:
        return None
    if t == "title" or t == "rich_text":
        return "".join(part.get("plain_text", "") for part in v) or None
    if t in ("status", "select"):
        return v.get("name")
    if t == "multi_select":
        return [o.get("name") for o in v] or None
    if t == "date":
        return v.get("start")
    if t in ("number", "checkbox", "url", "email", "phone_number"):
        return v
    if t == "people":
        return [p.get("name") or p.get("id") for p in v] or None
    if t == "formula":
        return v.get(v.get("type"))
    return None  # relations/rollups/files: not needed for urgency triage


def normalize_page(page):
    """One Notion page -> a flat task record. Property names are the workspace's own
    (fact-free): title is found by TYPE; status/priority/due by conventional-name
    match (case-insensitive) with type fallbacks."""
    props = page.get("properties", {})
    rec = {
        "id": page.get("id"),
        "url": page.get("url"),
        "last_edited": page.get("last_edited_time"),
        "title": None, "status": None, "priority": None, "due": None,
        "props": {},
    }
    for name, prop in props.items():
        val = _prop_value(prop)
        if val is None:
            continue
        t = prop.get("type")
        low = name.lower()
        if t == "title":
            rec["title"] = val
            continue
        rec["props"][name] = val
        if rec["status"] is None and (t == "status" or (t == "select" and "status" in low)):
            rec["status"] = val
        if rec["priority"] is None and "priority" in low:
            rec["priority"] = val
        if rec["due"] is None and t == "date" and ("due" in low or "date" in low):
            rec["due"] = val
    return rec


def filter_items(items, status_exclude):
    if not status_exclude:
        return items
    drop = {s.lower() for s in status_exclude}
    return [i for i in items if (i.get("status") or "").lower() not in drop]


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_check(args):
    token, source = resolve_token(args.token_env)
    if not token:
        _no_token_exit(args.token_env)
    status, body = _request("GET", f"{API_BASE}/users/me", token, DB_VERSION)
    if status == 200:
        bot = body.get("name") or body.get("id")
        ws = (body.get("bot") or {}).get("workspace_name")
        print(f"OK: token valid ({source}). Integration: {bot}" + (f" · workspace: {ws}" if ws else ""))
        return 0
    print(f"ERROR: token rejected (HTTP {status}): {body.get('message', body)}", file=sys.stderr)
    return 1


def _query_source(db_id, token, page_size):
    """Query one id: data-source endpoint first, database endpoint as fallback.
    Returns (endpoint_used, pages, error). page_size is the TOTAL item cap; the
    per-request page_size is clamped to Notion's API max of 100."""
    body = {"page_size": min(page_size, 100)}
    last_err = None
    for endpoint, version in ((f"data_sources/{db_id}/query", DS_VERSION),
                              (f"databases/{db_id}/query", DB_VERSION)):
        pages, cursor = [], None
        while True:
            if cursor:
                body["start_cursor"] = cursor
            else:
                body.pop("start_cursor", None)
            status, resp = _request("POST", f"{API_BASE}/{endpoint}", token, version, body)
            if status != 200:
                err = f"HTTP {status}: {resp.get('message', str(resp))}"
                break
            pages.extend(resp.get("results", []))
            if not resp.get("has_more") or len(pages) >= page_size:
                return endpoint.split("/", 1)[0], pages, None
            cursor = resp.get("next_cursor")
        if pages:
            # The endpoint WORKED, then failed mid-pagination (429/500). Report that
            # error — falling through to the other endpoint would mask it behind a 404.
            return None, [], f"{endpoint.split('/', 1)[0]}: {err} (after {len(pages)} pages fetched)"
        last_err = err
    return None, [], last_err


def cmd_tasks(args):
    token, _source = resolve_token(args.token_env)
    if not token:
        _no_token_exit(args.token_env)
    sources = []
    for raw in args.db:
        entry = {"db": raw, "ok": False, "endpoint": None, "count": 0, "error": None, "items": []}
        try:
            db_id = normalize_id(raw)
        except ValueError as e:
            entry["error"] = str(e)
            sources.append(entry)
            continue
        endpoint, pages, err = _query_source(db_id, token, args.page_size)
        if err is not None:
            entry["error"] = err
        else:
            items = filter_items([normalize_page(p) for p in pages], args.status_exclude)
            entry.update(ok=True, endpoint=endpoint, count=len(items), items=items)
        sources.append(entry)
    live = any(s["ok"] for s in sources)
    print(json.dumps({
        "gathered_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "live": live,
        "sources": sources,
    }, indent=1))
    return 0 if live else 1


def retrieve_page(page_id, token):
    """GET one page by id → a decision-ready record, trying the data-source API version then the
    database one (a page can belong to either, exactly like cmd_flip's read). Returns a dict:

      {ok, found, archived, page, http, error}

    - found=True  → the page exists (record in `page`, `archived` flags trashed/archived);
    - found=False → Notion says genuinely absent (HTTP 404) — the "vanished for real" verdict;
    - found=None  → the query could not decide (network/degraded/other HTTP) — the caller must
                    NOT conclude 'absent', it degrades to 'unverified'.

    This is the A95 Done-vs-vanished primitive: the open-task view filters Status≠Done, so a
    completed task simply disappears between gathers — ONE direct by-id read distinguishes
    'completed' from 'genuinely gone' instead of hedging 'status unconfirmed'."""
    status, body = _request("GET", f"{API_BASE}/pages/{page_id}", token, DS_VERSION)
    if status != 200:
        status, body = _request("GET", f"{API_BASE}/pages/{page_id}", token, DB_VERSION)
    if status == 200:
        rec = normalize_page(body)
        archived = bool(body.get("archived") or body.get("in_trash"))
        return {"ok": True, "found": True, "archived": archived, "page": rec,
                "http": status, "error": None}
    if status == 404:
        return {"ok": False, "found": False, "archived": None, "page": None,
                "http": 404, "error": (body or {}).get("message")}
    # 0 (network/TLS) or any other HTTP — undecided, never a false 'absent'
    return {"ok": False, "found": None, "archived": None, "page": None,
            "http": status, "error": (body or {}).get("message")}


def cmd_retrieve(args):
    token, _source = resolve_token(args.token_env)
    if not token:
        _no_token_exit(args.token_env)
    try:
        page_id = normalize_id(args.page)
    except ValueError as e:
        print(json.dumps({"ok": False, "found": None, "page": None, "error": str(e)}, indent=1))
        return 1
    result = retrieve_page(page_id, token)
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0 if result["ok"] else 1


def main(argv=None):
    _utf8_stdio()
    ap = argparse.ArgumentParser(description="Read-only Notion API gather for headless aios runs (A18).")
    ap.add_argument("--token-env", default=DEFAULT_TOKEN_NAME,
                    help=f"env var / Credential Manager name holding the integration secret (default {DEFAULT_TOKEN_NAME})")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="verify the token against /v1/users/me")
    sp = sub.add_parser("tasks", help="query databases/data-sources; print normalized JSON")
    sp.add_argument("--db", action="append", required=True,
                    help="database / data-source id (bare uuid or collection://uuid); repeatable")
    sp.add_argument("--status-exclude", action="append", default=[],
                    help="drop items whose status equals this (client-side, case-insensitive); repeatable")
    sp.add_argument("--page-size", type=int, default=100,
                    help="total item cap per source (fetched in API pages of <=100; default 100)")
    rp = sub.add_parser("retrieve", help="GET one page by id (A95 Done-vs-vanished direct read)")
    rp.add_argument("--page", required=True, help="page id (bare uuid or collection://uuid)")
    args = ap.parse_args(argv)
    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "retrieve":
        return cmd_retrieve(args)
    return cmd_tasks(args)


if __name__ == "__main__":
    sys.exit(main())
