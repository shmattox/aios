#!/usr/bin/env python3
"""Hermetic tests for the session-capture stage (G16b helper + G16c garden sweep).

Covers the MECHANICAL contract the agent-driven synthesis relies on:
  - session_synth.scan  : returns ended + unsynthesized sessions, excludes live ones, rescues
                          crashed (never-closed-but-stale) ones, parses intents.
  - session_synth.mark  : flips evidence synthesized:false->true atomically + idempotently; a day's
                          shared activity log flips ONLY once every session for that day is done.
  - garden_sweep (G16c) : sweeps synthesized + old evidence, KEEPS unsynthesized (live work) and
                          recent evidence; dormant when no evidence_dir is given (pre-cutover boundary).

Hermetic: every file lives under a tempfile.mkdtemp() tree; the live install is never touched.
Run: python tools/tests/test_session_capture.py
"""
import os, sys, json, time, tempfile, shutil
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)
import session_synth as ss
import garden_sweep

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok   {name}")
    else:
        FAIL += 1; print(f"  FAIL {name}")


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def write_sess(ev, sid, project="general", cwd="C:/x", started=None, ended=None,
               files=("a.md",), tool_counts=None, tools_failed=0, synthesized=False, mtime=None):
    started = started or iso(datetime.now(timezone.utc))
    # None -> default; {} stays empty (else an "empty" fixture silently gets default tools)
    tc = json.dumps({"Edit": 2, "Read": 5} if tool_counts is None else tool_counts)
    flist = ", ".join(f'"{f}"' for f in files)
    body = (
        "---\n"
        "type: session\n"
        f"id: {sid}\n"
        f"project: {project}\n"
        f"started_at: {started}\n"
        f"ended_at: {ended or ''}\n"
        f"cwd: {cwd}\n"
        f"files: [{flist}]\n"
        f"tool_counts: {tc}\n"
        f"tools_failed: {tools_failed}\n"
        "transcript_path: C:/t.jsonl\n"
        "ephemeral: true\n"
        f"synthesized: {'true' if synthesized else 'false'}\n"
        "tags: [session, auto-capture, evidence]\n"
        "---\n"
        f"# Session {sid[:8]}\n"
    )
    p = os.path.join(ev, f"sess-{sid}.md")
    open(p, "w", encoding="utf-8", newline="\n").write(body)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def write_intents(ev, sid, prompts, synthesized=False, mtime=None):
    lines = "".join(f"- 10:0{i} | {pr}\n" for i, pr in enumerate(prompts))
    body = (
        "---\n"
        "type: intents\n"
        f"session: {sid}\n"
        f"captured_utc: {iso(datetime.now(timezone.utc))}\n"
        "ephemeral: true\n"
        f"synthesized: {'true' if synthesized else 'false'}\n"
        "tags: [intents, auto-capture, evidence]\n"
        "---\n\n"
        f"{lines}"
    )
    p = os.path.join(ev, f"intents-{sid}.md")
    open(p, "w", encoding="utf-8", newline="\n").write(body)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def write_activity(ev, date, synthesized=False, mtime=None):
    body = (
        "---\n"
        "type: activity\n"
        f"date: {date}\n"
        f"captured_utc: {iso(datetime.now(timezone.utc))}\n"
        "ephemeral: true\n"
        f"synthesized: {'true' if synthesized else 'false'}\n"
        "---\n\n"
        "- 10:00 | dev | Edit | a.md\n"
    )
    p = os.path.join(ev, f"activity-{date}.md")
    open(p, "w", encoding="utf-8", newline="\n").write(body)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def synthesized_flag(p):
    return ss._get(ss._frontmatter(ss._read(p)), "synthesized")


def main():
    root = tempfile.mkdtemp(prefix="aios-sesscap-")
    try:
        ev = os.path.join(root, "evidence"); os.makedirs(ev)
        now = datetime.now(timezone.utc)

        # ---- scan -------------------------------------------------------------
        write_sess(ev, "AAA", project="aios", started=iso(now - timedelta(hours=3)),
                   ended=iso(now - timedelta(hours=2)))
        write_intents(ev, "AAA", ["build G16", "now wire the garden sweep"])
        write_sess(ev, "RUN", started=iso(now - timedelta(minutes=20)), ended="")        # live -> skip
        write_sess(ev, "STALE", started=iso(now - timedelta(hours=40)), ended="")        # crashed -> rescue
        write_sess(ev, "DONE", started=iso(now - timedelta(hours=5)),
                   ended=iso(now - timedelta(hours=4)), synthesized=True)                 # already done -> skip

        bundles = ss.scan(ev)
        ids = {b["id"] for b in bundles}
        check("scan: ended+unsynthesized session returned", "AAA" in ids)
        check("scan: live (running, fresh) session excluded", "RUN" not in ids)
        check("scan: crashed (never-closed but stale) session rescued", "STALE" in ids)
        check("scan: already-synthesized session excluded", "DONE" not in ids)
        a = next(b for b in bundles if b["id"] == "AAA")
        check("scan: intents parsed (the 'why')", a["intents"] == ["build G16", "now wire the garden sweep"])
        check("scan: project carried through (domain map is the SKILL's job)", a["project"] == "aios")
        check("scan: tool_counts parsed", a["tool_counts"].get("Read") == 5)
        check("scan: STALE flagged running_stale", next(b for b in bundles if b["id"] == "STALE")["running_stale"])

        # ---- mark: single session releases its own evidence + its solo-day activity ----
        write_activity(ev, "2026-06-15")
        # give AAA the 06-15 date by rewriting started_at date via a fresh file
        write_sess(ev, "AAA", project="aios", started="2026-06-15T10:00:00.000Z",
                   ended="2026-06-15T12:00:00.000Z")
        write_intents(ev, "AAA", ["build G16"])
        res = ss.mark(ev, ["AAA"])
        check("mark: session evidence flipped synthesized", synthesized_flag(os.path.join(ev, "sess-AAA.md")) == "true")
        check("mark: intents evidence flipped synthesized", synthesized_flag(os.path.join(ev, "intents-AAA.md")) == "true")
        check("mark: solo-day activity log released", synthesized_flag(os.path.join(ev, "activity-2026-06-15.md")) == "true")
        check("mark: report lists the session", res["sessions"] == ["AAA"])

        # ---- mark: shared-day activity log waits for ALL that day's sessions ----
        write_sess(ev, "P1", started="2026-06-18T09:00:00.000Z", ended="2026-06-18T10:00:00.000Z")
        write_sess(ev, "P2", started="2026-06-18T11:00:00.000Z", ended="2026-06-18T12:00:00.000Z")
        write_activity(ev, "2026-06-18")
        ss.mark(ev, ["P1"])
        check("mark: shared-day activity NOT released while a sibling session is unsynthesized",
              synthesized_flag(os.path.join(ev, "activity-2026-06-18.md")) == "false")
        ss.mark(ev, ["P2"])
        check("mark: shared-day activity released once every session is done",
              synthesized_flag(os.path.join(ev, "activity-2026-06-18.md")) == "true")

        # ---- mark: idempotent ----
        again = ss.mark(ev, ["AAA"])
        check("mark: idempotent (already-synthesized counts, no crash)",
              synthesized_flag(os.path.join(ev, "sess-AAA.md")) == "true")

        # ---- records: the capture `sessions` adapter discovery (record vs evidence) ----
        recdir = os.path.join(root, "raw_sessions"); os.makedirs(recdir)
        rec = (
            "---\n"
            "type: session-record\n"
            "source: claude-code\n"
            "id: REC1\n"
            "domain: familyoffice\n"
            "project: family-office\n"
            "started_utc: 2026-06-21T14:00:00.000Z\n"
            "conflict_key: familyoffice/wiki/journal/2026-06-21.md\n"
            "tags: [session-record, auto-capture]\n"
            "---\n"
            "# Bayview bridge hedge\n**Focus:** x\n**Outcome:** y\n**Why:** z\n"
        )
        open(os.path.join(recdir, "claude-code-2026-06-21-REC1.md"), "w", encoding="utf-8", newline="\n").write(rec)
        # decoy EVIDENCE files in the same dir — must NOT be enqueued
        write_sess(recdir, "EV1", synthesized=False)
        write_intents(recdir, "EV1", ["hi"])
        write_activity(recdir, "2026-06-21")
        # malformed record (no conflict_key, no domain -> kb would be '') must be SKIPPED, not emitted
        malformed = "---\ntype: session-record\nsource: cowork\nid: BAD1\n---\n# x\n**Focus:** a\n"
        open(os.path.join(recdir, "cowork-2026-06-21-BAD1.md"), "w", encoding="utf-8", newline="\n").write(malformed)
        recs = ss.records(recdir)
        check("records: returns exactly the well-formed session-record (evidence + malformed excluded)", len(recs) == 1)
        check("records: malformed record (no kb) skipped, not emitted", all(r["id"] != "BAD1" for r in recs))
        check("records: id parsed", recs[0]["id"] == "REC1")
        check("records: kb derived from conflict_key prefix (canonical)", recs[0]["kb"] == "familyoffice")
        check("records: conflict_key carried (the daily-note key)",
              recs[0]["conflict_key"] == "familyoffice/wiki/journal/2026-06-21.md")
        check("records: source carried", recs[0]["source"] == "claude-code")

        # ---- empty-bundle handling (scan excludes empties / prune_empty releases them) ----
        ev2 = os.path.join(root, "evidence_empty"); os.makedirs(ev2)
        nowe = datetime.now(timezone.utc)
        # a real (non-empty) ended session — must appear in scan, must NOT be pruned
        write_sess(ev2, "REAL", started=iso(nowe - timedelta(hours=3)), ended=iso(nowe - timedelta(hours=2)),
                   files=("x.md",), tool_counts={"Edit": 1})
        write_intents(ev2, "REAL", ["do the thing"])
        # an empty ended session — no files, no tools, no intents, no failures
        write_sess(ev2, "EMPTY", started=iso(nowe - timedelta(hours=3)), ended=iso(nowe - timedelta(hours=2)),
                   files=(), tool_counts={}, tools_failed=0)
        # an empty but still-LIVE session — not ready, must be left entirely alone
        write_sess(ev2, "EMPTYLIVE", started=iso(nowe - timedelta(minutes=10)), ended="",
                   files=(), tool_counts={}, tools_failed=0)

        sids = {b["id"] for b in ss.scan(ev2)}
        check("scan: empty ended session EXCLUDED from the work list", "EMPTY" not in sids)
        check("scan: real ended session still returned", "REAL" in sids)
        check("scan: empty live session not in work list", "EMPTYLIVE" not in sids)

        pres = ss.prune_empty(ev2)
        check("prune_empty: releases the empty ended session", "EMPTY" in pres["pruned_empty"])
        check("prune_empty: empty evidence flipped synthesized",
              synthesized_flag(os.path.join(ev2, "sess-EMPTY.md")) == "true")
        check("prune_empty: does NOT touch the real session", "REAL" not in pres["pruned_empty"])
        check("prune_empty: real session evidence still unsynthesized (work, not lost)",
              synthesized_flag(os.path.join(ev2, "sess-REAL.md")) == "false")
        check("prune_empty: leaves the empty LIVE session alone (not ready)",
              synthesized_flag(os.path.join(ev2, "sess-EMPTYLIVE.md")) == "false")
        check("prune_empty: idempotent (nothing left to prune on a second pass)",
              ss.prune_empty(ev2)["pruned_empty"] == [])

        # ---- ON2: fanned-out evaluator/judge sub-runs (canned prompt, NO files/tools) excluded ----
        # The real fan-out signature (basepair C4 genome-scorer): N siblings sharing an identical
        # canned evaluator PREAMBLE, each with no files/no tools — but the prompt TAILS differ (each
        # scores a different genome), so they cluster on a shared prefix, not exact text.
        PREAMBLE = ("You are an independent evaluator scoring a software genome — an AI-readable living "
                    "spec — on a fixed rubric. Score each dimension as an integer 1, 2, 3, 4, or 5 using "
                    "the anchors below. Score strictly and independently; do not inflate. ")  # >200 chars
        ev3 = os.path.join(root, "evidence_subrun"); os.makedirs(ev3)
        nows = datetime.now(timezone.utc)
        # a real human session — has files + tools, always work
        write_sess(ev3, "REALWORK", started=iso(nows - timedelta(hours=3)), ended=iso(nows - timedelta(hours=2)),
                   files=("plan.md",), tool_counts={"Edit": 3})
        write_intents(ev3, "REALWORK", ["build the thing"])
        # a LONE tool-free human conversation — intents but no files/no tools, NO siblings. This is the
        # regression guard: it must be treated as WORK (recorded), never dropped as a sub-run.
        write_sess(ev3, "CHAT", started=iso(nows - timedelta(hours=3)), ended=iso(nows - timedelta(hours=2)),
                   files=(), tool_counts={}, tools_failed=0)
        write_intents(ev3, "CHAT", ["should we use postgres or sqlite here, and why?"])
        # N judge sub-runs: identical canned preamble, distinct per-genome tails, no files/no tools
        for n in range(5):
            jid = f"JUDGE{n}"
            write_sess(ev3, jid, started=iso(nows - timedelta(hours=3)), ended=iso(nows - timedelta(hours=2)),
                       files=(), tool_counts={}, tools_failed=0)
            write_intents(ev3, jid, [PREAMBLE + f"Now score candidate genome #{n}: <repo blob {n}>."])
        sids3 = {b["id"] for b in ss.scan(ev3)}
        check("scan: fanned-out judge sub-runs excluded from the work list (ON2)",
              not any(s.startswith("JUDGE") for s in sids3))
        check("scan: real + lone tool-free human sessions both survive (ON2)",
              sids3 == {"REALWORK", "CHAT"})
        # confirmed fan-outs release (marked synthesized w/o a record) so they don't linger as residue
        pres3 = ss.prune_empty(ev3)
        check("prune: every fanned-out judge sub-run released without a record (ON2)",
              all(f"JUDGE{n}" in pres3.get("pruned_subruns", []) for n in range(5)))
        check("prune: a lone tool-free human session is NEVER released (regression guard, ON2)",
              "CHAT" not in pres3.get("pruned_subruns", []) and "CHAT" not in pres3["pruned_empty"]
              and synthesized_flag(os.path.join(ev3, "sess-CHAT.md")) == "false")
        check("prune: real work session is NOT released (ON2)",
              "REALWORK" not in pres3.get("pruned_subruns", []) and "REALWORK" not in pres3["pruned_empty"])

        # ---- G16c garden sweep --------------------------------------------------
        sweep_ev = os.path.join(root, "sweep_ev"); os.makedirs(sweep_ev)
        old, recent = time.time() - 10 * 86400, time.time() - 1 * 86400
        # synthesized + old -> swept
        write_sess(sweep_ev, "OLD", synthesized=True, mtime=old)
        write_intents(sweep_ev, "OLD", ["x"], synthesized=True, mtime=old)
        write_activity(sweep_ev, "2026-06-01", synthesized=True, mtime=old)
        # synthesized + recent -> kept
        write_sess(sweep_ev, "FRESH", synthesized=True, mtime=recent)
        # UNsynthesized + old -> kept (live work, never swept)
        write_sess(sweep_ev, "LIVE", synthesized=False, mtime=old)

        # a minimal install dir so sweep's state/vault globs are harmless no-ops
        install = os.path.join(root, "install")
        os.makedirs(os.path.join(install, "state", "queue.json.d"))
        os.makedirs(os.path.join(install, "vault"))

        # dormant when no evidence_dir
        b0, o0, e0 = garden_sweep.sweep(install, ttl_days=7, apply=False)
        check("G16c: evidence sweep dormant without --evidence-dir", e0 == [])

        b1, o1, e1 = garden_sweep.sweep(install, ttl_days=7, apply=False,
                                        evidence_dir=sweep_ev, evidence_ttl_days=7)
        names = {os.path.basename(p) for p in e1}
        check("G16c(dry): synthesized+old sess swept", "sess-OLD.md" in names)
        check("G16c(dry): synthesized+old intents swept", "intents-OLD.md" in names)
        check("G16c(dry): synthesized+old activity swept", "activity-2026-06-01.md" in names)
        check("G16c(dry): synthesized+recent KEPT", "sess-FRESH.md" not in names)
        check("G16c(dry): UNsynthesized+old KEPT (live work)", "sess-LIVE.md" not in names)
        check("G16c(dry): nothing deleted on dry-run", os.path.exists(os.path.join(sweep_ev, "sess-OLD.md")))

        garden_sweep.sweep(install, ttl_days=7, apply=True, evidence_dir=sweep_ev, evidence_ttl_days=7)
        check("G16c(apply): synthesized+old evidence deleted", not os.path.exists(os.path.join(sweep_ev, "sess-OLD.md")))
        check("G16c(apply): UNsynthesized+old evidence survives", os.path.exists(os.path.join(sweep_ev, "sess-LIVE.md")))
        check("G16c(apply): synthesized+recent evidence survives", os.path.exists(os.path.join(sweep_ev, "sess-FRESH.md")))

        print(f"\n{PASS} passed, {FAIL} failed")
        sys.exit(1 if FAIL else 0)
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
