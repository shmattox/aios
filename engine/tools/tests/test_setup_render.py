#!/usr/bin/env python3
"""setup_render.py + brief_session cache-status/resolve_scope test harness (A25).
Scratch; safe to delete."""
import json, os, sys, tempfile, shutil, subprocess, time

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS)
import setup_render
import brief_session as bs

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ok  " if cond else " FAIL ") + name)

TEMPLATE = """# {{ENTITY_NAME}} — Orchestrator ({{DOMAIN_COUNT}} domains)

## 1. Session-Start Ritual

{{SESSION_START_STATE_BLOCK}}

Trigger: `{{BRIEF_TRIGGER}}`

## 2. Session-End Ritual

{{SESSION_END_STATE_BLOCK}}
3. Update knowledge pages for facts locked this session.
4. **Review gate (mandatory).** Dispatch a fresh-context subagent.

## 4. Routing

{{ROUTING_TABLE}}

## 5. Discipline

{{DISCIPLINE_MODULES}}
"""

TOKENS = {
    "ENTITY_NAME": "Test Person",
    "DOMAIN_COUNT": "2",
    "BRIEF_TRIGGER": "Hello there.",
    "ROUTING_TABLE": "| kw | specialist |\n|---|---|\n| a | alpha |",
    "DISCIPLINE_MODULES": "(none enabled)",
    "SESSION_START_STATE_BLOCK": "1. Read the cache.\n2. Select awaiting.\n3. Identify specialist.",
    "SESSION_END_STATE_BLOCK": "1. Append a session record.\n2. Add follow-ups.",
}

d = tempfile.mkdtemp(prefix="sr_")
try:
    # 1. render: full substitution, no leftover, 2-item end block keeps 3./4. as-is
    out, leftover = setup_render.render(TEMPLATE, TOKENS)
    check("all tokens substituted", leftover == [] and "Test Person" in out and "Hello there." in out)
    check("2-item end block: trailing hardcoded items stay 3./4.",
          "3. Update knowledge pages" in out and "4. **Review gate" in out)

    # 2. renumber rule: a 3-item end block renumbers the trailing hardcoded items to 4./5.
    t3 = dict(TOKENS)
    t3["SESSION_END_STATE_BLOCK"] = "1. One.\n2. Two.\n3. Three."
    out3, _ = setup_render.render(TEMPLATE, t3)
    check("3-item end block: trailing items renumber to 4./5.",
          "4. Update knowledge pages" in out3 and "5. **Review gate" in out3)
    check("renumber is scoped — Session-Start numbering untouched",
          "3. Identify specialist." in out3)

    # 3. CLI: guard fails loud on a missing token; --out writes the file
    tpl = os.path.join(d, "tpl.md"); tok = os.path.join(d, "tok.json"); outp = os.path.join(d, "CLAUDE.md")
    open(tpl, "w", encoding="utf-8").write(TEMPLATE)
    bad = dict(TOKENS); bad.pop("ROUTING_TABLE")
    json.dump(bad, open(tok, "w", encoding="utf-8"))
    r = subprocess.run([sys.executable, os.path.join(HARNESS, "setup_render.py"),
                        "--template", tpl, "--tokens", tok, "--out", outp],
                       capture_output=True, text=True)
    check("CLI guard: unresolved token fails loud, nothing shipped",
          r.returncode != 0 and "ROUTING_TABLE" in r.stderr and not os.path.exists(outp))
    json.dump(TOKENS, open(tok, "w", encoding="utf-8"))
    r2 = subprocess.run([sys.executable, os.path.join(HARNESS, "setup_render.py"),
                         "--template", tpl, "--tokens", tok, "--out", outp],
                        capture_output=True, text=True)
    check("CLI renders the file with zero unresolved tokens",
          r2.returncode == 0 and os.path.exists(outp) and "{{" not in open(outp, encoding="utf-8").read())

    # ── brief_session.cache_status (the SKILL's usability boolean as code) ──
    cache = os.path.join(d, "brief-cache.json")
    now = time.time()
    def write_cache(age_min, notion_live):
        gen = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - age_min * 60))
        json.dump({"generated_utc": gen, "source_counts": {"notion_live": notion_live}},
                  open(cache, "w", encoding="utf-8"))
    write_cache(30, True)
    st = bs.cache_status(cache, max_age_min=720, notion_enabled=True, session_has_notion=True,
                         now_epoch=now)
    check("cache-status: fresh + at parity", st["status"] == "fresh")
    write_cache(30, False)
    st2 = bs.cache_status(cache, max_age_min=720, notion_enabled=True, session_has_notion=True,
                          now_epoch=now)
    check("cache-status: minutes old but notion-blind -> degraded",
          st2["status"] == "degraded" and st2["degraded"])
    st3 = bs.cache_status(cache, max_age_min=720, notion_enabled=False, session_has_notion=False,
                          now_epoch=now)
    check("cache-status: notion-blind is FINE for a no-notion install", st3["status"] == "fresh")
    write_cache(800, True)
    st4 = bs.cache_status(cache, max_age_min=720, notion_enabled=True, session_has_notion=True,
                          now_epoch=now)
    check("cache-status: past max_age -> stale", st4["status"] == "stale")
    st5 = bs.cache_status(os.path.join(d, "nope.json"))
    check("cache-status: missing cache", st5["status"] == "missing")

    # ── A93 §1: event-based staleness — a signal newer than generated_utc flips to stale ──
    # even when the age backstop says fresh. Offline: signals are supplied as ISO/epoch, the
    # tool never reaches out. Cache is 30 min old (well within max_age 720).
    write_cache(30, True)
    gen_epoch = now - 30 * 60
    older = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(gen_epoch - 600))   # before the gather
    newer = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(gen_epoch + 600))   # after the gather
    base = dict(max_age_min=720, notion_enabled=False, session_has_notion=False, now_epoch=now)
    # signal 1: walk ledger newer
    s_w = bs.cache_status(cache, session_updated=newer, **base)
    check("cache-status A93: walk-ledger newer than cache -> stale",
          s_w["status"] == "stale" and "walk_ledger" in s_w["stale_signals"])
    # signal 2: changelog tail newer
    s_c = bs.cache_status(cache, changelog_newest=newer, **base)
    check("cache-status A93: changelog newer than cache -> stale",
          s_c["status"] == "stale" and "changelog" in s_c["stale_signals"])
    # signal 3: notion watermark newer
    s_n = bs.cache_status(cache, notion_watermark=newer, **base)
    check("cache-status A93: notion watermark newer than cache -> stale",
          s_n["status"] == "stale" and "notion_watermark" in s_n["stale_signals"])
    # all three OLDER than the cache -> still fresh (already reflected in the gather)
    s_ok = bs.cache_status(cache, session_updated=older, changelog_newest=older,
                           notion_watermark=older, **base)
    check("cache-status A93: signals older than cache stay fresh",
          s_ok["status"] == "fresh" and not s_ok["event_stale"])
    # the age backstop still bites with no signal at all
    write_cache(800, True)
    s_age = bs.cache_status(cache, max_age_min=720, notion_enabled=False,
                            session_has_notion=False, now_epoch=now)
    check("cache-status A93: age backstop still stale with no signal",
          s_age["status"] == "stale" and not s_age["event_stale"])
    # epoch (not just ISO) accepted for a signal
    write_cache(30, True)
    s_ep = bs.cache_status(cache, session_updated=(gen_epoch + 600), **base)
    check("cache-status A93: an epoch signal is accepted too", s_ep["status"] == "stale")

    # file-backed signal readers (what the CLI resolves before calling cache_status)
    sess = os.path.join(d, "brief-session.json")
    json.dump({"updated_utc": newer}, open(sess, "w", encoding="utf-8"))
    check("cache-status A93: _session_updated reads updated_utc",
          bs._session_updated(sess) == bs._iso_epoch(newer))
    check("cache-status A93: _session_updated on a missing file -> None",
          bs._session_updated(os.path.join(d, "no-sess.json")) is None)
    clog = os.path.join(d, "notion-changelog.jsonl")
    with open(clog, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": older}) + "\n")
        f.write("\n")                                   # blank line tolerated
        f.write("{ torn line\n")                        # unparseable line skipped
        f.write(json.dumps({"ts": newer}) + "\n")
    check("cache-status A93: _changelog_newest takes the max ts, skips torn lines",
          bs._changelog_newest(clog) == bs._iso_epoch(newer))
    check("cache-status A93: _changelog_newest on a missing file -> None",
          bs._changelog_newest(os.path.join(d, "no-clog.jsonl")) is None)

    # ── brief_session.resolve_scope (the cwd→silo lever as code) ──
    env_root = os.path.join(d, "env"); vault = os.path.join(env_root, "Vault")
    proj = os.path.join(env_root, "Projects", "family-office", "sub")
    os.makedirs(proj); os.makedirs(os.path.join(vault, "02_FO", "wiki"))
    dm = {"family-office": "familyoffice", "personal": "personal"}
    km = {"familyoffice": "02_FO", "personal": "01_P"}
    check("scope: Projects/<name> maps via domain_map (from a subdir too)",
          bs.resolve_scope(proj, dm) == "familyoffice")
    check("scope: KB-root cwd maps via kb_map",
          bs.resolve_scope(os.path.join(vault, "02_FO", "wiki"), dm, vault_root=vault, kb_map=km)
          == "familyoffice")
    check("scope: unmapped cwd -> default all", bs.resolve_scope(env_root, dm) == "all")
    check("scope: explicit override wins over cwd",
          bs.resolve_scope(proj, dm, override="dev") == "dev")
    check("scope: cache-write is always all (overrides everything)",
          bs.resolve_scope(proj, dm, override="dev", cache_write=True) == "all")

    # ── A26 fact-free acceptance: a 2-domain install passes validate_cache + renders ──
    import copy
    import brief_render as R
    fix_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fixtures", "brief-cache.sample.json")
    fix = json.load(open(fix_path, encoding="utf-8"))
    two = copy.deepcopy(fix)
    for blk in ("station_counts", "stations"):
        for dom in ("familyoffice", "dev"):
            two.get(blk, {}).pop(dom, None)
    ok2, errs2 = bs.validate_cache(two)
    check("A26: a 2-domain cache passes validate_cache (no hardcoded domain set)", ok2)
    check("A26: a 2-domain cache still renders a station", R.render_station(two, "system") != "")

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:", FAIL)
    sys.exit(1 if FAIL else 0)
finally:
    shutil.rmtree(d, ignore_errors=True)
