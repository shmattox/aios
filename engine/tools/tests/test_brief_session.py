#!/usr/bin/env python3
"""Hermetic tests for brief_session.py (the brief-walk ledger helper).

Covers: new_walk schema; resume_or_new detection; record_decision counts; record_deferral
carryover; advance transitions + completion; start_over archive (increment on repeat);
validate_cache pass/fail for every defect class.

Hermetic: all state files live under tempfile.mkdtemp(); a real install is never touched.
Run:  python tools/tests/test_brief_session.py    (exit 0 = all green)
"""
import json
import os
import sys
import tempfile
import shutil
import time

HERE  = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)
import brief_session as bs

PASS, FAIL = [], []

def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ok  " if cond else " FAIL ") + name)


d = tempfile.mkdtemp(prefix="brief_sess_test_")
try:
    state_path   = os.path.join(d, "brief-session.json")
    archive_dir  = os.path.join(d, "brief-sessions")
    state_path2  = os.path.join(d, "brief-session2.json")   # second ledger for carryover test

    ORDER = ["system", "personal", "familyoffice", "dev"]
    SEED  = {"system": 5, "personal": 3, "familyoffice": 6, "dev": 2}

    # ── 1. new_walk creates correct schema ────────────────────────────────────────────
    print("\n== 1. new_walk schema ==")
    ledger = bs.new_walk(state_path, "2026-06-22", ORDER, SEED)
    check("new_walk: file written on disk",                os.path.exists(state_path))
    check("new_walk: walk_id",                             ledger["walk_id"] == "2026-06-22")
    check("new_walk: status in_progress",                  ledger["status"] == "in_progress")
    check("new_walk: station_order preserved",             ledger["station_order"] == ORDER)
    check("new_walk: current_station = first",             ledger["current_station"] == "system")
    check("new_walk: all 4 stations present",              set(ledger["stations"].keys()) == set(ORDER))
    check("new_walk: first station in_progress",           ledger["stations"]["system"]["status"] == "in_progress")
    check("new_walk: remaining stations pending",
          all(ledger["stations"][s]["status"] == "pending" for s in ORDER[1:]))
    check("new_walk: items_total from seed",               ledger["stations"]["system"]["items_total"] == 5)
    check("new_walk: decided starts 0",                    ledger["stations"]["system"]["decided"] == 0)
    check("new_walk: deferred starts 0",                   ledger["stations"]["system"]["deferred"] == 0)
    check("new_walk: decisions list empty",                ledger["decisions"] == [])
    check("new_walk: deferrals list empty",                ledger["deferrals"] == [])
    check("new_walk: started_utc present",                 bool(ledger.get("started_utc")))
    check("new_walk: updated_utc present",                 bool(ledger.get("updated_utc")))

    # file is valid JSON on disk
    on_disk = json.load(open(state_path, encoding="utf-8"))
    check("new_walk: on-disk file parses + matches",       on_disk["walk_id"] == "2026-06-22")

    # ── 2. resume_or_new: in_progress → resume / fresh → new ──────────────────────────
    print("\n== 2. resume_or_new ==")
    mode, returned = bs.resume_or_new(state_path, "2026-06-22", ORDER, SEED)
    check("resume_or_new: detects in_progress -> 'resume'",  mode == "resume")
    check("resume_or_new: returns the existing ledger",       returned["walk_id"] == "2026-06-22")

    # A fresh path should produce "new"
    fresh_path = os.path.join(d, "fresh-session.json")
    mode2, ledger2 = bs.resume_or_new(fresh_path, "2026-06-23", ORDER, SEED)
    check("resume_or_new: absent ledger -> 'new'",            mode2 == "new")
    check("resume_or_new: new ledger written to disk",        os.path.exists(fresh_path))
    check("resume_or_new: new ledger walk_id correct",        ledger2["walk_id"] == "2026-06-23")

    # ── 3. record_decision bumps counts + appends ──────────────────────────────────────
    print("\n== 3. record_decision ==")
    bs.record_decision(state_path, "sys-item-1", "Fix backup sync",
                       "system", "system", "Queued maintenance task", executed=True,
                       thread="state/threads/sys-item-1.md")
    updated = bs.load(state_path)
    check("record_decision: decision appended",               len(updated["decisions"]) == 1)
    check("record_decision: station decided bumped to 1",     updated["stations"]["system"]["decided"] == 1)
    d0 = updated["decisions"][0]
    check("record_decision: item_id stored",                  d0["item_id"] == "sys-item-1")
    check("record_decision: title stored",                    d0["title"] == "Fix backup sync")
    check("record_decision: choice stored",                   d0["choice"] == "system")
    check("record_decision: executed stored",                 d0["executed"] is True)
    check("record_decision: thread stored",                   d0.get("thread") == "state/threads/sys-item-1.md")
    check("record_decision: ts present",                      bool(d0.get("ts")))
    check("record_decision: updated_utc refreshed",          bool(updated.get("updated_utc")))

    # Second decision on same station
    bs.record_decision(state_path, "sys-item-2", "Doc drift flag",
                       "system", "claude", "Deferred to next week", executed=False)
    updated2 = bs.load(state_path)
    check("record_decision: second decision bumps decided to 2",
          updated2["stations"]["system"]["decided"] == 2)
    check("record_decision: both decisions present",          len(updated2["decisions"]) == 2)

    # ── 4. record_deferral stores one-word reason ──────────────────────────────────────
    print("\n== 4. record_deferral ==")
    bs.record_deferral(state_path, "sys-item-3", "Review pipeline flags",
                       "system", "timing", "2026-06-22")
    deferred = bs.load(state_path)
    check("record_deferral: deferral appended",               len(deferred["deferrals"]) == 1)
    check("record_deferral: station deferred bumped to 1",    deferred["stations"]["system"]["deferred"] == 1)
    def0 = deferred["deferrals"][0]
    check("record_deferral: item_id stored",                  def0["item_id"] == "sys-item-3")
    check("record_deferral: reason stored",                   def0["reason"] == "timing")
    check("record_deferral: deferred_on stored",              def0["deferred_on"] == "2026-06-22")
    check("record_deferral: resurface = next-walk",           def0["resurface"] == "next-walk")

    # ── 4b. Carryover deferrals surface in the NEXT walk ──────────────────────────────
    print("\n== 4b. carryover deferrals ==")
    # The current ledger has 1 deferral with resurface=next-walk.
    # Mark it complete so resume_or_new creates a new walk.
    ledger_tmp = bs.load(state_path)
    ledger_tmp["status"] = "complete"
    import json as _json
    with open(state_path, "w", encoding="utf-8") as f:
        f.write(_json.dumps(ledger_tmp, indent=2))

    mode3, ledger3 = bs.resume_or_new(state_path, "2026-06-23", ORDER, SEED)
    check("carryover: complete ledger -> 'new'",              mode3 == "new")
    check("carryover: deferral carried into new walk",        len(ledger3["deferrals"]) == 1)
    check("carryover: carried deferral has correct reason",   ledger3["deferrals"][0]["reason"] == "timing")

    # Reset for advance tests — create a fresh ledger
    os.remove(state_path)

    # ── 5. advance transitions stations in order and flips status:complete ────────────
    print("\n== 5. advance ==")
    bs.new_walk(state_path, "2026-06-22", ORDER, {"system":2,"personal":1,"familyoffice":0,"dev":1})

    # advance from system → personal
    next_s = bs.advance(state_path)
    adv1 = bs.load(state_path)
    check("advance: returns next station",                    next_s == "personal")
    check("advance: system marked complete",                  adv1["stations"]["system"]["status"] == "complete")
    check("advance: personal set to in_progress",             adv1["stations"]["personal"]["status"] == "in_progress")
    check("advance: current_station updated",                 adv1["current_station"] == "personal")
    check("advance: ledger still in_progress",                adv1["status"] == "in_progress")

    # advance → familyoffice
    next_s2 = bs.advance(state_path)
    check("advance: step 2 returns familyoffice",             next_s2 == "familyoffice")

    # advance → dev
    next_s3 = bs.advance(state_path)
    check("advance: step 3 returns dev",                      next_s3 == "dev")

    # advance past last station → complete
    next_s4 = bs.advance(state_path)
    final = bs.load(state_path)
    check("advance: returns None when complete",              next_s4 is None)
    check("advance: ledger status = complete",                final["status"] == "complete")
    check("advance: current_station = None when complete",    final["current_station"] is None)
    check("advance: all stations complete",
          all(final["stations"][s]["status"] == "complete" for s in ORDER))

    # ── 6. start_over archives and increments ─────────────────────────────────────────
    print("\n== 6. start_over ==")
    # Create a fresh in_progress ledger to archive
    os.remove(state_path)
    bs.new_walk(state_path, "2026-06-22", ORDER, SEED)
    bs.start_over(state_path, archive_dir)

    archived = os.listdir(archive_dir)
    check("start_over: archive dir created",                  os.path.isdir(archive_dir))
    check("start_over: archived file exists",                 len(archived) == 1)
    check("start_over: archived filename pattern",            archived[0] == "2026-06-22-1.json")
    check("start_over: live ledger removed",                  not os.path.exists(state_path))

    # Repeat start_over with SAME walk_id -> n increments to 2
    bs.new_walk(state_path, "2026-06-22", ORDER, SEED)
    bs.start_over(state_path, archive_dir)
    archived2 = sorted(os.listdir(archive_dir))
    check("start_over: second archive does not clobber first", len(archived2) == 2)
    check("start_over: second archive filename n=2",          "2026-06-22-2.json" in archived2)

    # After start_over, new_walk can create a fresh ledger
    bs.new_walk(state_path, "2026-06-23", ORDER, SEED)
    check("start_over: new_walk succeeds after start_over",   bs.load(state_path)["walk_id"] == "2026-06-23")

    # ── 7. validate_cache ─────────────────────────────────────────────────────────────
    print("\n== 7. validate_cache ==")

    def good_item(domain="system", sv_grade=None, sv_cite=True):
        sv = None
        if sv_grade is not None:
            sv = {"grade": sv_grade, "text": "Take action X"}
            if sv_cite and sv_grade in ("1", "2a"):
                sv["cite"] = "Decision log 2026-01-01"
        return {
            "item_id": "F1",
            "title": "Fix backup sync",
            "domain": domain,
            "claude_voice": {"text": "Consider reviewing the backup config."},
            "system_voice": sv,
        }

    good_cache = {
        "station_counts": {"system": 3, "personal": 2, "familyoffice": 4, "dev": 1},
        "stations": {
            "system":       [good_item("system", "1")],
            "personal":     [good_item("personal", "2a")],
            "familyoffice": [good_item("familyoffice", "2b")],
            "dev":          [good_item("dev", None)],  # Grade 0 — system_voice=null
        },
        "act": [],   # REQUIRED: a real gather always writes the key (absent == stale writer)
    }

    ok, errs = bs.validate_cache(good_cache)
    check("validate_cache: passes a correct cache",           ok is True and errs == [])

    # Grade 0 (null system_voice) explicitly accepted
    grade0_cache = {
        "station_counts": {"system": 1, "personal": 0, "familyoffice": 0, "dev": 0},
        "stations": {
            "system":       [good_item("system", None)],  # system_voice = null
            "personal":     [],
            "familyoffice": [],
            "dev":          [],
        },
        "act": [],
    }
    ok0, errs0 = bs.validate_cache(grade0_cache)
    check("validate_cache: Grade-0 (null system_voice) accepted",  ok0 is True)

    # Missing station_counts
    bad_sc = dict(good_cache); bad_sc.pop("station_counts")
    ok_sc, e_sc = bs.validate_cache(bad_sc)
    check("validate_cache: missing station_counts -> fail",   ok_sc is False and any("station_counts" in e for e in e_sc))

    # Missing a domain from station_counts
    bad_sc2 = dict(good_cache)
    bad_sc2["station_counts"] = {"system": 1, "personal": 0, "familyoffice": 0}  # missing dev
    ok_sc2, e_sc2 = bs.validate_cache(bad_sc2)
    check("validate_cache: station_counts missing 'dev' -> fail",
          ok_sc2 is False and any("dev" in e for e in e_sc2))

    # Missing stations block
    bad_st = dict(good_cache); bad_st.pop("stations")
    ok_st, e_st = bs.validate_cache(bad_st)
    check("validate_cache: missing stations block -> fail",   ok_st is False)

    # Item missing title
    bad_title = {
        "station_counts": {"system": 1, "personal": 0, "familyoffice": 0, "dev": 0},
        "stations": {
            "system":       [{"item_id": "X", "domain": "system",
                              "claude_voice": {"text": "Do it"}, "system_voice": None}],
            "personal": [], "familyoffice": [], "dev": [],
        },
    }
    ok_bt, e_bt = bs.validate_cache(bad_title)
    check("validate_cache: item missing 'title' -> fail",     ok_bt is False and any("title" in e for e in e_bt))

    # Item missing domain
    bad_dom = {
        "station_counts": {"system": 1, "personal": 0, "familyoffice": 0, "dev": 0},
        "stations": {
            "system":       [{"item_id": "X", "title": "T",
                              "claude_voice": {"text": "Do it"}, "system_voice": None}],
            "personal": [], "familyoffice": [], "dev": [],
        },
    }
    ok_bd, e_bd = bs.validate_cache(bad_dom)
    check("validate_cache: item missing 'domain' -> fail",    ok_bd is False and any("domain" in e for e in e_bd))

    # Missing claude_voice
    bad_cv = {
        "station_counts": {"system": 1, "personal": 0, "familyoffice": 0, "dev": 0},
        "stations": {
            "system":       [{"item_id": "X", "title": "T", "domain": "system", "system_voice": None}],
            "personal": [], "familyoffice": [], "dev": [],
        },
    }
    ok_cv, e_cv = bs.validate_cache(bad_cv)
    check("validate_cache: missing claude_voice -> fail",     ok_cv is False and any("claude_voice" in e for e in e_cv))

    # claude_voice present but empty text (the exact gate the deterministic renderer relies on)
    bad_cv_text = {
        "station_counts": {"system": 1, "personal": 0, "familyoffice": 0, "dev": 0},
        "stations": {
            "system":       [{"item_id": "X", "title": "T", "domain": "system",
                              "claude_voice": {}, "system_voice": None}],
            "personal": [], "familyoffice": [], "dev": [],
        },
    }
    ok_cvt, e_cvt = bs.validate_cache(bad_cv_text)
    check("validate_cache: claude_voice present but no .text -> fail",
          ok_cvt is False and any("claude_voice.text" in e for e in e_cvt))

    # Bad grade enum (not "1", "2a", "2b")
    bad_grade = {
        "station_counts": {"system": 1, "personal": 0, "familyoffice": 0, "dev": 0},
        "stations": {
            "system":       [{
                "item_id": "X", "title": "T", "domain": "system",
                "claude_voice": {"text": "Do it"},
                "system_voice": {"grade": "3", "text": "Bad grade", "cite": "x"},
            }],
            "personal": [], "familyoffice": [], "dev": [],
        },
    }
    ok_bg, e_bg = bs.validate_cache(bad_grade)
    check("validate_cache: bad grade enum -> fail",           ok_bg is False and any("grade" in e for e in e_bg))

    # Grade "1" missing cite
    bad_cite = {
        "station_counts": {"system": 1, "personal": 0, "familyoffice": 0, "dev": 0},
        "stations": {
            "system":       [{
                "item_id": "X", "title": "T", "domain": "system",
                "claude_voice": {"text": "Do it"},
                "system_voice": {"grade": "1", "text": "Your system says X"},  # missing cite
            }],
            "personal": [], "familyoffice": [], "dev": [],
        },
    }
    ok_bc, e_bc = bs.validate_cache(bad_cite)
    check("validate_cache: grade '1' missing cite -> fail",   ok_bc is False and any("cite" in e for e in e_bc))

    # Grade "2a" missing cite
    bad_cite2a = {
        "station_counts": {"system": 1, "personal": 0, "familyoffice": 0, "dev": 0},
        "stations": {
            "system":       [{
                "item_id": "X", "title": "T", "domain": "system",
                "claude_voice": {"text": "Do it"},
                "system_voice": {"grade": "2a", "text": "Implies X"},  # missing cite
            }],
            "personal": [], "familyoffice": [], "dev": [],
        },
    }
    ok_bc2, e_bc2 = bs.validate_cache(bad_cite2a)
    check("validate_cache: grade '2a' missing cite -> fail",  ok_bc2 is False and any("cite" in e for e in e_bc2))

    # Grade "2b" without cite is OK (cite optional for 2b)
    ok_2b_cache = {
        "station_counts": {"system": 1, "personal": 0, "familyoffice": 0, "dev": 0},
        "stations": {
            "system":       [{
                "item_id": "X", "title": "T", "domain": "system",
                "claude_voice": {"text": "Do it"},
                "system_voice": {"grade": "2b", "text": "Loosely, by your rule…"},  # no cite
            }],
            "personal": [], "familyoffice": [], "dev": [],
        },
        "act": [],
    }
    ok_2b, e_2b = bs.validate_cache(ok_2b_cache)
    check("validate_cache: grade '2b' without cite is accepted", ok_2b is True)

    # ── 8. runtime guards: bad choice / unknown station are rejected ──────────────────
    print("\n== 8. runtime guards ==")
    guard_path = os.path.join(d, "guard-session.json")
    bs.new_walk(guard_path, "2026-06-22", ORDER, SEED)

    # bad choice enum
    raised = False
    try:
        bs.record_decision(guard_path, "g1", "T", "system", "NOPE", "x", executed=True)
    except ValueError:
        raised = True
    check("record_decision: invalid choice raises ValueError",  raised)

    # unknown station on a decision
    raised2 = False
    try:
        bs.record_decision(guard_path, "g2", "T", "nosuchstation", "system", "x", executed=True)
    except ValueError:
        raised2 = True
    check("record_decision: unknown station raises ValueError", raised2)

    # unknown station on a deferral
    raised3 = False
    try:
        bs.record_deferral(guard_path, "g3", "T", "nosuchstation", "timing", "2026-06-22")
    except ValueError:
        raised3 = True
    check("record_deferral: unknown station raises ValueError", raised3)

    # ledger not corrupted by the rejected calls (still 0 decisions / 0 deferrals)
    gl = bs.load(guard_path)
    check("guards: rejected calls left ledger clean",
          len(gl["decisions"]) == 0 and len(gl["deferrals"]) == 0)

    # a valid call still works after the rejections
    bs.record_decision(guard_path, "g4", "Good one", "system", "claude", "did it", executed=True)
    gl2 = bs.load(guard_path)
    check("guards: valid call after rejections succeeds",       len(gl2["decisions"]) == 1)

    # ── A15: held_summary — review-lane age + batch grouping ─────────────────────────
    print("\n== A15 held_summary ==")
    NOW = 1751760000.0   # fixed epoch (2026-07-06 UTC) — tests must not read the wall clock
    def _iso(epoch):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
    DAY = 86400.0
    qp = os.path.join(d, "queue.json")
    def _item(iid, stage, lane, ck, kb, first_awaiting=None, rec=None, extra=None):
        it = {"id": iid, "stage": stage, "lane": lane, "conflict_key": ck, "kb": kb,
              "history": ([{"ts": _iso(first_awaiting), "stage": "awaiting"}]
                          if first_awaiting else [])}
        if rec: it["recommended"] = rec
        if extra: it.update(extra)
        return it
    json.dump({"queue": [
        _item("old-may", "awaiting", "review", "dev/wiki/sources/old-may.md", "dev",
              first_awaiting=NOW - 63 * DAY, rec="approve"),
        _item("fresh", "awaiting", "review", "dev/wiki/sources/fresh.md", "dev",
              first_awaiting=NOW - 2 * DAY, rec="approve"),
        _item("confirm-1", "awaiting", "confirm", "personal/wiki/journal/2026-07-01.md",
              "personal", first_awaiting=NOW - 1 * DAY),
        # re-awaited item: FIRST awaiting counts, not the latest flip
        _item("re-awaited", "awaiting", "review", "dev/wiki/journal/re.md", "dev",
              rec="hold",
              extra={"history": [{"ts": _iso(NOW - 30 * DAY), "stage": "awaiting"},
                                 {"ts": _iso(NOW - 5 * DAY), "stage": "sorted"},
                                 {"ts": _iso(NOW - 1 * DAY), "stage": "awaiting"}]}),
        # ts-less history -> falls back to first_drafted_utc
        _item("fallback-ts", "awaiting", "review", "dev/wiki/sources/fb.md", "dev",
              extra={"first_drafted_utc": _iso(NOW - 4 * DAY)}),
        # NOT held: auto-ship lane, other stages
        _item("auto", "awaiting", "auto-ship", "dev/wiki/sources/auto.md", "dev"),
        _item("done", "shipped", "review", "dev/wiki/sources/done.md", "dev"),
        _item("sorted", "sorted", "review", "dev/wiki/sources/sorted.md", "dev"),
    ]}, open(qp, "w", encoding="utf-8"))

    hs = bs.held_summary(qp, now_epoch=NOW)
    check("held: counts only awaiting review/confirm", hs["count"] == 5)
    check("held: oldest is the May item at 63d",
          hs["oldest_id"] == "old-may" and 62.5 < hs["oldest_days"] < 63.5)
    check("held: nag fires past the default 7d", hs["nag"] is True)
    check("held: age_line carries count, days, and the nag",
          "5 held" in hs["age_line"] and "63d" in hs["age_line"] and "aging past" in hs["age_line"])
    reaw = [g for g in hs["groups"] if "re-awaited" in g["ids"]]
    check("held: re-awaited item present and grouped", len(reaw) == 1)
    check("held: not grouped below the threshold", hs["grouped"] is False)
    gkeys = {(g["kb"], g["folder"], g["recommended"]) for g in hs["groups"]}
    check("held: groups keyed by kb+folder+ballot",
          ("dev", "wiki/sources", "approve") in gkeys and ("personal", "wiki/journal", "-") in gkeys)

    hs2 = bs.held_summary(qp, now_epoch=NOW, group_threshold=4)
    check("held: grouped flips above the threshold", hs2["grouped"] is True)
    biggest = hs2["groups"][0]
    check("held: groups sorted by size, ids + sample slugs present",
          biggest["count"] >= 2 and len(biggest["sample_slugs"]) >= 2)

    json.dump({"queue": []}, open(qp, "w", encoding="utf-8"))
    hs3 = bs.held_summary(qp, now_epoch=NOW)
    check("held: zero held renders the clear line, no nag",
          hs3["count"] == 0 and hs3["age_line"] == "Review lane: clear ✓" and hs3["nag"] is False)

    open(qp, "w", encoding="utf-8").write("{not json")
    hs4 = bs.held_summary(qp, now_epoch=NOW)
    check("held: unreadable queue fails loud, never a silent zero", "error" in hs4)

    # ── Summary ───────────────────────────────────────────────────────────────────────
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:", FAIL)
    # Only exit here when run as a standalone script (suite_test.py subprocess mode).
    # Guarded so pytest can import this module directly to collect the pytest-style
    # test(s) below without the legacy script's sys.exit aborting collection.
    if __name__ == "__main__":
        sys.exit(1 if FAIL else 0)

finally:
    shutil.rmtree(d, ignore_errors=True)
# end of brief_session tests (legacy check()-style suite, runs at import time)


# ── pytest-style additions (new tests go here, not into the legacy check() block) ──────

def test_record_decision_stores_notion_write_intent(tmp_path):
    sp = str(tmp_path / "walk.json")
    bs.new_walk(sp, "w1", ["settle", "system"], {"settle": 1, "system": 0})
    bs.record_decision(sp, "OI-901", "Pay tax", "settle", "system", "flip Status=Done",
                       executed=True, notion_write={"page_id": "p1", "field": "Status", "to": "Done"})
    led = bs.load(sp)
    d = led["decisions"][0]
    assert d["station"] == "settle"
    assert d["notion_write"] == {"page_id": "p1", "field": "Status", "to": "Done"}


# ── validate_cache: optional settle block (C2) ──────────────────────────────────────

_MIN = {  # a minimal valid cache: adjust keys to match existing valid fixtures in this file
    "station_counts": {"system": 0, "personal": 0, "familyoffice": 0, "dev": 0},
    "stations": {"system": [], "personal": [], "familyoffice": [], "dev": []},
    "act": [],   # REQUIRED since the act-conservation fix — a real gather always writes the key
}

def test_settle_absent_is_valid():
    ok, errs = bs.validate_cache(dict(_MIN))
    assert ok, errs

def test_settle_valid_block_ok():
    c = dict(_MIN)
    c["settle"] = {"auto_healed": [], "candidates": [
        {"task_id": "OI-909", "title": "Ship page", "proposed_transition": "in_progress",
         "evidence": [{"source": "git", "ref": "abc123", "quote": "..."}], "confidence": "high", "domain": "dev"}]}
    ok, errs = bs.validate_cache(c)
    assert ok, errs

def test_settle_bad_transition_rejected():
    c = dict(_MIN)
    c["settle"] = {"candidates": [{"task_id": "X", "title": "Y", "proposed_transition": "cancelled"}]}
    ok, errs = bs.validate_cache(c)
    assert not ok
    assert any("proposed_transition" in e for e in errs)

def test_settle_candidate_missing_field_rejected():
    c = dict(_MIN)
    c["settle"] = {"candidates": [{"title": "no id", "proposed_transition": "done"}]}
    ok, errs = bs.validate_cache(c)
    assert not ok
    assert any("task_id" in e for e in errs)

def test_settle_candidates_not_a_list_rejected():
    c = dict(_MIN)
    c["settle"] = {"candidates": 5}
    ok, errs = bs.validate_cache(c)
    assert not ok
    assert any("candidates must be a list" in e for e in errs)


# ── validate_cache: act-list coverage (A88) ─────────────────────────────────────────
# The Act list (`cache["act"]`, renamed from `needs_you` in Task 5) is the FIRST thing the
# brief shows, and was entirely unasserted by validate_cache. These lock the same per-item
# rules (title/domain/claude_voice.text/system_voice) applied to a station item.

def test_validate_cache_rejects_an_act_item_missing_voice():
    cache = {"stations": {"system": [], "personal": [], "familyoffice": [], "gm": []},
             "station_counts": {"system": 0, "personal": 0, "familyoffice": 0, "gm": 0},
             "act": [{"title": "T", "domain": "gm"}]}          # no claude_voice
    ok, errs = bs.validate_cache(cache, required_domains=["system", "personal", "familyoffice", "gm"])
    assert not ok and any("act" in e and "claude_voice" in e for e in errs)


def test_validate_cache_accepts_a_well_formed_act():
    cache = {"stations": {"system": [], "personal": [], "familyoffice": [], "gm": []},
             "station_counts": {"system": 0, "personal": 0, "familyoffice": 0, "gm": 0},
             "act": [{"title": "T", "domain": "gm", "claude_voice": {"text": "c"},
                      "system_voice": None}]}
    ok, errs = bs.validate_cache(cache, required_domains=["system", "personal", "familyoffice", "gm"])
    assert ok, errs


def test_validate_cache_rejects_act_not_a_list():
    c = dict(_MIN)
    c["act"] = {"not": "a list"}
    ok, errs = bs.validate_cache(c)
    assert not ok
    assert any(e.startswith("act:") and "list" in e for e in errs)


def test_validate_cache_act_item_missing_title_and_domain():
    c = dict(_MIN)
    c["act"] = [{"claude_voice": {"text": "c"}, "system_voice": None}]
    ok, errs = bs.validate_cache(c)
    assert not ok
    assert any("act[0]" in e and "title" in e for e in errs)
    assert any("act[0]" in e and "domain" in e for e in errs)


def test_validate_cache_act_item_bad_grade_reuses_station_rule():
    # Same grade/cite rules as a station item, proving the act path shares the helper rather
    # than a re-implemented copy.
    c = dict(_MIN)
    c["act"] = [{"title": "T", "domain": "gm", "claude_voice": {"text": "c"},
                 "system_voice": {"grade": "1", "text": "x"}}]   # grade 1 requires cite
    ok, errs = bs.validate_cache(c)
    assert not ok
    assert any("act[0]" in e and "cite" in e for e in errs)


# ── validate_cache: `act` is REQUIRED, not optional ────────────────────────────────
# Task 5 renamed cache.needs_you -> act with NO fallback (correct: a fallback would hide a
# stale writer). Task 6 then added act validation but made it OPTIONAL. Composed against the
# cache actually on disk (pre-rename, still `needs_you`), the whole chain reported healthy
# while the Act list rendered EMPTY: cache-status fresh -> validate_cache OK -> overview ''.
# Seven real items vanished and every gate said OK. Spec §1's headline complaint — "a gather
# emitting needs_you: [] passes with OK" — was renamed, not closed.
#
# The rule: ABSENCE is a contract break; EMPTINESS is a legitimate state. A gather always
# writes the key, so an absent `act` can only mean a stale/pre-rename writer. An `act: []` is
# a real quiet day and MUST still render — banning it would brick the ENTIRE brief
# (Invariant 4: INVALID -> don't render) on a good day. The spec's `needs_you: []` failure is
# caught by the headline-chip conservation below, not by outlawing emptiness.

def test_validate_cache_act_absent_is_an_error():
    c = dict(_MIN); c.pop("act", None)
    ok, errs = bs.validate_cache(c)
    assert not ok
    assert any("act" in e and "missing" in e for e in errs), errs


def test_validate_cache_catches_the_stale_pre_rename_writer():
    """The exact cache on disk: a writer still emitting `needs_you` must NOT validate OK."""
    c = dict(_MIN); c.pop("act", None)
    c["needs_you"] = [{"title": "T", "domain": "gm", "claude_voice": {"text": "c"},
                       "system_voice": None}]
    ok, errs = bs.validate_cache(c)
    assert not ok, "a pre-rename cache must fail loud, not render an empty Act list"


def test_validate_cache_empty_act_is_valid_when_the_chip_agrees():
    """DELIBERATE: a legitimately quiet day (nothing needs the owner) renders — not an error."""
    c = dict(_MIN, act=[], headline_bubbles=["0 need you", "0 to review"])
    ok, errs = bs.validate_cache(c)
    assert ok, errs


# ── validate_cache: headline chips cannot disagree with the list they count ─────────
# The 5/7/21 regression: the masthead said "5 need you" (model-authored prose) while
# cache["act"] held 7 items and standup.totals.needs_you said 21 — three numbers on one
# screen and nothing compared them. compute_headline_bubbles() derives the chips, but a model
# writing the cache follows the cache-contract prose, so the assertion is the load-bearing
# half: it closes the hole whichever prose the model follows.
#
# The chips are DERIVED data, so ABSENT is recoverable (the render just computes them) —
# unlike `act`/`delta`, which are authored and cannot be reconstructed. Present -> must agree.

def test_validate_cache_fails_when_headline_bubbles_disagree_with_act():
    item = {"title": "T", "domain": "gm", "claude_voice": {"text": "c"}, "system_voice": None}
    c = dict(_MIN, act=[dict(item) for _ in range(7)], headline_bubbles=["5 need you"])
    ok, errs = bs.validate_cache(c)
    assert not ok
    assert any("headline_bubbles" in e for e in errs), errs


def test_validate_cache_catches_the_spec_empty_act_under_a_nonzero_chip():
    """Spec §1's literal failure: an EMPTY Act list shipped under a hand-typed "5 need you"."""
    c = dict(_MIN, act=[], headline_bubbles=["5 need you"])
    ok, errs = bs.validate_cache(c)
    assert not ok
    assert any("headline_bubbles" in e for e in errs), errs


def test_validate_cache_passes_when_headline_bubbles_agree_with_act():
    item = {"title": "T", "domain": "gm", "claude_voice": {"text": "c"}, "system_voice": None}
    c = dict(_MIN, act=[item], headline_bubbles=["1 need you", "3 to review"])
    ok, errs = bs.validate_cache(c)
    assert ok, errs


def test_validate_cache_headline_bubbles_absent_is_valid():
    """Derived data: absent chips are computed at render, so there is nothing to contradict."""
    ok, errs = bs.validate_cache(dict(_MIN, act=[]))
    assert ok, errs


def test_validate_cache_headline_chip_matches_the_renderer_format_exactly():
    """The assertion is worthless if it checks a format the renderer never emits — pin them
    together so a format change in one cannot silently pass the other."""
    sys.path.insert(0, TOOLS)
    import brief_render as R
    item = {"title": "T", "domain": "gm", "claude_voice": {"text": "c"}, "system_voice": None}
    cache = dict(_MIN, act=[dict(item) for _ in range(3)])
    computed = R.compute_headline_bubbles(cache)
    ok, errs = bs.validate_cache(dict(cache, headline_bubbles=computed))
    assert ok, ("validate_cache must accept what compute_headline_bubbles emits", errs)


# ── validate_cache: standup delta -> stations.gm conservation (A88 / Task 7) ────────
# The brief printed "21 need you" from standup.json's delta while the walk rendered four
# unrelated cards from the cache, and nothing compared them. A delta item with no card in
# stations.gm is a silent drop — assert it.

def test_validate_cache_fails_when_a_delta_item_has_no_card():
    standup = {"delta": [{"repo": "Claude", "id": "H54", "title": "Retirement sweep"},
                         {"repo": "aios", "id": "A69", "title": "Reflect lessons"}]}
    cache = {"stations": {"system": [], "personal": [], "familyoffice": [],
                          "gm": [{"item_id": "H54", "title": "Retirement sweep", "domain": "gm",
                                  "claude_voice": {"text": "c"}, "system_voice": None}]},
             "station_counts": {"system": 0, "personal": 0, "familyoffice": 0, "gm": 1},
             "act": []}
    ok, errs = bs.validate_cache(
        cache, required_domains=["system", "personal", "familyoffice", "gm"], standup=standup)
    assert not ok
    joined = " ".join(errs)
    assert "unaccounted" in joined and "A69" in joined, joined


def test_validate_cache_passes_when_every_delta_item_has_a_card():
    standup = {"delta": [{"repo": "Claude", "id": "H54", "title": "Retirement sweep"}]}
    cache = {"stations": {"system": [], "personal": [], "familyoffice": [],
                          "gm": [{"item_id": "H54", "title": "Retirement sweep", "domain": "gm",
                                  "claude_voice": {"text": "c"}, "system_voice": None}]},
             "station_counts": {"system": 0, "personal": 0, "familyoffice": 0, "gm": 1},
             "act": []}
    ok, errs = bs.validate_cache(
        cache, required_domains=["system", "personal", "familyoffice", "gm"], standup=standup)
    assert ok, errs


# ── validate_cache: id-less delta items are exempt from the card assertion (Task 7) ─
# The standup collector deliberately emits id-less "◷" backlog seeds (id: "") and routes
# them around its dedupe sidecar, so they are ALWAYS in delta and ALWAYS reported via the
# collector's own errors[]. An id-less item can never be carded BY ID — asserting one is
# structurally impossible to satisfy, and previously bricked the ENTIRE brief (Invariant 4:
# INVALID -> don't render) over a single seed that was never meant to have a card.

def test_validate_cache_passes_when_the_only_unaccounted_delta_item_has_no_id():
    standup = {"delta": [{"repo": "aios", "id": "", "title": "Capture worthiness floor…"}]}
    cache = {"stations": {"system": [], "personal": [], "familyoffice": [], "gm": []},
             "station_counts": {"system": 0, "personal": 0, "familyoffice": 0, "gm": 0},
             "act": []}
    ok, errs = bs.validate_cache(
        cache, required_domains=["system", "personal", "familyoffice", "gm"], standup=standup)
    assert ok, errs


def test_validate_cache_id_less_item_does_not_mask_a_real_unaccounted_item():
    standup = {"delta": [{"repo": "aios", "id": "", "title": "Capture worthiness floor…"},
                         {"repo": "Claude", "id": "H54", "title": "Retirement sweep"}]}
    cache = {"stations": {"system": [], "personal": [], "familyoffice": [], "gm": []},
             "station_counts": {"system": 0, "personal": 0, "familyoffice": 0, "gm": 0},
             "act": []}
    ok, errs = bs.validate_cache(
        cache, required_domains=["system", "personal", "familyoffice", "gm"], standup=standup)
    assert not ok
    joined = " ".join(errs)
    assert "H54" in joined, joined
    assert "Capture worthiness floor" not in joined, joined


# ── the standup.json cross-repo contract (Scripts/factory-gate -> aios) ─────────────
# standup.json is written in the ENV repo and read here; nothing pinned its shape across the
# boundary. Every test above hand-builds {"delta": [...]}, so a hand-built fixture could not
# catch env-side drift. Simulated drift (delta[] -> changes[]) returned ok=True, errs=[]: the
# conservation assertion ran over an empty list and passed VACUOUSLY, because
# `standup.get("delta") or []` makes a CONTRACT BREAK indistinguishable from a quiet day —
# "absence looks like OK" at exactly the repo boundary where drift is likeliest.
#
# Same rule as `act`: ABSENCE is a contract break, EMPTINESS is legitimate. A caller passing
# standup= is asserting "there is a standup to conserve against"; if it carries no delta[],
# the file is not a standup and the check cannot run — say so rather than report OK.
# The paired producer-side test lives in the env repo:
#   Scripts/factory-gate/tests/test_factory_standup.py::test_collect_emits_the_cross_repo_standup_contract

_FIX_STANDUP = os.path.join(HERE, "fixtures", "standup.sample.json")


def test_validate_cache_rejects_a_standup_missing_delta():
    """Env-side drift must fail LOUD, not degrade into a vacuous pass."""
    standup = {"changes": [{"repo": "Claude", "id": "H54", "title": "Retirement sweep"}],
               "unchanged": [], "totals": {"delta": 1}}          # `delta` renamed -> drift
    ok, errs = bs.validate_cache(dict(_MIN), standup=standup)
    assert not ok, "a standup with no delta[] must not pass vacuously"
    assert any("delta" in e for e in errs), errs


def test_validate_cache_rejects_a_standup_whose_delta_is_not_a_list():
    ok, errs = bs.validate_cache(dict(_MIN), standup={"delta": {"H54": "x"}})
    assert not ok
    assert any("delta" in e for e in errs), errs


def test_validate_cache_rejects_a_delta_of_non_dict_items():
    """A gate must RETURN (ok, errors), never raise: a delta of bare ids used to crash
    validate_cache with AttributeError, which bricks the brief harder than a vacuous pass
    (Invariant 4 never even gets to evaluate)."""
    ok, errs = bs.validate_cache(dict(_MIN), standup={"delta": ["H54", "A69"]})
    assert not ok
    assert any("delta" in e for e in errs), errs


def test_validate_cache_standup_with_empty_delta_is_valid():
    """A real quiet day in the decision queue: nothing moved. Legitimate — must render."""
    ok, errs = bs.validate_cache(dict(_MIN), standup={"delta": [], "unchanged": [{"id": "H1"}]})
    assert ok, errs


def test_validate_cache_against_a_real_shaped_standup_fixture():
    """The fixture is GENERATED BY the env collector (factory_standup.collect), not hand-typed,
    so it carries the real key set. Its delta ids are carded -> conservation holds."""
    with open(_FIX_STANDUP, encoding="utf-8") as f:
        standup = json.load(f)
    ids = [i["id"] for i in standup["delta"]]
    assert ids, "fixture must carry delta items or it asserts nothing"
    cards = [{"item_id": i, "title": "T", "domain": "gm",
              "claude_voice": {"text": "c"}, "system_voice": None} for i in ids]
    cache = {"stations": {"system": [], "personal": [], "familyoffice": [], "gm": cards},
             "station_counts": {"system": 0, "personal": 0, "familyoffice": 0, "gm": len(cards)},
             "act": []}
    ok, errs = bs.validate_cache(
        cache, required_domains=["system", "personal", "familyoffice", "gm"], standup=standup)
    assert ok, errs


def test_real_shaped_fixture_drops_a_card_and_is_caught():
    """Same real fixture, one card missing — the conservation check must catch the drop."""
    with open(_FIX_STANDUP, encoding="utf-8") as f:
        standup = json.load(f)
    ids = [i["id"] for i in standup["delta"]]
    cards = [{"item_id": i, "title": "T", "domain": "gm",
              "claude_voice": {"text": "c"}, "system_voice": None} for i in ids[:-1]]
    cache = {"stations": {"system": [], "personal": [], "familyoffice": [], "gm": cards},
             "station_counts": {"system": 0, "personal": 0, "familyoffice": 0, "gm": len(cards)},
             "act": []}
    ok, errs = bs.validate_cache(
        cache, required_domains=["system", "personal", "familyoffice", "gm"], standup=standup)
    assert not ok
    assert ids[-1] in " ".join(errs), errs


def test_real_shaped_fixture_carries_the_documented_delta_item_keys():
    """Pins the field set the aios side consumes. If the env collector stops emitting one of
    these, regenerating this fixture fails here rather than silently rendering a blank card."""
    with open(_FIX_STANDUP, encoding="utf-8") as f:
        standup = json.load(f)
    for key in ("delta", "unchanged", "totals", "groups"):
        assert key in standup, f"real standup must carry {key!r}"
    for key in ("delta", "unchanged"):
        assert key in standup["totals"], f"totals must carry {key!r}"
    for item in standup["delta"]:
        for key in ("repo", "id", "title", "reason", "group"):
            assert key in item, f"delta item must carry {key!r}: {item}"


# ── FIX 1: the headline chip asserts the RENDERED Act count, not len(act) ────────────
# The 'corrected' 5/7/21: 7 raw act items, 2 court-filtered to ⏳In-motion -> 5 render as Act.
# The honest "5 need you" chip (matching the rendered rows) must PASS; the len(act) "7 need you"
# chip must FAIL. Before the fix the assertion demanded len(act)=7 and REJECTED the honest chip.

def _act_5_of_7():
    act = [{"title": "you%d" % i, "domain": "gm", "claude_voice": {"text": "c"},
            "system_voice": None} for i in range(5)]
    act.append({"title": "w1", "domain": "gm", "claude_voice": {"text": "c"},
                "system_voice": None, "in_motion": {"court": "others"}})
    act.append({"title": "w2", "domain": "gm", "claude_voice": {"text": "c"},
                "system_voice": None, "in_motion": {"court": "done"}})
    return act


def _base_cache_with_act(act):
    return {"stations": {"system": [], "personal": [], "familyoffice": [], "gm": []},
            "station_counts": {"system": 0, "personal": 0, "familyoffice": 0, "gm": 0},
            "act": act}


_REQ_GM = ["system", "personal", "familyoffice", "gm"]


def test_validate_cache_accepts_the_honest_rendered_count_chip():
    cache = _base_cache_with_act(_act_5_of_7())
    cache["headline_bubbles"] = ["5 need you"]     # what render_overview actually emits
    ok, errs = bs.validate_cache(cache, required_domains=_REQ_GM)
    assert ok, errs


def test_validate_cache_rejects_the_len_act_chip_over_a_filtered_list():
    cache = _base_cache_with_act(_act_5_of_7())
    cache["headline_bubbles"] = ["7 need you"]      # the misleading len(act) count
    ok, errs = bs.validate_cache(cache, required_domains=_REQ_GM)
    assert not ok
    assert any("5 need you" in e or "RENDERED" in e for e in errs), errs


# ── FIX 2 (A7): the delta-card station key is NOT hardcoded "gm" ─────────────────────
# The engine's own shipped fixture keys the Dev station "dev". A dev-keyed cache with the delta
# id carded under stations["dev"] must be accounted; before the fix `carded` read stations["gm"]
# (absent -> empty), every real-id delta was "unaccounted", and Invariant 4 bricked the brief.

def test_validate_cache_delta_card_in_dev_keyed_station_is_accounted():
    standup = {"delta": [{"repo": "Claude", "id": "H54", "title": "Retirement sweep"}]}
    cache = {"stations": {"system": [], "personal": [], "familyoffice": [],
                          "dev": [{"item_id": "H54", "title": "Retirement sweep", "domain": "dev",
                                   "claude_voice": {"text": "c"}, "system_voice": None}]},
             "station_counts": {"system": 0, "personal": 0, "familyoffice": 0, "dev": 1},
             "act": []}
    ok, errs = bs.validate_cache(
        cache, required_domains=["system", "personal", "familyoffice", "dev"], standup=standup)
    assert ok, errs


def test_validate_cache_uncarded_delta_still_fails_under_any_station_key():
    # The real protection must survive the de-hardcode: a delta id carded in NO station fails.
    standup = {"delta": [{"repo": "Claude", "id": "H54", "title": "Retirement sweep"},
                         {"repo": "aios", "id": "A69", "title": "Reflect lessons"}]}
    cache = {"stations": {"system": [], "personal": [], "familyoffice": [],
                          "dev": [{"item_id": "H54", "title": "Retirement sweep", "domain": "dev",
                                   "claude_voice": {"text": "c"}, "system_voice": None}]},
             "station_counts": {"system": 0, "personal": 0, "familyoffice": 0, "dev": 1},
             "act": []}
    ok, errs = bs.validate_cache(
        cache, required_domains=["system", "personal", "familyoffice", "dev"], standup=standup)
    assert not ok
    assert "A69" in " ".join(errs) and "unaccounted" in " ".join(errs), errs


# ── FIX 4: --standup with a missing/torn file fails CLEAN, no raw traceback ──────────
# Carried minor #8: the --standup read had no error handling; a missing file threw a raw
# FileNotFoundError instead of the "FAIL: could not parse …" the cache path already gets. The
# gate's own rule (asserted in this branch) is "a gate RETURNS (ok, errors), never raises".

def test_cli_validate_cache_standup_missing_file_fails_clean(tmp_path):
    import subprocess
    tool = os.path.join(TOOLS, "brief_session.py")
    cache_path = str(tmp_path / "cache_ok.json")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"station_counts": {"gm": 0}, "stations": {"gm": []}, "act": []}, f)
    proc = subprocess.run(
        [sys.executable, tool, "validate_cache", cache_path, "--domains", "gm",
         "--standup", str(tmp_path / "does_not_exist.json")],
        capture_output=True, encoding="utf-8", errors="replace")
    assert proc.returncode != 0
    assert "Traceback" not in (proc.stderr or ""), proc.stderr
    assert "could not parse standup" in (proc.stdout or ""), (proc.stdout, proc.stderr)


# ─── A93 §2a — carryover-deferral revalidation ───────────────────────────────

def test_revalidate_carryover_drops_completed_and_keeps_open():
    carry = [{"item_id": "OI-1", "title": "Still open"},
             {"item_id": "OI-2", "title": "Done since deferral"},
             {"item_id": "", "title": "no id — cannot resurface"}]
    kept, cleared = bs.revalidate_carryover(carry, live_ids={"OI-1"})
    assert [d["item_id"] for d in kept] == ["OI-1"]
    assert {d["item_id"] for d in cleared} == {"OI-2", ""}


def test_revalidate_carryover_none_live_keeps_everything():
    carry = [{"item_id": "OI-1", "title": "x"}, {"item_id": "OI-2", "title": "y"}]
    kept, cleared = bs.revalidate_carryover(carry, live_ids=None)
    assert kept == carry and cleared == []


def test_resume_or_new_auto_clears_completed_carryover(tmp_path):
    state = str(tmp_path / "brief-session.json")
    # a completed prior walk with two open deferrals
    bs.new_walk(state, "2026-07-16", ["kb", "dev"], {"dev": 1})
    bs.record_deferral(state, "OI-1", "Kept task", "dev", "timing", "2026-07-16")
    bs.record_deferral(state, "OI-9", "Completed task", "dev", "blocked", "2026-07-16")
    led = bs.load(state)
    led["status"] = "complete"
    bs._atomic_write(state, led)
    # fresh gather: only OI-1 is still live
    mode, ledger = bs.resume_or_new(state, "2026-07-17", ["kb", "dev"], {"dev": 1},
                                    live_ids={"OI-1"})
    assert mode == "new"
    assert [d["item_id"] for d in ledger["deferrals"]] == ["OI-1"]
    assert [d["item_id"] for d in ledger["auto_cleared_deferrals"]] == ["OI-9"]


# ─── A93 §2b — live held-panel enforcement in validate_cache ─────────────────

def _min_cache(held_n):
    return {"station_counts": {"dev": 0}, "stations": {"dev": []}, "act": [],
            "held": [{"id": "h%d" % i} for i in range(held_n)]}


def test_validate_cache_flags_held_panel_drift():
    cache = _min_cache(3)
    ok, errs = bs.validate_cache(cache, required_domains=["dev"], live_held_count=5)
    assert not ok
    assert any("held-panel drift" in e for e in errs), errs


def test_validate_cache_passes_when_held_matches_live():
    cache = _min_cache(4)
    ok, errs = bs.validate_cache(cache, required_domains=["dev"], live_held_count=4)
    assert ok, errs


def test_validate_cache_held_check_skipped_when_no_live_count():
    cache = _min_cache(3)
    ok, errs = bs.validate_cache(cache, required_domains=["dev"])  # no live count -> not checked
    assert ok, errs


def test_cli_validate_cache_bad_live_held_count_fails_clean(tmp_path):
    import subprocess
    tool = os.path.join(TOOLS, "brief_session.py")
    cache_path = str(tmp_path / "cache_ok.json")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"station_counts": {"gm": 0}, "stations": {"gm": []}, "act": [], "held": []}, f)
    proc = subprocess.run(
        [sys.executable, tool, "validate_cache", cache_path, "--domains", "gm",
         "--live-held-count", "not-an-int"],
        capture_output=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 2
    assert "Traceback" not in (proc.stderr or ""), proc.stderr
    assert "must be an integer" in (proc.stderr or ""), (proc.stdout, proc.stderr)
