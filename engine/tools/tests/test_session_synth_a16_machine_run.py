#!/usr/bin/env python3
"""A16 — machine-run marker: the pipeline must not capture its own fleet.

Nightly fleet runs (headless `claude -p` via the deploy runners) do real tool work, so the
work/empty/subrun classifier scores them as WORK and session-capture synthesizes 5-9
self-referential session records per night. The fix: the runner exports AIOS_MACHINE_RUN,
the Layer-1 evidence hook stamps `machine_run: true` into the sess-<id>.md frontmatter, and
session_synth gives those bundles a dedicated `machine` disposition:

  - scan        : EXCLUDES machine-run bundles (never synthesized into a record), even though
                  they carry files/tools that would otherwise make them unambiguous "work".
  - prune-empty : RELEASES ready machine-run bundles (marks synthesized without a record — the
                  run's own context-log line is the durable record of fleet work, so nothing is
                  lost) and reports them as `pruned_machine`.
  - a LIVE machine run (still running, fresh) is left entirely alone (caught next run);
  - an interactive session (no machine_run key, or machine_run: false) is classified exactly
    as before — the regression guard.

Hermetic: every file lives under a tempfile.mkdtemp() tree; the live install is never touched.
Run: python engine/tools/tests/test_session_synth_a16_machine_run.py
"""
import os, sys, json, tempfile, shutil
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)
import session_synth as ss

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok   {name}")
    else:
        FAIL += 1; print(f"  FAIL {name}")


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def write_sess(ev, sid, project="general", started=None, ended=None,
               files=("a.md",), tool_counts=None, tools_failed=0,
               synthesized=False, machine_run=None):
    """machine_run: None -> key absent (pre-A16 evidence / interactive), else 'true'/'false'."""
    started = started or iso(datetime.now(timezone.utc))
    tc = json.dumps({"Bash": 4, "Read": 9} if tool_counts is None else tool_counts)
    flist = ", ".join(f'"{f}"' for f in files)
    machine_line = "" if machine_run is None else f"machine_run: {'true' if machine_run else 'false'}\n"
    body = (
        "---\n"
        "type: session\n"
        f"id: {sid}\n"
        f"project: {project}\n"
        f"started_at: {started}\n"
        f"ended_at: {ended or ''}\n"
        "cwd: C:/x\n"
        f"files: [{flist}]\n"
        f"tool_counts: {tc}\n"
        f"tools_failed: {tools_failed}\n"
        "transcript_path: C:/t.jsonl\n"
        f"{machine_line}"
        "ephemeral: true\n"
        f"synthesized: {'true' if synthesized else 'false'}\n"
        "tags: [session, auto-capture, evidence]\n"
        "---\n"
        f"# Session {sid[:8]}\n"
    )
    p = os.path.join(ev, f"sess-{sid}.md")
    open(p, "w", encoding="utf-8", newline="\n").write(body)
    return p


def write_intents(ev, sid, prompts):
    lines = "".join(f"- 10:0{i} | {pr}\n" for i, pr in enumerate(prompts))
    body = (
        "---\n"
        "type: intents\n"
        f"session: {sid}\n"
        f"captured_utc: {iso(datetime.now(timezone.utc))}\n"
        "ephemeral: true\n"
        "synthesized: false\n"
        "tags: [intents, auto-capture, evidence]\n"
        "---\n\n"
        f"{lines}"
    )
    p = os.path.join(ev, f"intents-{sid}.md")
    open(p, "w", encoding="utf-8", newline="\n").write(body)
    return p


def synthesized_flag(p):
    return ss._get(ss._frontmatter(ss._read(p)), "synthesized")


def main():
    root = tempfile.mkdtemp(prefix="aios-a16-")
    try:
        ev = os.path.join(root, "evidence"); os.makedirs(ev)
        now = datetime.now(timezone.utc)
        ago3, ago2 = iso(now - timedelta(hours=3)), iso(now - timedelta(hours=2))

        # A nightly fleet run: machine_run true, REAL tool/file work (the exact shape that used
        # to score as "work" and become a self-referential record).
        write_sess(ev, "FLEET", project="general", started=ago3, ended=ago2,
                   files=("state/context-log.jsonl",), tool_counts={"Bash": 12, "Read": 30},
                   machine_run=True)
        write_intents(ev, "FLEET", ["You are the aios 'aios-ingest' stage running UNATTENDED..."])
        # An interactive session, same window — no machine_run key (pre-A16 evidence shape).
        write_sess(ev, "HUMAN", project="aios", started=ago3, ended=ago2,
                   files=("engine/tools/session_synth.py",), tool_counts={"Edit": 3})
        write_intents(ev, "HUMAN", ["ship the machine-run marker"])
        # An interactive session that explicitly carries machine_run: false.
        write_sess(ev, "HUMANF", project="aios", started=ago3, ended=ago2,
                   files=("b.md",), tool_counts={"Edit": 1}, machine_run=False)
        # A machine run still LIVE (running, fresh) — not ready; must be left entirely alone.
        write_sess(ev, "FLEETLIVE", started=iso(now - timedelta(minutes=10)), ended="",
                   machine_run=True)
        # A machine run that is EMPTY (no intents/files/tools) — machine wins, still released.
        write_sess(ev, "FLEETEMPTY", started=ago3, ended=ago2,
                   files=(), tool_counts={}, tools_failed=0, machine_run=True)
        # A machine run that looks like a candidate sub-run (intents, no artifacts) but has NO
        # canned-prompt siblings — machine disposition must win over the lone-candidate=work rule.
        write_sess(ev, "FLEETCAND", started=ago3, ended=ago2,
                   files=(), tool_counts={}, tools_failed=0, machine_run=True)
        write_intents(ev, "FLEETCAND", ["You are the aios 'aios-garden' stage running UNATTENDED..."])

        # ---- scan: fleet runs never become work -------------------------------
        sids = {b["id"] for b in ss.scan(ev)}
        check("scan: machine-run bundle with real tool work EXCLUDED", "FLEET" not in sids)
        check("scan: machine-run lone-candidate shape EXCLUDED (machine beats work-default)",
              "FLEETCAND" not in sids)
        check("scan: machine-run empty shape EXCLUDED", "FLEETEMPTY" not in sids)
        check("scan: interactive session (no machine_run key) still captured", "HUMAN" in sids)
        check("scan: interactive session (machine_run: false) still captured", "HUMANF" in sids)
        check("scan: live machine run not in work list", "FLEETLIVE" not in sids)
        hb = next(b for b in ss.scan(ev) if b["id"] == "HUMAN")
        check("scan: interactive bundle carries machine_run False", hb.get("machine_run") is False)

        # ---- prune-empty: ready machine bundles released, live one untouched ----
        pres = ss.prune_empty(ev)
        pm = pres.get("pruned_machine", [])
        check("prune: ready machine-run bundles released as pruned_machine",
              set(pm) == {"FLEET", "FLEETEMPTY", "FLEETCAND"})
        check("prune: machine evidence flipped synthesized",
              synthesized_flag(os.path.join(ev, "sess-FLEET.md")) == "true")
        check("prune: machine intents flipped synthesized too",
              synthesized_flag(os.path.join(ev, "intents-FLEET.md")) == "true")
        check("prune: LIVE machine run left alone (not ready)",
              synthesized_flag(os.path.join(ev, "sess-FLEETLIVE.md")) == "false")
        check("prune: interactive sessions NEVER released (work, not lost)",
              synthesized_flag(os.path.join(ev, "sess-HUMAN.md")) == "false"
              and synthesized_flag(os.path.join(ev, "sess-HUMANF.md")) == "false")
        check("prune: machine ids not double-reported as empty/subrun",
              "FLEET" not in pres["pruned_empty"] and "FLEET" not in pres.get("pruned_subruns", [])
              and "FLEETEMPTY" not in pres["pruned_empty"]
              and "FLEETCAND" not in pres.get("pruned_subruns", []))
        check("prune: idempotent (nothing left to prune on a second pass)",
              ss.prune_empty(ev).get("pruned_machine", []) == [])

        print(f"\n{PASS} passed, {FAIL} failed")
        sys.exit(1 if FAIL else 0)
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
