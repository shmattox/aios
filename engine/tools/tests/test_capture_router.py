#!/usr/bin/env python3
"""capture_router.py test harness — the auto/ -> {kb}/raw/inbox/ bridge, in an isolated
temp vault. Scratch; safe to delete. Run: python tools/tests/test_capture_router.py

Covers (G19 §6 test plan): normalize_url parity with capture; routing-rule table;
URL + routed fences; delete-independent move (denied unlink -> inert husk, next run skips);
end-to-end plan/run with manifest reconciliation + idempotency; staleness freeze-flag;
nested-YAML (webclipper author/tags lists) preserved through the frontmatter transform."""
import json, os, sys, tempfile, shutil, glob
from datetime import datetime, timezone, timedelta

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../engine/tools
sys.path.insert(0, HARNESS)
import capture_router as cr
import capture  # parity source for normalize_url

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ok  " if cond else " FAIL ") + name)

def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

NOW = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc)

def gmail_stub(kb="familyoffice", routed="false", subject="S"):
    return (f"---\nsource: gmail\nkb: {kb}\nfrom: Someone <a@b.com>\n"
            f"subject: {subject}\ndrive_path: n/a\ncaptured_utc: 2026-06-23T09:15:00Z\n"
            f"routed: {routed}\n---\n\nRich gmail body paragraph.\n")

def webclipper_stub(url="https://www.youtube.com/watch?v=abc123"):
    return (f'---\ntitle: "A Clip"\nsource: "{url}"\nauthor:\n  - "[[Ben AI]]"\n'
            f'published: 2026-06-12\ncreated: 2026-06-22\n'
            f'description: "desc"\ntags:\n  - "clippings"\n---\n![]({url})\n\nclip body line\n')

# ─────────────────────────── pure: normalize_url parity ───────────────────────────
for u in ["https://www.GitHub.com/A/b/?utm_source=z&ref=y", "http://x.com/i?id=42&utm_medium=q", ""]:
    check(f"normalize_url parity: {u!r}", cr.normalize_url(u) == capture.normalize_url(u))

# ─────────────────────────── pure: read_frontmatter / is_routed ───────────────────────────
fm = cr.read_frontmatter(gmail_stub(kb="familyoffice", routed="false"))
check("read_frontmatter reads kb", fm.get("kb") == "familyoffice")
check("is_routed false when routed:false", cr.is_routed(fm) is False)
check("is_routed true when routed:true", cr.is_routed(cr.read_frontmatter("---\nrouted: true\n---\nx")) is True)
check("is_routed false when routed absent", cr.is_routed(cr.read_frontmatter("---\ntitle: x\n---\ny")) is False)

# ─────────────────────────── pure: route_kb ───────────────────────────
check("route_kb stub_kb when kb present", cr.route_kb({"kb": "familyoffice"}, "personal") == ("familyoffice", "stub_kb"))
check("route_kb default_personal when kb absent", cr.route_kb({}, "personal") == ("personal", "default_personal"))
check("route_kb honors a non-personal default", cr.route_kb({}, "dev") == ("dev", "default_personal"))

# ─────────────────────────── pure: extract_url ───────────────────────────
check("extract_url: webclipper source-as-url", cr.extract_url({"source": "https://y.com/v"}) == "https://y.com/v")
check("extract_url: explicit url field wins", cr.extract_url({"url": "https://a.com", "source": "gmail"}) == "https://a.com")
check("extract_url: gmail (source not a url) -> ''", cr.extract_url({"source": "gmail"}) == "")
check("extract_url: nothing -> ''", cr.extract_url({}) == "")

# ─────────────────────────── pure: upsert_frontmatter (nested-YAML safe) ───────────────────────────
wc = webclipper_stub("https://y.com/v")
out = cr.upsert_frontmatter(wc, {"routed": "true", "routed_to_kb": "personal", "kb": "personal", "url": "https://y.com/v"})
check("upsert preserves nested author list", '  - "[[Ben AI]]"' in out)
check("upsert preserves nested tags list", '  - "clippings"' in out)
check("upsert sets routed:true", "\nrouted: true\n" in out)
ofm = cr.read_frontmatter(out)
check("upsert adds routed_to_kb", ofm.get("routed_to_kb") == "personal")
check("upsert adds kb", ofm.get("kb") == "personal")
check("upsert preserves body verbatim", out.endswith("clip body line\n"))
# replace, not duplicate, an existing key
out2 = cr.upsert_frontmatter(gmail_stub(routed="false"), {"routed": "true"})
check("upsert replaces existing routed (no duplicate line)",
      out2.count("\nrouted:") == 1 and cr.read_frontmatter(out2)["routed"] == "true")

# ─────────────────────────── I/O: plan + run in a temp vault ───────────────────────────
KB_FOLDERS = {"familyoffice": "02_FamilyOffice", "personal": "01_Personal", "dev": "03_Dev"}
SOURCES = ["gmail", "webclipper"]

d = tempfile.mkdtemp(prefix="caprouter_")
try:
    vault = os.path.join(d, "Vault")
    auto = os.path.join(vault, "00_Inbox", "auto")
    manifest = os.path.join(d, "capture-router-manifest.jsonl")

    # two fresh stubs
    write(os.path.join(auto, "gmail", "2026-06-23-bayview.md"), gmail_stub(kb="familyoffice"))
    write(os.path.join(auto, "webclipper", "a-clip.md"), webclipper_stub("https://y.com/v"))

    # ── plan (read-only) ──
    p = cr.plan(SOURCES, auto, vault, KB_FOLDERS, "personal", now=NOW)
    check("plan: seen=2 across sources", p["totals"]["seen"] == 2)
    check("plan: would-write=2", p["totals"]["written"] == 2)
    check("plan: writes nothing to disk (dry)",
          not glob.glob(os.path.join(vault, "0*", "raw", "inbox", "**", "*.md"), recursive=True))
    gm_plan = next(a for a in p["planned"] if a["src"].endswith("2026-06-23-bayview.md"))
    check("plan: gmail routes to familyoffice by stub_kb",
          gm_plan["kb"] == "familyoffice" and gm_plan["rule"] == "stub_kb")
    check("plan: gmail dest is 02_FamilyOffice/raw/inbox/gmail/",
          gm_plan["dest"].replace(os.sep, "/").endswith("02_FamilyOffice/raw/inbox/gmail/2026-06-23-bayview.md"))
    wc_plan = next(a for a in p["planned"] if a["src"].endswith("a-clip.md"))
    check("plan: webclipper routes to personal by default_personal",
          wc_plan["kb"] == "personal" and wc_plan["rule"] == "default_personal")

    # ── run (commit) ── use a non-removing delete so the husk stays inspectable (mirrors the
    #    real unattended native scheduled path where the husk delete is deferred until an approver is present)
    deferred = []
    r = cr.run(SOURCES, auto, vault, KB_FOLDERS, "personal", manifest, stale_days=3, now=NOW,
               delete=deferred.append)
    check("run: exit ok", r["ok"] is True)
    check("run: wrote 2", r["totals"]["written"] == 2)
    fo_dest = os.path.join(vault, "02_FamilyOffice", "raw", "inbox", "gmail", "2026-06-23-bayview.md")
    pe_dest = os.path.join(vault, "01_Personal", "raw", "inbox", "webclipper", "a-clip.md")
    check("run: gmail routed copy exists", os.path.exists(fo_dest))
    check("run: webclipper routed copy exists", os.path.exists(pe_dest))
    dfm = cr.read_frontmatter(read(fo_dest))
    check("run: dest gmail routed:true + routed_to_kb + rule",
          dfm.get("routed") == "true" and dfm.get("routed_to_kb") == "familyoffice" and dfm.get("routed_by_rule") == "stub_kb")
    check("run: dest gmail has routed_at_utc", bool(dfm.get("routed_at_utc")))
    check("run: dest gmail body preserved", "Rich gmail body paragraph." in read(fo_dest))
    wfm = cr.read_frontmatter(read(pe_dest))
    check("run: dest webclipper got url (fence populated downstream)", wfm.get("url") == "https://y.com/v")
    check("run: dest webclipper body preserved", "clip body line" in read(pe_dest))

    # source husk marked routed:true in place
    src_gm = read(os.path.join(auto, "gmail", "2026-06-23-bayview.md"))
    check("run: source husk stamped routed:true in place", cr.read_frontmatter(src_gm).get("routed") == "true")

    # manifest line written, counts reconcile
    mlines = [json.loads(l) for l in read(manifest).splitlines() if l.strip()]
    check("run: one manifest line appended", len(mlines) == 1)
    msum = mlines[0]
    check("run: manifest seen == written + skipped (reconciles)",
          msum["totals"]["seen"] == msum["totals"]["written"] + msum["totals"]["skipped_routed"] + msum["totals"]["skipped_dup"] + msum["totals"]["errors"])

    check("run: husk delete was attempted on both", len(deferred) == 2)

    # ── idempotent re-run: husks now routed:true -> skip-routed, 0 written ──
    r2 = cr.run(SOURCES, auto, vault, KB_FOLDERS, "personal", manifest, stale_days=3, now=NOW,
                delete=deferred.append)
    check("run: idempotent re-run writes 0", r2["totals"]["written"] == 0)
    check("run: idempotent re-run skips both as routed", r2["totals"]["skipped_routed"] == 2)

    # ── default delete (os.remove) actually unlinks the husk (native-with-approver path) ──
    write(os.path.join(auto, "gmail", "removeme.md"), gmail_stub(kb="dev"))
    cr.run(["gmail"], auto, vault, KB_FOLDERS, "personal", manifest, stale_days=3, now=NOW)
    check("run: default delete unlinks the husk", not os.path.exists(os.path.join(auto, "gmail", "removeme.md")))

finally:
    shutil.rmtree(d, ignore_errors=True)

# ─────────────────────────── URL fence: same page already in destination ───────────────────────────
d2 = tempfile.mkdtemp(prefix="caprouter_url_")
try:
    vault = os.path.join(d2, "Vault")
    auto = os.path.join(vault, "00_Inbox", "auto")
    manifest = os.path.join(d2, "m.jsonl")
    # an existing destination raw already carrying the normalized url
    write(os.path.join(vault, "01_Personal", "raw", "inbox", "webclipper", "existing.md"),
          '---\nsource: webclipper\nurl: "https://y.com/v"\nrouted: true\n---\nold\n')
    # a NEW auto stub for the SAME url (different tracking params/scheme)
    write(os.path.join(auto, "webclipper", "dup.md"), webclipper_stub("http://www.y.com/v/?utm_source=t"))
    p = cr.plan(["webclipper"], auto, vault, KB_FOLDERS, "personal", now=NOW)
    check("url fence: same page already in dest -> skip-dup",
          p["totals"]["written"] == 0 and p["totals"]["skipped_dup"] == 1)
finally:
    shutil.rmtree(d2, ignore_errors=True)

# ─────────────────────────── delete-independent: denied unlink -> inert husk, next run skips ───────────────────────────
d3 = tempfile.mkdtemp(prefix="caprouter_del_")
try:
    vault = os.path.join(d3, "Vault")
    auto = os.path.join(vault, "00_Inbox", "auto")
    manifest = os.path.join(d3, "m.jsonl")
    husk = os.path.join(auto, "gmail", "x.md")
    write(husk, gmail_stub(kb="familyoffice"))

    def deny(_path):
        raise OSError("unlink denied (no approver)")

    r = cr.run(["gmail"], auto, vault, KB_FOLDERS, "personal", manifest, stale_days=3, now=NOW, delete=deny)
    check("delete-independent: routed copy still written despite denied unlink",
          os.path.exists(os.path.join(vault, "02_FamilyOffice", "raw", "inbox", "gmail", "x.md")))
    check("delete-independent: husk remains (delete was denied)", os.path.exists(husk))
    check("delete-independent: husk marked routed:true (inert)", cr.read_frontmatter(read(husk)).get("routed") == "true")
    r2 = cr.run(["gmail"], auto, vault, KB_FOLDERS, "personal", manifest, stale_days=3, now=NOW, delete=deny)
    check("delete-independent: next run skips the inert husk (skip-routed), 0 written",
          r2["totals"]["written"] == 0 and r2["totals"]["skipped_routed"] == 1)
finally:
    shutil.rmtree(d3, ignore_errors=True)

# ─────────────────────────── staleness freeze-flag ───────────────────────────
d4 = tempfile.mkdtemp(prefix="caprouter_stale_")
try:
    vault = os.path.join(d4, "Vault")
    auto = os.path.join(vault, "00_Inbox", "auto")
    manifest = os.path.join(d4, "m.jsonl")
    # seed history: 10 days ago gmail had seen=5; webclipper never seen
    old_iso = (NOW - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    write(manifest, json.dumps({"ts": old_iso, "run_id": "2026-06-20",
          "per_source": {"gmail": {"seen": 5, "written": 5, "skipped_routed": 0, "skipped_dup": 0, "errors": 0},
                         "webclipper": {"seen": 0, "written": 0, "skipped_routed": 0, "skipped_dup": 0, "errors": 0}},
          "totals": {"seen": 5, "written": 5, "skipped_routed": 0, "skipped_dup": 0, "errors": 0}, "stale": []}) + "\n")
    # this run: gmail still 0 (frozen), webclipper gets a fresh stub
    write(os.path.join(auto, "webclipper", "fresh.md"), webclipper_stub("https://y.com/fresh"))
    r = cr.run(SOURCES, auto, vault, KB_FOLDERS, "personal", manifest, stale_days=3, now=NOW)
    check("staleness: gmail flagged stale (0 seen >= 3 days)", "gmail" in r["stale"])
    check("staleness: webclipper NOT stale (fresh this run)", "webclipper" not in r["stale"])
finally:
    shutil.rmtree(d4, ignore_errors=True)

# ─────────────────────────── _write_verify retries a TRANSIENT open/write OSError ───────────────────────────
# Root cause of the 2026-06-30 drain hiccup: a one-off Windows EINVAL (FUSE/AV/indexer touching
# the dest mid-open) bubbled straight out — only a verify-mismatch was retried, not a transient
# open() failure. A scheduled run would hit this nightly.
d5 = tempfile.mkdtemp(prefix="caprouter_wv_")
try:
    target = os.path.join(d5, "out.md")
    calls = {"n": 0}
    real_open = open
    def flaky_open(path, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(22, "Invalid argument (transient)")
        return real_open(path, *a, **k)
    cr._write_verify(target, "payload\n", _open=flaky_open)
    check("_write_verify retries a transient open OSError then succeeds",
          read(target) == "payload\n" and calls["n"] == 2)
finally:
    shutil.rmtree(d5, ignore_errors=True)

# ─────────────────────────── run() reflects a PERMANENT write failure truthfully ───────────────────────────
d6 = tempfile.mkdtemp(prefix="caprouter_fail_")
try:
    vault = os.path.join(d6, "Vault")
    auto = os.path.join(vault, "00_Inbox", "auto")
    manifest = os.path.join(d6, "m.jsonl")
    write(os.path.join(auto, "gmail", "boom.md"), gmail_stub(kb="dev"))
    real_open = open
    def always_fail_dest(path, *a, **k):
        if "raw" in path and "w" in (a[0] if a else k.get("mode", "")):
            raise OSError(22, "Invalid argument (permanent)")
        return real_open(path, *a, **k)
    r = cr.run(["gmail"], auto, vault, KB_FOLDERS, "personal", manifest, stale_days=3, now=NOW, _open=always_fail_dest)
    check("run: permanent dest write failure -> ok False", r["ok"] is False)
    check("run: failed write counted as error, not written",
          r["totals"]["errors"] == 1 and r["totals"]["written"] == 0)
    check("run: failed item's source husk left intact (unstamped) for retry",
          cr.read_frontmatter(read(os.path.join(auto, "gmail", "boom.md"))).get("routed") != "true")
    mrec = [json.loads(l) for l in read(manifest).splitlines() if l.strip()][-1]
    check("run: manifest reconciles after failure (seen == written+skipped+errors)",
          mrec["totals"]["seen"] == mrec["totals"]["written"] + mrec["totals"]["skipped_routed"]
          + mrec["totals"]["skipped_dup"] + mrec["totals"]["errors"])
finally:
    shutil.rmtree(d6, ignore_errors=True)

# ─────────────────────────── NUL-corrupt source is refused, not propagated ───────────────────────────
# A webclipper source landed in auto/ with 929 NUL bytes (torn write upstream); faithfully copying
# it would spread corruption into the clean raw/inbox. Fail-loud: skip + count as error.
d7 = tempfile.mkdtemp(prefix="caprouter_nul_")
try:
    vault = os.path.join(d7, "Vault")
    auto = os.path.join(vault, "00_Inbox", "auto")
    manifest = os.path.join(d7, "m.jsonl")
    write(os.path.join(auto, "gmail", "clean.md"), gmail_stub(kb="dev"))
    write(os.path.join(auto, "gmail", "corrupt.md"), gmail_stub(kb="dev")[:60] + "\x00" * 40 + "\nbody\n")
    p = cr.plan(["gmail"], auto, vault, KB_FOLDERS, "personal", now=NOW)
    check("NUL guard: corrupt source not planned for write", p["totals"]["written"] == 1 and p["totals"]["errors"] == 1)
    r = cr.run(["gmail"], auto, vault, KB_FOLDERS, "personal", manifest, stale_days=3, now=NOW)
    check("NUL guard: corrupt source husk left intact (not routed, not deleted)",
          os.path.exists(os.path.join(auto, "gmail", "corrupt.md")))
    check("NUL guard: clean sibling still routed",
          os.path.exists(os.path.join(vault, "03_Dev", "raw", "inbox", "gmail", "clean.md")))
finally:
    shutil.rmtree(d7, ignore_errors=True)

# ─────────────────────────── M1: malformed (unclosed) frontmatter is refused, not mis-routed ───────────────────────────
# A torn/truncated stub that opens `---` with no closing `---` would otherwise read as empty
# frontmatter -> mis-route to default personal (ignoring kb:), strip routing metadata, and (under
# denied delete) re-route forever reported as ok:true. Fail-loud instead.
d8 = tempfile.mkdtemp(prefix="caprouter_malformed_")
try:
    vault = os.path.join(d8, "Vault")
    auto = os.path.join(vault, "00_Inbox", "auto")
    manifest = os.path.join(d8, "m.jsonl")
    write(os.path.join(auto, "gmail", "torn.md"), "---\nsource: gmail\nkb: familyoffice\nbody with no closing fence\n")
    write(os.path.join(auto, "gmail", "ok.md"), gmail_stub(kb="dev"))
    p = cr.plan(["gmail"], auto, vault, KB_FOLDERS, "personal", now=NOW)
    check("M1: unclosed-frontmatter stub refused (error, not written)",
          p["totals"]["errors"] == 1 and p["totals"]["written"] == 1)
    r = cr.run(["gmail"], auto, vault, KB_FOLDERS, "personal", manifest, stale_days=3, now=NOW, delete=lambda p: None)
    check("M1: run reports ok False on the torn stub", r["ok"] is False)
    check("M1: torn husk left intact (not routed)", os.path.exists(os.path.join(auto, "gmail", "torn.md")))
    check("M1: torn stub NOT mis-routed to personal",
          not os.path.exists(os.path.join(vault, "01_Personal", "raw", "inbox", "gmail", "torn.md")))
    r2 = cr.run(["gmail"], auto, vault, KB_FOLDERS, "personal", manifest, stale_days=3, now=NOW, delete=lambda p: None)
    check("M1: converges — re-run does not re-route the torn stub", r2["totals"]["written"] == 0 and r2["totals"]["errors"] == 1)
finally:
    shutil.rmtree(d8, ignore_errors=True)

# ─────────────────────────── M2: unknown kb refused; husk-stamp-fails-after-durable-dest is safe ───────────────────────────
d9 = tempfile.mkdtemp(prefix="caprouter_m2_")
try:
    vault = os.path.join(d9, "Vault")
    auto = os.path.join(vault, "00_Inbox", "auto")
    manifest = os.path.join(d9, "m.jsonl")
    write(os.path.join(auto, "gmail", "badkb.md"), gmail_stub(kb="nonexistent"))
    p = cr.plan(["gmail"], auto, vault, KB_FOLDERS, "personal", now=NOW)
    check("M2: unknown kb: refused (error, not written)", p["totals"]["errors"] == 1 and p["totals"]["written"] == 0)

    # husk-stamp (step 2, writes under auto/) fails AFTER the dest copy is durably written
    write(os.path.join(auto, "gmail", "deferhusk.md"), gmail_stub(kb="dev"))
    os.remove(os.path.join(auto, "gmail", "badkb.md"))  # clear the error item
    real_open = open
    def fail_husk_write(path, *a, **k):
        mode = a[0] if a else k.get("mode", "")
        if (os.sep + "auto" + os.sep) in path and "w" in mode:
            raise OSError(22, "husk write locked")
        return real_open(path, *a, **k)
    r = cr.run(["gmail"], auto, vault, KB_FOLDERS, "personal", manifest, stale_days=3, now=NOW, _open=fail_husk_write)
    check("M2: dest copy durable even though husk-stamp failed",
          os.path.exists(os.path.join(vault, "03_Dev", "raw", "inbox", "gmail", "deferhusk.md")))
    check("M2: husk-stamp failure surfaced as error (ok False)", r["ok"] is False and r["totals"]["errors"] == 1)
finally:
    shutil.rmtree(d9, ignore_errors=True)

# ─────────────────────────── chrome source: generate-from-Bookmarks-JSON intake ───────────────────────────
# Chrome is a different shape: not husks in auto/, but a Chrome Bookmarks JSON at .state/chrome/Bookmarks.
# The router GENERATES raw/inbox/chrome stubs from new bookmarks and fences by guid (+ url).
def bookmarks_json(entries):
    """entries = [(name,url,guid,date_micros,[folder names])]. Builds a minimal Chrome Bookmarks dict."""
    bar = {"type": "folder", "name": "Bookmarks bar", "guid": "root-bar", "children": []}
    for name, url, guid, micros, folders in entries:
        node = {"type": "url", "name": name, "url": url, "guid": guid, "date_added": str(micros)}
        parent = bar
        for fn in folders:
            sub = next((c for c in parent["children"] if c.get("type") == "folder" and c["name"] == fn), None)
            if sub is None:
                sub = {"type": "folder", "name": fn, "guid": "f-" + fn, "children": []}
                parent["children"].append(sub)
            parent = sub
        parent["children"].append(node)
    return {"roots": {"bookmark_bar": bar, "other": {"type": "folder", "name": "Other bookmarks", "children": []}}}

# pure: webkit microseconds -> date (2021-01-01 00:00 UTC = (1609459200+11644473600)*1e6)
check("chrome_date: webkit micros -> YYYY-MM-DD", cr.chrome_date("13253932800000000") == "2021-01-01")
check("chrome_host: strips www", cr.chrome_host("https://www.example.com/a/b") == "example.com")
check("chrome_host: keeps real subdomain", cr.chrome_host("https://docs.google.com/x") == "docs.google.com")
check("route_chrome: folder-hint match -> that kb", cr.route_chrome("bookmark_bar / Bookmarks bar / Finance",
      [{"keyword": "finance", "kb": "familyoffice"}], "personal") == ("familyoffice", "folder_hint"))
check("route_chrome: no hint -> default_personal",
      cr.route_chrome("bookmark_bar / Bookmarks bar / Misc", [], "personal") == ("personal", "default_personal"))
check("route_chrome: short keyword matches a SEGMENT (FO), not a substring",
      cr.route_chrome("bookmark_bar / Bookmarks bar / FO / Real Estate",
                      [{"keyword": "fo", "kb": "familyoffice"}], "personal") == ("familyoffice", "folder_hint"))
check("route_chrome: keyword does NOT substring-match a segment (footwear !~ fo)",
      cr.route_chrome("bookmark_bar / Bookmarks bar / Personal / Footwear",
                      [{"keyword": "fo", "kb": "familyoffice"}], "personal") == ("personal", "default_personal"))

# walk: nested folders -> folder_path; non-http skipped
bm = bookmarks_json([
    ("Seams Portal", "https://portal.seams.com/x", "g1", 13253932800000000, ["Finance"]),
    ("Top Level", "https://top.example.com", "g2", 13253932800000000, []),
    ("A JS bookmarklet", "javascript:void(0)", "g3", 13253932800000000, []),
])
recs = cr.walk_bookmarks(bm)
check("walk_bookmarks: extracts http bookmarks, skips javascript:", len(recs) == 2)
r_fin = next(r for r in recs if r["guid"] == "g1")
check("walk_bookmarks: folder_path includes root + nesting",
      r_fin["folder_path"] == "bookmark_bar / Bookmarks bar / Finance")
check("walk_bookmarks: record carries url/title/host/date", r_fin["url"] == "https://portal.seams.com/x"
      and r_fin["title"] == "Seams Portal" and r_fin["host"] == "portal.seams.com" and r_fin["date_added"] == "2021-01-01")

d10 = tempfile.mkdtemp(prefix="caprouter_chrome_")
try:
    vault = os.path.join(d10, "Vault")
    auto = os.path.join(vault, "00_Inbox", "auto")
    manifest = os.path.join(d10, "m.jsonl")
    bmpath = os.path.join(vault, "00_Inbox", ".state", "chrome", "Bookmarks")
    hints = [{"keyword": "finance", "kb": "familyoffice"}]
    write(bmpath, json.dumps(bookmarks_json([
        ("Seams Portal", "https://portal.seams.com/x", "g1", 13253932800000000, ["Finance"]),
        ("Personal Tool", "https://tool.example.com", "g2", 13253932800000000, ["Misc"]),
    ])))
    # plan
    p = cr.plan(["chrome"], auto, vault, KB_FOLDERS, "personal", now=NOW, bookmarks_path=bmpath, folder_hints=hints)
    check("chrome plan: 2 new bookmarks would-generate", p["totals"]["written"] == 2)
    g1 = next(a for a in p["planned"] if a.get("guid") == "g1")
    check("chrome plan: Finance bookmark routes familyoffice by folder_hint",
          g1["kb"] == "familyoffice" and g1["rule"] == "folder_hint" and g1.get("action") == "generate")
    # run
    r = cr.run(["chrome"], auto, vault, KB_FOLDERS, "personal", manifest, stale_days=3, now=NOW,
               bookmarks_path=bmpath, folder_hints=hints)
    fo_chrome = os.path.join(vault, "02_FamilyOffice", "raw", "inbox", "chrome")
    pe_chrome = os.path.join(vault, "01_Personal", "raw", "inbox", "chrome")
    g1files = glob.glob(os.path.join(fo_chrome, "*g1.md"))
    check("chrome run: Finance bookmark generated under FO/raw/inbox/chrome", len(g1files) == 1)
    cfm = cr.read_frontmatter(read(g1files[0]))
    check("chrome run: stub has source:chrome + routed:true + bookmark_id + url",
          cfm.get("source") == "chrome" and cfm.get("routed") == "true" and cfm.get("bookmark_id") == "g1" and "seams.com" in cfm.get("url", ""))
    check("chrome run: stub body has Source link", "[Source]" in read(g1files[0]))
    check("chrome run: personal bookmark under Personal/raw/inbox/chrome", len(glob.glob(os.path.join(pe_chrome, "*g2.md"))) == 1)
    # idempotent: guids now present in dest -> 0 generated
    r2 = cr.run(["chrome"], auto, vault, KB_FOLDERS, "personal", manifest, stale_days=3, now=NOW,
                bookmarks_path=bmpath, folder_hints=hints)
    check("chrome run: idempotent re-run generates 0 (guid fence)", r2["totals"]["written"] == 0 and r2["totals"]["skipped_routed"] == 2)
    mrec = [json.loads(l) for l in read(manifest).splitlines() if l.strip()][0]
    check("chrome run: manifest reconciles (seen == written+skipped+errors)",
          mrec["totals"]["seen"] == mrec["totals"]["written"] + mrec["totals"]["skipped_routed"]
          + mrec["totals"]["skipped_dup"] + mrec["totals"]["errors"])
finally:
    shutil.rmtree(d10, ignore_errors=True)

# review hardening: a title with an embedded newline/`---` must not break the generated frontmatter
_evil = cr.chrome_stub({"guid": "g9", "url": "https://x.com", "title": "Bad\n---\ninjected: yes",
                        "host": "x.com", "folder_path": "a / b", "date_added": "2021-01-01"},
                       "personal", "default_personal", "2026-06-30T00:00:00Z")
check("chrome_stub: newline in title can't break frontmatter (bookmark_id still parses)",
      cr.read_frontmatter(_evil).get("bookmark_id") == "g9")

# ─────────────────────────── A57 Task 2: chrome_stub enrichment (fail-soft) ───────────────────────────
def _a57_rec():
    return {"host": "example.com", "url": "https://example.com/a", "date_added": "2026-07-10",
            "title": "A Thing", "folder_path": "General Mgmt", "guid": "g1"}

_s_unenriched = cr.chrome_stub(_a57_rec(), "gm", "default", "2026-07-10T00:00:00Z")   # no enrich
check("chrome_stub: unenriched (enrich=None) is today's shape", "open the source for full content" in _s_unenriched)
check("chrome_stub: unenriched frontmatter intact", "url: https://example.com/a" in _s_unenriched)

_enrich_ok = lambda url: {"ok": True, "markdown": "## Real Heading\n\nfull article body", "reason": "ok"}
_s_enriched = cr.chrome_stub(_a57_rec(), "gm", "default", "2026-07-10T00:00:00Z", enrich=_enrich_ok)
check("chrome_stub: enriched folds in fetched markdown", "full article body" in _s_enriched)
check("chrome_stub: enriched frontmatter intact", "url: https://example.com/a" in _s_enriched)
check("chrome_stub: enriched keeps the Source link", "[Source](https://example.com/a)" in _s_enriched)

_enrich_fail = lambda url: {"ok": False, "markdown": "", "reason": "timeout>20s"}
_s_failsoft = cr.chrome_stub(_a57_rec(), "gm", "default", "2026-07-10T00:00:00Z", enrich=_enrich_fail)
check("chrome_stub: enrich fail-soft is never worse (today's body exactly)",
      "open the source for full content" in _s_failsoft)

# review hardening: malformed Bookmarks shape (non-dict child, null children) must not crash the run
_bad_bm = {"roots": {"bookmark_bar": {"type": "folder", "name": "B", "children": [
    "i am not a dict",
    {"type": "url", "name": "ok", "url": "https://ok.com", "guid": "gok", "date_added": "13253932800000000"},
    {"type": "folder", "name": "F", "children": None},
]}}}
_recs = cr.walk_bookmarks(_bad_bm)
check("walk_bookmarks: tolerates non-dict child + null children (no crash)",
      len(_recs) == 1 and _recs[0]["guid"] == "gok")

# review hardening: empty-guid bookmark is skipped as an error, never written blank
d11 = tempfile.mkdtemp(prefix="caprouter_noguid_")
try:
    vault = os.path.join(d11, "Vault")
    bmp = os.path.join(vault, "00_Inbox", ".state", "chrome", "Bookmarks")
    write(bmp, json.dumps(bookmarks_json([("NoGuid", "https://ng.com", "", 13253932800000000, [])])))
    p = cr.plan(["chrome"], os.path.join(vault, "00_Inbox", "auto"), vault, KB_FOLDERS, "personal",
                now=NOW, bookmarks_path=bmp, folder_hints=[])
    check("chrome: empty-guid bookmark -> error, not written",
          p["totals"]["errors"] == 1 and p["totals"]["written"] == 0)
finally:
    shutil.rmtree(d11, ignore_errors=True)

# ─────────────────────────── A58: Windows long-path safety + dest basename fit ───────────────────────────
# Regression: an inbox husk whose full path exceeds Windows MAX_PATH (260) made _read_text raise
# FileNotFoundError -> None -> {"error":"unreadable"} -> ok:false, exit 1 (observed 2026-07-11: an
# Obsidian Web-Clipper file named with the whole tweet body, 248-char basename / 317-char path).

# _longpath: no-op for short/relative paths; \\?\-prefix a >260 absolute path on Windows only.
check("_longpath no-ops a short path", cr._longpath("x.md") == "x.md")
_lp = os.path.join(os.path.abspath(os.sep), "d" * 300, "file.md")   # unambiguously > 260 chars
_res = cr._longpath(_lp)
if os.name == "nt":
    check("_longpath prefixes a >260 absolute path on Windows",
          _res.startswith("\\\\?\\") and _res.endswith("file.md"))
else:
    check("_longpath is a no-op off Windows", _res == _lp)

# _fit_basename: cap an over-long basename so the routed dest path stays under 260, keeping the
# extension + a hash tail for uniqueness; a short name passes through untouched.
_dir = os.path.join(os.path.abspath(os.sep), "Vault", "03_GeneralManagement", "raw", "inbox", "webclipper")
_n1 = "Amanda Orson on X " + "x" * 250 + ".md"
_n2 = "Amanda Orson on X " + "y" * 250 + ".md"
_b1, _b2 = cr._fit_basename(_n1, _dir), cr._fit_basename(_n2, _dir)
check("_fit_basename caps an over-long name to fit under 260", len(os.path.join(_dir, _b1)) < 260)
check("_fit_basename preserves the extension", _b1.endswith(".md"))
check("_fit_basename keeps two distinct long names distinct", _b1 != _b2)
check("_fit_basename no-ops a short name", cr._fit_basename("short.md", _dir) == "short.md")

# End-to-end (Windows only — a >260 path can't even be created without the prefix elsewhere):
# plant a real over-long webclipper husk and assert plan() reads + routes it, dest fits under 260.
if os.name == "nt":
    _dlp = tempfile.mkdtemp(prefix="caprouter_longpath_")
    try:
        _vault = os.path.join(_dlp, "Vault")
        _auto = os.path.join(_vault, "00_Inbox", "auto")
        _wdir = os.path.join(_auto, "webclipper")
        os.makedirs(_wdir, exist_ok=True)
        # basename < 255 (NTFS per-component limit — even \\?\ can't exceed it) but the deep temp
        # dir pushes the TOTAL path over 260, reproducing the real 2026-07-11 husk (248-char name /
        # 317-char path). len(_wdir) is ~90+, so a 231-char basename clears 260 comfortably.
        _long = "Amanda Orson on X " + "z" * 210 + ".md"          # 231-char basename, creatable
        _husk = os.path.join(_wdir, _long)
        with open(cr._longpath(_husk), "w", encoding="utf-8") as f:  # create via the prefix
            f.write(webclipper_stub(url="https://x.com/amandaorson/status/2075218531705037132"))
        _pl = cr.plan(["webclipper"], _auto, _vault, KB_FOLDERS, "personal", now=NOW)
        check("long-path husk: read + routed, zero 'unreadable' errors",
              _pl["totals"]["errors"] == 0 and _pl["totals"]["written"] == 1)
        check("long-path husk: planned dest fits under 260",
              _pl["planned"] and len(_pl["planned"][0]["dest"]) < 260)
    finally:
        shutil.rmtree(cr._longpath(_dlp), ignore_errors=True)

# run() over a long-path src — covers the _longpath wiring into _write_verify (husk stamp) + the
# husk delete(), which plan() alone never exercises. Guards against a future unwiring that would
# leave the src un-stamped/undeletable -> silently re-routed next run -> a duplicate in the vault.
if os.name == "nt":
    _dr = tempfile.mkdtemp(prefix="caprouter_longrun_")
    try:
        _v = os.path.join(_dr, "Vault")
        _a = os.path.join(_v, "00_Inbox", "auto")
        _wd = os.path.join(_a, "webclipper")
        os.makedirs(_wd, exist_ok=True)
        _hk = os.path.join(_wd, "Amanda Orson on X " + "z" * 210 + ".md")
        with open(cr._longpath(_hk), "w", encoding="utf-8") as f:
            f.write(webclipper_stub(url="https://x.com/a/status/12345"))
        _mf = os.path.join(_dr, "m.jsonl")
        _r = cr.run(["webclipper"], _a, _v, KB_FOLDERS, "personal", _mf, stale_days=3, now=NOW)
        _routed = glob.glob(os.path.join(_v, "01_Personal", "raw", "inbox", "webclipper", "*.md"))
        check("run long src: ok, one routed copy written, husk deleted",
              _r["ok"] and _r["totals"]["written"] == 1 and len(_routed) == 1
              and not os.path.exists(cr._longpath(_hk)))
        check("run long src: routed dest basename <= 255 (NTFS per-component limit)",
              len(os.path.basename(_routed[0])) <= 255)
        _r2 = cr.run(["webclipper"], _a, _v, KB_FOLDERS, "personal", _mf, stale_days=3, now=NOW)
        check("run long src: rerun writes 0 (idempotent, no duplicate)", _r2["totals"]["written"] == 0)
    finally:
        shutil.rmtree(cr._longpath(_dr), ignore_errors=True)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
sys.exit(1 if FAIL else 0)
