#!/usr/bin/env python3
"""capture_router.py — the auto/ -> {kb}/raw/inbox/ bridge (BACKLOG B-G19).

Re-connects the inbox feed the 2026-06-20→22 pipeline cutover severed. The retired
`inbox-autosort` used to move `00_Inbox/auto/{source}/` stubs into `{kb}/raw/inbox/{source}/`
(the "01:00 auto-sort" that upstream mail/clipper feeds still expect). Nothing replaced it, so gmail +
webclipper stubs piled up unrouted in `auto/` while the inbox-capture stage (which reads
`{kb}/raw/inbox/`) saw only session records. This tool restores that bridge.

Shape mirrors `capture.py` exactly (B8/C9 pattern): a tested, stdlib-only, FACT-FREE tool that
a thin scheduled wrapper invokes. All paths are args; zero person-facts here.

Position: runs BEFORE the inbox-capture stage. It is UPSTREAM of the queue — it writes raw vault
files (additive, like the legacy pipeline), and the inbox-capture stage remains the sole
`queue_tx.py add` author (Stage Contract #4 does not apply here).

Dedupe / fences (delete-independent by design):
  1. routed fence: a source stub already `routed: true` is never re-routed (in-place flag).
  2. URL fence: normalize_url (reused from capture.py for parity) against raws already in the
     destination subfolder for this source — so the same page re-clipped doesn't double-write,
     and the routed copy carries a `url:` that finally populates the engine URL fence downstream.
     (This fence is per-source; cross-source dedupe is capture.py's all-time ledger, downstream.)
Move is delete-INDEPENDENT: write the routed copy (create), stamp the source `routed: true`
in place (overwrite), THEN best-effort-delete the husk. A denied unlink leaves an inert husk
(marked routed) that the next run skips — never blocks, never depends on `mv`/`unlink`.

Observability: each run appends a manifest line (per-source seen/written/skipped/errors) +
a staleness flag when a source reports 0 seen for >= N days — the freeze-detector that turns a
silently-dry source into one visible glance instead of a 9-day blind spot.

Commands:
  python capture_router.py plan --auto-root A --vault-root V --kb familyoffice=02_FamilyOffice ...
        -> read-only; prints JSON {planned, skipped, per_source, totals}. NO writes.
  python capture_router.py run  (same args) --manifest M [--stale-days 3] [--context-log C]
        -> plan -> write routed copies + stamp husks + best-effort delete -> manifest + context-log.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from capture import normalize_url  # reuse for URL-fence parity (G19 §3)
from frontmatter import read_frontmatter  # the one guarded flat-frontmatter reader
import context_log as ctxlog
import url_extract  # A57: chrome_stub "Extract rich" enrichment (fail-soft; CLI-boundary only, see main())

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SOURCES = ["gmail", "webclipper"]
_URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.I)
_TOP_KEY = re.compile(r"^([A-Za-z0-9_\-]+):")
_WEBKIT_EPOCH_OFFSET = 11644473600  # seconds between 1601-01-01 (Chrome epoch) and 1970-01-01


# ─────────────────────────── io helpers (same shape as capture.py) ───────────────────────────
def _longpath(path):
    """Windows MAX_PATH (260) guard: stdlib open()/os.remove raise FileNotFoundError on an absolute
    path at or over the limit unless it carries the \\?\ extended-length prefix — that silent drop
    (a webclipper husk named with the whole clip body, 317-char path) is what logged 'write errors'
    2026-07-11. No-op off Windows, for already-prefixed paths, and for short paths — the happy path
    is unchanged."""
    if os.name == "nt":
        ap = os.path.abspath(path)
        if len(ap) >= 260 and not ap.startswith("\\\\?\\"):
            return "\\\\?\\" + ap
    return path


def _fit_basename(name, dest_dir, limit=255):
    """Cap an over-long routed-destination basename so `dest_dir/<name>` stays under the MAX_PATH
    budget, preserving the extension + an 8-char hash of the full name for uniqueness. A name that
    already fits passes through unchanged. This keeps the *vault* free of >260-char paths (a landmine
    for Obsidian, git, and the two-machine sync) — distinct from _longpath, which only lets the
    router READ an over-long incoming husk whose name we don't control.

    `limit` is the TOTAL-path budget, not the per-component limit. The real `dest_dir`
    (`<vault>/<kb>/raw/inbox/<source>`, ~90 chars) is bounded well below `limit`, so the kept
    component always lands comfortably under the NTFS 255-char per-component ceiling; a
    pathologically deep dest_dir (≥~246 chars) would defeat the shrink, but _write_verify's
    _longpath wrap still lets that write succeed (it just lands long — the bound makes it moot)."""
    budget = limit - len(os.path.abspath(dest_dir)) - 1     # room for the path separator
    if len(name) <= budget:
        return name
    stem, ext = os.path.splitext(name)
    h = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    keep = max(1, budget - len(ext) - 9)                    # 9 = '-' + 8-char hash
    return f"{stem[:keep]}-{h}{ext}"


def _read_text(path):
    try:
        with open(_longpath(path), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _read_json(path):
    """Parse a JSON file, or None if missing/unparseable."""
    try:
        with open(_longpath(path), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_verify(path, text, _open=open):
    """In-place overwrite + read-back verify (vault-watcher defence, NOT a FUSE relic).

    The destination is the live knowledge vault, which is ALSO an Obsidian vault + a git repo
    (hourly env-auto-sync) — so a freshly-written .md is intermittently locked by a watcher: the
    write returns clean but the immediate read-back raises EINVAL / returns a stale view (observed
    2026-06-30, one of 38 files). Retry generously and distinguish a transient read-lock (re-read,
    don't necessarily re-write) from a real content mismatch (re-write)."""
    last = None
    for attempt in range(6):
        try:
            with _open(_longpath(path), "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:                       # transient open/write collision
            last = e
            time.sleep(0.3 * (attempt + 1))
            continue
        for _ in range(5):                         # verify; tolerate a brief watcher read-lock
            if _read_text(path) == text:
                return True
            time.sleep(0.3)
        last = "read-back verify mismatch/locked"
        time.sleep(0.3 * (attempt + 1))
    raise OSError(f"write failed after retries ({last}): {path}")


# ─────────────────────────── pure helpers (the testable core) ───────────────────────────
def is_routed(fm):
    return str(fm.get("routed", "")).strip().lower() == "true"


def route_kb(fm, default_kb):
    """Routing rule: a stub that already carries `kb:` is trusted (stub_kb); otherwise the
    default (default_personal — matches the legacy `routed_by_rule`)."""
    kb = fm.get("kb")
    if kb:
        return kb, "stub_kb"
    return default_kb, "default_personal"


def extract_url(fm):
    """The stub's URL for the dedupe fence. Prefer an explicit `url:`; else a webclipper-style
    `source:` that holds the clip URL (Obsidian Web Clipper's native frontmatter)."""
    u = fm.get("url")
    if u:
        return u
    s = fm.get("source", "")
    if isinstance(s, str) and _URL_RE.match(s.strip()):
        return s.strip()
    return ""


def upsert_frontmatter(text, kv):
    """Set/replace top-level frontmatter keys WITHOUT reparsing the block — so nested YAML
    (webclipper `author:`/`tags:` lists) survives untouched. An existing top-level `key:` line
    is replaced in place; a new key is appended just before the closing `---`."""
    if not text.startswith("---"):
        head = "".join(f"{k}: {v}\n" for k, v in kv.items())
        return "---\n" + head + "---\n" + text
    nl = text.find("\n")
    close = text.find("\n---", 3)
    if nl == -1 or close == -1:
        return text  # malformed; leave as-is
    inner = text[nl + 1:close]
    rest = text[close + 1:]                       # "---\n<body>…"
    lines = inner.split("\n")
    remaining = dict(kv)
    for i, line in enumerate(lines):
        m = _TOP_KEY.match(line)
        if m and m.group(1) in remaining:
            key = m.group(1)
            lines[i] = f"{key}: {remaining.pop(key)}"
    for k, v in remaining.items():
        lines.append(f"{k}: {v}")
    return text[:nl + 1] + "\n".join(lines) + "\n" + rest


def _parse_iso(s):
    try:
        return datetime.strptime(s or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def compute_stale(history, sources, per_source_now, now, stale_days):
    """A source is stale (frozen) when it has 0 seen this run AND its last seen>0 in the manifest
    history is >= stale_days ago. Never-seen-with-no-history is NOT flagged (first runs)."""
    stale = []
    cutoff = now.timestamp() - stale_days * 86400
    for s in sources:
        if per_source_now.get(s, {}).get("seen", 0) > 0:
            continue
        last = None
        for rec in history:
            if rec.get("per_source", {}).get(s, {}).get("seen", 0) > 0:
                ts = _parse_iso(rec.get("ts"))
                if ts and (last is None or ts > last):
                    last = ts
        if last is not None and last.timestamp() < cutoff:
            stale.append(s)
    return stale


# ─────────────────────────── chrome source: Bookmarks-JSON intake (generate, not move) ───────────────────────────
def chrome_date(micros):
    """Chrome's `date_added` (microseconds since 1601-01-01) -> 'YYYY-MM-DD' (UTC). '' on garbage."""
    try:
        unix = int(micros) / 1_000_000 - _WEBKIT_EPOCH_OFFSET
        return datetime.fromtimestamp(unix, timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError, OverflowError):
        return ""


def chrome_host(url):
    """Display host for a bookmark (netloc, lowercased, leading `www.` dropped)."""
    try:
        netloc = urlparse(url).netloc.lower()
    except (ValueError, TypeError):
        return ""
    return netloc[4:] if netloc.startswith("www.") else netloc


def route_chrome(folder_path, folder_hints, default_kb):
    """Chrome routing (OD-2): default_personal, with optional folder-name hints supplied by the
    install (NOT hardcoded — fact-free). A hint matches when its keyword EQUALS a path SEGMENT
    (case-insensitive) — segment-exact, not substring, so `fo` matches the `FO` folder but not
    `footwear`. First matching hint (in list order) wins."""
    segs = {s.strip().lower() for s in (folder_path or "").split("/")}
    for h in (folder_hints or []):
        if str(h.get("keyword", "")).strip().lower() in segs:
            return h.get("kb", default_kb), "folder_hint"
    return default_kb, "default_personal"


def walk_bookmarks(bm):
    """Flatten a Chrome Bookmarks dict to records: guid, url, title, host, folder_path, date_added.
    Folders become a ` / `-joined path (prefixed by the root key + name); only http(s) URLs are kept
    (javascript:/chrome:///file: bookmarks are skipped)."""
    out = []
    for rkey, rnode in (bm or {}).get("roots", {}).items():
        if isinstance(rnode, dict):
            base = [rkey] + ([rnode["name"]] if rnode.get("name") else [])
            _walk_node(rnode, base, out)
    return out


def _walk_node(node, path, out):
    for child in (node.get("children") or []):
        if not isinstance(child, dict):
            continue  # tolerate a malformed (non-dict / null) bookmarks shape, don't crash the run
        if child.get("type") == "folder":
            _walk_node(child, path + [child.get("name", "")], out)
        elif child.get("type") == "url":
            url = child.get("url", "") or ""
            if not url.lower().startswith(("http://", "https://")):
                continue
            out.append({"guid": child.get("guid", ""), "url": url, "title": child.get("name", ""),
                        "host": chrome_host(url), "folder_path": " / ".join(p for p in path if p),
                        "date_added": chrome_date(child.get("date_added", ""))})


def chrome_stub(rec, kb, rule, now_iso, enrich=None):
    """The routed raw/inbox/chrome stub for a bookmark — reproduces the legacy unified-capture shape.
    Title/folder_path are scrubbed of CR/LF so a bookmark name can't inject a premature `---` and
    truncate the frontmatter; bookmark_id is placed BEFORE title as a belt-and-suspenders fence.

    A57: when `enrich` (a url_extract-shaped callable) is supplied, a successful fetch replaces the
    placeholder body with the real content; a failed/absent fetch keeps today's stub exactly
    (never-worse). enrich=None preserves legacy behavior."""
    host, url, date = rec["host"], rec["url"], rec["date_added"]
    title = rec["title"].replace(chr(34), chr(39)).replace("\n", " ").replace("\r", " ")
    fp = rec["folder_path"].replace("\n", " ").replace("\r", " ")
    fm = ("---\nsource: chrome\n"
          f"captured_utc: {now_iso}\nurl: {url}\n"
          f"bookmark_id: {rec['guid']}\n"
          f'title: "{title}"\nauthor: "{host}"\n'
          f'folder_path: "{fp}"\ndate_added: {date}\n'
          f"routed: true\nrouted_to_kb: {kb}\nrouted_at_utc: {now_iso}\nrouted_by_rule: {rule}\n---\n\n")
    legacy_body = (f"# {title}\n\n{host} · bookmarked {date}\n\n"
                   f"Bookmarked link to **{host}**. Saved {date} in Chrome folder `{fp}`. This is a "
                   f"tool/app/reference bookmark captured as a stub; open the source for full content.\n\n"
                   f"[Source]({url})\n")
    if enrich is not None:
        res = enrich(url)
        if res.get("ok") and res.get("markdown", "").strip():
            body = (f"# {title}\n\n{host} · bookmarked {date} · Chrome folder `{fp}`\n\n"
                    f"{res['markdown'].strip()}\n\n[Source]({url})\n")
            return fm + body
    return fm + legacy_body


def _existing_chrome_guids(vault_root, kb_folders):
    guids = set()
    for folder in set(kb_folders.values()):
        for p in glob.glob(os.path.join(vault_root, folder, "raw", "inbox", "chrome", "*.md")):
            g = read_frontmatter(_read_text(p) or "").get("bookmark_id")
            if g:
                guids.add(g)
    return guids


def _chrome_candidates(bookmarks_path, vault_root, kb_folders, default_kb, folder_hints, now, enrich=None):
    """Generate planned chrome stubs for NEW bookmarks (fenced by guid + normalized url).
    `enrich` (A57) is threaded straight through to `chrome_stub`; None (the default, and what every
    existing/offline test passes implicitly) reproduces today's stub with zero network calls."""
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_prefix = now.strftime("%Y-%m-%d")
    ps = {"seen": 0, "written": 0, "skipped_routed": 0, "skipped_dup": 0, "errors": 0}
    planned, skipped, errors = [], [], []
    bm = _read_json(bookmarks_path) if bookmarks_path else None
    if bm is None:
        ps["errors"] += 1
        errors.append({"src": bookmarks_path or "(no --bookmarks)", "error": "Bookmarks JSON unreadable/absent"})
        return planned, skipped, errors, ps
    seen_guids = _existing_chrome_guids(vault_root, kb_folders)
    dest_urls = _dest_urls(vault_root, kb_folders, "chrome")
    batch_guids, batch_urls = set(), set()
    for rec in walk_bookmarks(bm):
        ps["seen"] += 1
        guid = rec["guid"]
        if not guid:
            ps["errors"] += 1
            errors.append({"src": rec["url"], "error": "bookmark missing guid"})
            continue
        if guid in seen_guids or guid in batch_guids:
            ps["skipped_routed"] += 1
            skipped.append({"src": guid, "action": "skip-routed"})
            continue
        nu = normalize_url(rec["url"])
        if nu and (nu in dest_urls or nu in batch_urls):
            ps["skipped_dup"] += 1
            skipped.append({"src": guid, "action": "skip-dup", "url": rec["url"]})
            continue
        kb, rule = route_chrome(rec["folder_path"], folder_hints, default_kb)
        if kb not in kb_folders:
            ps["errors"] += 1
            errors.append({"src": guid, "error": f"unknown kb {kb!r} (not in --kb map)"})
            continue
        dest = os.path.join(vault_root, kb_folders[kb], "raw", "inbox", "chrome", f"{date_prefix}-{guid}.md")
        planned.append({"src": os.path.abspath(bookmarks_path), "dest": os.path.abspath(dest),
                        "source": "chrome", "kb": kb, "rule": rule, "url": rec["url"], "guid": guid,
                        "action": "generate", "content": chrome_stub(rec, kb, rule, now_iso, enrich=enrich)})
        ps["written"] += 1
        batch_guids.add(guid)
        if nu:
            batch_urls.add(nu)
    return planned, skipped, errors, ps


# ─────────────────────────── destination URL set (the fence's right-hand side) ───────────────────────────
def _dest_urls(vault_root, kb_folders, source):
    urls = set()
    for folder in set(kb_folders.values()):
        ddir = os.path.join(vault_root, folder, "raw", "inbox", source)
        for p in glob.glob(os.path.join(ddir, "*.md")):
            nu = normalize_url(extract_url(read_frontmatter(_read_text(p) or "")))
            if nu:
                urls.add(nu)
    return urls


# ─────────────────────────── plan (read-only) ───────────────────────────
def plan(sources, auto_root, vault_root, kb_folders, default_kb, now=None,
         bookmarks_path=None, folder_hints=None, enrich=None):
    """Pure-ish (read-only): decide, per source, write/generate / skip-routed / skip-dup / error.
    `written` here means would-write. No disk writes. `chrome` is a generate-from-JSON intake;
    the other sources are husk-moves out of auto/. `enrich` (A57) passes straight through to
    chrome_stub; None (the default) is today's stub, no network — the CLI (main()) is the only
    caller that wires in the real `url_extract.extract`."""
    now = now or datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    planned, skipped, errors, per_source = [], [], [], {}
    for source in sources:
        if source == "chrome":
            cpl, csk, cer, cps = _chrome_candidates(bookmarks_path, vault_root, kb_folders,
                                                    default_kb, folder_hints, now, enrich=enrich)
            planned.extend(cpl); skipped.extend(csk); errors.extend(cer); per_source["chrome"] = cps
            continue
        sdir = os.path.join(auto_root, source)
        ps = {"seen": 0, "written": 0, "skipped_routed": 0, "skipped_dup": 0, "errors": 0}
        dest_urls = _dest_urls(vault_root, kb_folders, source)
        batch_urls = set()
        for p in sorted(glob.glob(os.path.join(sdir, "*.md"))):
            if os.path.basename(p).startswith("."):
                continue
            ps["seen"] += 1
            text = _read_text(p)
            if text is None:
                ps["errors"] += 1
                errors.append({"src": p, "error": "unreadable"})
                continue
            if "\x00" in text:
                # Fail-loud: a torn/NUL-padded source (upstream corruption) must NOT be copied
                # into the clean raw/inbox. Leave the husk; flag it for repair.
                ps["errors"] += 1
                errors.append({"src": p, "error": f"NUL-corrupt source ({text.count(chr(0))} NUL bytes) — not routed"})
                continue
            if text.startswith("---") and text.find("\n---", 3) == -1:
                # Unclosed frontmatter = a torn/truncated stub. read_frontmatter would return {}
                # -> mis-route to default kb (ignoring kb:) + upsert_frontmatter would no-op
                # (no routed stamp) -> non-convergent re-routing. Refuse it (same class as NUL).
                ps["errors"] += 1
                errors.append({"src": p, "error": "malformed frontmatter (no closing '---') — not routed"})
                continue
            fm = read_frontmatter(text)
            if is_routed(fm):
                ps["skipped_routed"] += 1
                skipped.append({"src": os.path.abspath(p), "action": "skip-routed"})
                continue
            kb, rule = route_kb(fm, default_kb)
            if kb not in kb_folders:
                ps["errors"] += 1
                errors.append({"src": p, "error": f"unknown kb {kb!r} (not in --kb map)"})
                continue
            url = extract_url(fm)
            nu = normalize_url(url)
            if nu and (nu in dest_urls or nu in batch_urls):
                ps["skipped_dup"] += 1
                skipped.append({"src": os.path.abspath(p), "action": "skip-dup", "url": url})
                continue
            dest_dir = os.path.join(vault_root, kb_folders[kb], "raw", "inbox", source)
            dest = os.path.join(dest_dir, _fit_basename(os.path.basename(p), dest_dir))
            planned.append({"src": os.path.abspath(p), "dest": os.path.abspath(dest),
                            "source": source, "kb": kb, "rule": rule, "url": url, "action": "write"})
            ps["written"] += 1
            if nu:
                batch_urls.add(nu)
        per_source[source] = ps
    totals = {k: sum(ps[k] for ps in per_source.values())
              for k in ["seen", "written", "skipped_routed", "skipped_dup", "errors"]}
    return {"stage": "capture-router", "run_id": now.strftime("%Y-%m-%d"), "now_iso": now_iso,
            "planned": planned, "skipped": skipped, "errors": errors,
            "per_source": per_source, "totals": totals}


# ─────────────────────────── run (plan + commit) ───────────────────────────
def run(sources, auto_root, vault_root, kb_folders, default_kb, manifest_path, stale_days=3,
        context_log=None, now=None, delete=os.remove, dry_run=False, _open=open,
        bookmarks_path=None, folder_hints=None, enrich=None):
    now = now or datetime.now(timezone.utc)
    pl = plan(sources, auto_root, vault_root, kb_folders, default_kb, now=now,
              bookmarks_path=bookmarks_path, folder_hints=folder_hints, enrich=enrich)
    now_iso = pl["now_iso"]
    ok = True

    if not dry_run:
        for a in pl["planned"]:
            try:
                if a.get("action") == "generate":
                    # chrome: synthesize the stub into raw/inbox/chrome; no husk to stamp/delete
                    # (the JSON source isn't consumed per-item — the guid fence prevents re-capture).
                    os.makedirs(os.path.dirname(a["dest"]), exist_ok=True)
                    _write_verify(a["dest"], a["content"], _open=_open)
                    continue
                src_text = _read_text(a["src"])
                if src_text is None:
                    raise OSError("source vanished before move")
                # 1) write the routed copy (create)
                add = {"kb": a["kb"], "routed": "true", "routed_to_kb": a["kb"],
                       "routed_at_utc": now_iso, "routed_by_rule": a["rule"]}
                if a["url"]:
                    add["url"] = a["url"]
                os.makedirs(os.path.dirname(a["dest"]), exist_ok=True)
                _write_verify(a["dest"], upsert_frontmatter(src_text, add), _open=_open)
                # 2) stamp the SOURCE husk routed:true in place (overwrite)
                _write_verify(a["src"], upsert_frontmatter(
                    src_text, {"routed": "true", "routed_to_kb": a["kb"]}), _open=_open)
                # 3) best-effort delete; a denied unlink leaves an inert routed husk
                try:
                    delete(_longpath(a["src"]))
                except OSError:
                    pass
            except OSError as e:
                # Truthful counts: a failed write is an error, not a write. The husk is left
                # un-stamped (step 2 never ran) so the next run retries it cleanly.
                ok = False
                a["action"] = "error"
                pl["errors"].append({"src": a["src"], "error": str(e)})
                ps = pl["per_source"].get(a["source"])
                if ps:
                    ps["written"] -= 1
                    ps["errors"] += 1
        pl["totals"] = {k: sum(ps[k] for ps in pl["per_source"].values())
                        for k in ["seen", "written", "skipped_routed", "skipped_dup", "errors"]}

    history = _read_manifest(manifest_path)
    stale = compute_stale(history, sources, pl["per_source"], now, stale_days)
    summary = {"ok": ok and not pl["errors"], "stage": "capture-router", "run_id": pl["run_id"],
               "per_source": pl["per_source"], "totals": pl["totals"], "stale": stale,
               "planned": pl["planned"], "errors": pl["errors"]}

    if not dry_run:
        _append_manifest(manifest_path, {"ts": now_iso, "run_id": pl["run_id"],
                                         "per_source": pl["per_source"], "totals": pl["totals"],
                                         "stale": stale})
        if context_log:
            anomalies = (["stale: " + ", ".join(stale)] if stale else []) + ([] if summary["ok"] else ["write errors"])
            try:
                ctxlog.emit({"ts": now_iso, "stage": "capture-router", "run_id": pl["run_id"],
                             "items_in": pl["totals"]["seen"], "items_out": pl["totals"]["written"],
                             "skipped_routed": pl["totals"]["skipped_routed"],
                             "skipped_dup": pl["totals"]["skipped_dup"], "repairs": [],
                             "anomalies": anomalies, "note": "ok" if summary["ok"] else "write errors"},
                            context_log)
                summary["context_log_ok"] = True
            except (ctxlog.ContextLogWriteError, OSError) as e:
                summary["context_log_ok"] = False
                print("WARNING: context-log emit failed: " + str(e), file=sys.stderr)
    return summary


def _read_manifest(path):
    out = []
    txt = _read_text(path)
    if not txt:
        return out
    for line in txt.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _append_manifest(path, rec):
    """Append-only jsonl, fsync'd, binary so the terminator stays '\\n' on every platform."""
    line = json.dumps(rec, ensure_ascii=False)
    with open(path, "ab") as f:
        f.write((line + "\n").encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())


# ─────────────────────────── CLI ───────────────────────────
def _parse_kb(kb_args):
    out = {}
    for spec in kb_args:
        if "=" not in spec:
            sys.exit(f"--kb expects kb=folder, got {spec!r}")
        kb, folder = spec.split("=", 1)
        out[kb] = folder
    return out


def _parse_hints(hint_args):
    """--folder-hint finance=familyoffice -> [{'keyword':'finance','kb':'familyoffice'}] (chrome)."""
    out = []
    for spec in hint_args:
        if "=" not in spec:
            sys.exit(f"--folder-hint expects keyword=kb, got {spec!r}")
        kw, kb = spec.split("=", 1)
        out.append({"keyword": kw, "kb": kb})
    return out


def _add_common(ap):
    ap.add_argument("--auto-root", required=True, help="…/vault/00_Inbox/auto")
    ap.add_argument("--vault-root", required=True, help="resolved live vault base (…/vault)")
    ap.add_argument("--kb", action="append", default=[], required=True,
                    help="kb=folder, repeatable (e.g. --kb familyoffice=02_FamilyOffice)")
    ap.add_argument("--source", action="append", default=[],
                    help=f"source to route, repeatable (default: {' '.join(DEFAULT_SOURCES)}; add 'chrome')")
    ap.add_argument("--default-kb", default="personal", help="kb for stubs that carry no kb: (default personal)")
    ap.add_argument("--stale-days", type=int, default=3)
    ap.add_argument("--bookmarks", help="path to the Chrome Bookmarks JSON (required when --source chrome)")
    ap.add_argument("--folder-hint", action="append", default=[],
                    help="chrome routing: keyword=kb, repeatable (e.g. --folder-hint finance=familyoffice)")


def _utf8_stdio():
    """Force UTF-8 on stdout/stderr — webclipper stub filenames carry emoji (🧠, 👀) and a native
    Windows console defaults to cp1252, which would crash the JSON print. A non-Windows console is already
    UTF-8, so this only ever helps."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def main(argv=None):
    _utf8_stdio()
    ap = argparse.ArgumentParser(description="aios capture-router — auto/ -> {kb}/raw/inbox/ bridge (B-G19).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("plan", help="read-only; print planned moves + per-source counts")
    _add_common(pp)
    rp = sub.add_parser("run", help="plan + write routed copies + stamp husks + best-effort delete")
    _add_common(rp)
    rp.add_argument("--manifest", required=True, help="append-only jsonl manifest (per-source counts + staleness)")
    rp.add_argument("--context-log")
    args = ap.parse_args(argv)

    kb_folders = _parse_kb(args.kb)
    folder_hints = _parse_hints(args.folder_hint)
    sources = args.source or DEFAULT_SOURCES
    if args.cmd == "plan":
        # plan is a cheap READ-ONLY preview — enrich=None keeps it offline + fast (no 20s/bookmark
        # network fetch). Enrichment fires only on the real `run` (A57 final-review finding #2).
        print(json.dumps(plan(sources, args.auto_root, args.vault_root, kb_folders, args.default_kb,
                              bookmarks_path=args.bookmarks, folder_hints=folder_hints,
                              enrich=None),
                         ensure_ascii=False, indent=2))
        return 0
    summary = run(sources, args.auto_root, args.vault_root, kb_folders, args.default_kb,
                  args.manifest, stale_days=args.stale_days, context_log=args.context_log,
                  bookmarks_path=args.bookmarks, folder_hints=folder_hints,
                  enrich=url_extract.extract)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
