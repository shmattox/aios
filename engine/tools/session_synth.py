#!/usr/bin/env python3
"""session_synth.py - deterministic scaffolding for Stage: session-capture (G16b).

The NARRATIVE synthesis (mining a transcript into a real Focus / Outcome / Why) is the AGENT's job -
that needs judgment and lives in skills/session-capture/SKILL.md. This helper does the MECHANICAL,
testable parts around it so the stage stays honest and idempotent:

  scan    <evidence_dir>             -> JSON list of unsynthesized, COMPLETED session evidence bundles
  mark    <evidence_dir> --id <id>.. -> flip synthesized:false -> true on a session's evidence (atomic);
                                        a day's activity log flips only once ALL its sessions are done
  records <sessions_dir>             -> JSON list of `type: session-record` files (the capture
                                        `sessions` adapter's discovery; EVIDENCE files are excluded)
  prune-empty <evidence_dir>         -> release no-work ready bundles WITHOUT a record, so they stop
                                        re-appearing in `scan` every run: EMPTY (no intents/files/tools
                                        — aborted/instant sessions), fanned-out evaluator/judge
                                        SUB-RUNS (a canned prompt repeated across >=2 siblings, no
                                        files/tools), AND MACHINE runs (`machine_run: true` — the
                                        fleet's own headless sessions, A16). Reports `pruned_empty` +
                                        `pruned_subruns` + `pruned_machine`. `scan` already excludes
                                        all three; a LONE tool-free session stays work.

Evidence is what the Layer-1 hook (hook-handler.js, G16a) deposits in the evidence dir:
  - sess-<id>.md       per-session metadata (project, cwd, files, tool_counts, tools_failed, transcript)
  - intents-<id>.md    the user's prompts, append-only - the "why" the mechanical trace never had
  - activity-<date>.md per-day tool trace (project | tool | file [ | FAILED])
All carry `synthesized: false` until this stage mines them into a raw/sessions record; garden
(garden_sweep.py, G16c) then TTL-sweeps the synthesized evidence. Un-synthesized evidence is NEVER
swept (live work) - the flag this tool sets is the "safe to reclaim" signal.

Fact-free: the cwd->kb domain map and the TTL are profile facts the SKILL applies; this tool only
reads/writes the evidence flags. No deps beyond the stdlib.

Usage:
  python session_synth.py scan <evidence_dir> [--stale-hours 18]
  python session_synth.py mark <evidence_dir> --id <session-id> [--id <session-id> ...]
  python session_synth.py prune-empty <evidence_dir> [--stale-hours 18]
"""
import json, os, re, sys, glob
from datetime import datetime, timezone


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    return m.group(1) if m else ""


def _get(fm, key):
    m = re.search(rf"(?m)^{re.escape(key)}:[ \t]*(.*)$", fm)
    if not m:
        return ""
    v = m.group(1).strip()
    # Strip a matched surrounding quote pair: newer writers emit quoted YAML scalars
    # (`type: "session-record"`), and a naive value compare would treat the quotes as
    # part of the value and silently drop every such record. (Lists/JSON objects like
    # `["a"]` / `{...}` keep different first/last chars, so they're untouched.)
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1]
    return v


def _get_list(fm, key):
    m = re.search(rf"(?m)^{re.escape(key)}:[ \t]*\[(.*)\][ \t]*$", fm)
    if not m:
        return []
    return [s.strip().strip('"') for s in m.group(1).split(",") if s.strip()]


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _age_hours(iso):
    dt = _parse_iso(iso)
    if not dt:
        return 1e9
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def _mtime_date(p):
    return datetime.fromtimestamp(os.path.getmtime(p), timezone.utc).strftime("%Y-%m-%d")


def _session_date(path, fm=None):
    fm = fm if fm is not None else _frontmatter(_read(path))
    started = _get(fm, "started_at")
    return started[:10] if started else _mtime_date(path)


def _intents(evidence_dir, sid):
    p = os.path.join(evidence_dir, f"intents-{sid}.md")
    if not os.path.exists(p):
        return []
    out = []
    for line in _read(p).splitlines():
        m = re.match(r"^- \d{2}:\d{2} \| (.+)$", line)
        if m:
            out.append(m.group(1))
    return out


def _is_empty(intents, files, tool_counts, tools_failed):
    """A bundle with NO evidence of work — no prompts, no files, no tool calls, no failures.
    These are aborted/instant sessions (opened then closed). There is nothing to mine into a
    Focus/Outcome/Why, so they are not 'work to synthesize' — they are released by `prune_empty`
    without a record (nothing to lose). Conservative on purpose: ANY signal makes it non-empty."""
    return not intents and not files and not tool_counts and not tools_failed


def _is_candidate_subrun(intents, files, tool_counts, tools_failed):
    """A POSSIBLE evaluator/judge sub-run: carries a prompt but produced NO work artifact (no files,
    no tools, no failures). On its own this is ambiguous — it could be a fanned-out machine judge OR
    a genuine tool-free human conversation. It is only CONFIRMED a sub-run when it clusters with a
    sibling carrying the same canned prompt (see `_subrun_key` / `_classify`); a LONE candidate is
    treated as work, so a real tool-free session is never dropped or released. This is the guard the
    naive 'no tools => sub-run' rule lacked (it would silently lose tool-free human sessions)."""
    return bool(intents) and not files and not tool_counts and not tools_failed


def _base_disposition(intents, files, tool_counts, tools_failed, machine_run=False):
    """First pass for a ready bundle: 'machine' (a headless fleet run stamped `machine_run: true`
    by the evidence hook when the runner exports AIOS_MACHINE_RUN — the pipeline must never
    capture its own fleet, A16), 'empty' (nothing at all), 'candidate' (prompt but no artifact —
    a possible sub-run, resolved by `_classify`), or 'work' (touched files / invoked tools / hit a
    failure — unambiguously a real session). Machine wins outright: fleet runs DO real tool work,
    which is exactly why the artifact heuristics can't catch them."""
    if machine_run:
        return "machine"
    if _is_empty(intents, files, tool_counts, tools_failed):
        return "empty"
    if _is_candidate_subrun(intents, files, tool_counts, tools_failed):
        return "candidate"
    return "work"


# A fan-out scores N different items with the SAME canned evaluator prompt, so the per-item tail
# differs but a long leading PREFIX is identical. Cluster candidates on that prefix; a group of >=2
# is a machine fan-out, a singleton is a lone (human) session. Fact-free — no prompt text hardcoded.
_SUBRUN_KEY_PREFIX = 200       # chars of the normalized prompt used to group a fan-out
_SUBRUN_FANOUT_MIN = 2         # a cluster of this many candidates is a sub-run fan-out


def _subrun_key(intents):
    """Whitespace-normalized leading prefix of a candidate's prompts — the cluster key. The prefix
    cap lets a TEMPLATED evaluator prompt (identical preamble, per-item tail) still group with its
    siblings while keeping distinct human prompts in their own singleton groups."""
    return " ".join(" ".join(i.split()) for i in intents)[:_SUBRUN_KEY_PREFIX]


def _classify(ready):
    """Resolve a list of (bundle, base_disposition) into (bundle, 'work'|'empty'|'subrun'). A
    'candidate' (no artifact, has a prompt) becomes 'subrun' ONLY if >=_SUBRUN_FANOUT_MIN candidates
    share its canned-prompt key (a fan-out); otherwise it is 'work' — so a lone tool-free human
    session is recorded, never silently dropped. 'empty'/'work' pass through unchanged."""
    counts = {}
    for b, base in ready:
        if base == "candidate":
            counts[_subrun_key(b["intents"])] = counts.get(_subrun_key(b["intents"]), 0) + 1
    out = []
    for b, base in ready:
        if base == "candidate":
            out.append((b, "subrun" if counts[_subrun_key(b["intents"])] >= _SUBRUN_FANOUT_MIN else "work"))
        else:
            out.append((b, base))
    return out


def _ready_bundles(evidence_dir, stale_hours=18.0):
    """Yield (bundle, base_disposition) for every unsynthesized session that is READY — ENDED, or
    never closed but now stale past `stale_hours` (a crashed session shouldn't be skipped forever).
    Live (running + fresh) and already-synthesized sessions are skipped. `base_disposition` is
    'work' | 'empty' | 'candidate' (see `_base_disposition`); `_classify` then resolves 'candidate'
    into 'work' or 'subrun' by fan-out clustering. Both `scan` (work only) and `prune_empty`
    (empty + sub-run residue) classify this same walk, so they never disagree."""
    for sp in sorted(glob.glob(os.path.join(evidence_dir, "sess-*.md"))):
        try:
            fm = _frontmatter(_read(sp))
        except Exception:
            continue
        if not fm or _get(fm, "synthesized") == "true":
            continue
        sid = _get(fm, "id") or os.path.basename(sp)[len("sess-"):-len(".md")]
        ended = _get(fm, "ended_at")
        started = _get(fm, "started_at")
        running = not ended
        if running and _age_hours(started) < stale_hours:
            continue  # live session - catch it next run (same contract as dev-session-capture)
        try:
            tool_counts = json.loads(_get(fm, "tool_counts") or "{}")
        except Exception:
            tool_counts = {}
        files = _get_list(fm, "files")
        tools_failed = int(_get(fm, "tools_failed") or 0)
        intents = _intents(evidence_dir, sid)
        machine_run = _get(fm, "machine_run") == "true"
        bundle = {
            "id": sid,
            "project": _get(fm, "project") or "general",
            "cwd": _get(fm, "cwd"),
            "started_utc": started,
            "ended_utc": ended,
            "date": _session_date(sp, fm),
            "transcript_path": _get(fm, "transcript_path"),
            "files": files,
            "tool_counts": tool_counts,
            "tools_failed": tools_failed,
            "intents": intents,
            "running_stale": running,
            "machine_run": machine_run,
        }
        yield bundle, _base_disposition(intents, files, tool_counts, tools_failed, machine_run)


def scan(evidence_dir, stale_hours=18.0):
    """The work list: unsynthesized, ENDED-or-stale sessions that have real work to mine into a
    record. EXCLUDES bundles with no narrative — EMPTY (no intents/files/tools — aborted/instant
    sessions), fanned-out evaluator/judge SUB-RUNS (a canned prompt repeated across >=2 siblings,
    no files/tools — machine fan-out, ON2), AND MACHINE runs (`machine_run: true` — the fleet's own
    headless sessions; the pipeline never captures itself, A16). A LONE tool-free session is kept as
    work (recorded), so a real human conversation is never dropped. Excluded bundles are released by
    `prune_empty`, never synthesized into a stub."""
    return [b for b, disp in _classify(list(_ready_bundles(evidence_dir, stale_hours))) if disp == "work"]


def prune_empty(evidence_dir, stale_hours=18.0):
    """Release ready bundles that must never become a record — EMPTY (no intents/files/tools),
    evaluator/judge SUB-RUNS (a canned prompt but no files/tools, ON2), and MACHINE runs
    (`machine_run: true`, A16) — for garden sweep WITHOUT a record. Safe by construction: empties
    and sub-runs carry no evidence of work, and a machine run's durable record is the context-log
    line the fleet stage itself writes — synthesizing it again would be the self-referential
    capture A16 exists to stop. Returns the `mark` report plus `pruned_empty`, `pruned_subruns`,
    and `pruned_machine` (the ids released in each class), so the stage can log what it reclaimed.
    This is what stops all three classes re-appearing in `scan` every run."""
    empties, subruns, machines = [], [], []
    for b, disp in _classify(list(_ready_bundles(evidence_dir, stale_hours))):
        if disp == "empty":
            empties.append(b["id"])
        elif disp == "subrun":
            subruns.append(b["id"])
        elif disp == "machine":
            machines.append(b["id"])
    res = mark(evidence_dir, empties + subruns + machines)
    res["pruned_empty"] = empties
    res["pruned_subruns"] = subruns
    res["pruned_machine"] = machines
    return res


def records(sessions_dir):
    """List the SESSION-RECORD files in a raw/sessions dir (the capture `sessions` adapter's discovery).
    Filters by `type: session-record` so the cheap EVIDENCE files (sess-/intents-/activity-, which also
    live here) are never enqueued — only the synthesized record rides the pipeline. `conflict_key` is
    canonical (the carried daily-note key); `kb` is derived from its prefix (Stage Contract)."""
    out = []
    for p in sorted(glob.glob(os.path.join(sessions_dir, "*.md"))):
        try:
            fm = _frontmatter(_read(p))
        except Exception:
            continue
        if _get(fm, "type") != "session-record":
            continue
        ck = _get(fm, "conflict_key")
        kb = ck.split("/")[0] if "/" in ck else _get(fm, "domain")
        sid = _get(fm, "id")
        if not kb or not sid:
            # malformed record (synthesis VERIFY should have caught it) — don't emit a broken queue
            # item with kb:''; surface it on stderr instead of silently dropping or mis-routing it.
            print(f"WARN session_synth.records: skipping malformed record (kb/id missing): {p}", file=sys.stderr)
            continue
        out.append({
            "file": os.path.abspath(p),
            "id": sid,
            "kb": kb,
            "conflict_key": ck,
            "source": _get(fm, "source"),
            "date": (_get(fm, "started_utc") or "")[:10],
        })
    return out


def _set_synthesized(path):
    """Atomically flip `synthesized: false` -> `synthesized: true` in the frontmatter. Idempotent;
    returns True if the file now reads synthesized (already-true counts), False if absent/unwritable.
    Write -> re-read & validate -> os.replace (the env's atomic-validated-write)."""
    if not os.path.exists(path):
        return False
    try:
        text = _read(path)
    except Exception:
        return False
    fm = _frontmatter(text)
    if not fm:
        return False
    if _get(fm, "synthesized") == "true":
        return True  # already done - idempotent
    new = re.sub(r"(?m)^synthesized:[ \t]*false[ \t]*$", "synthesized: true", text, count=1)
    if new == text:
        return False  # had synthesized:false in fm per _get, but no literal line matched -> bail safe
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    if _get(_frontmatter(_read(tmp)), "synthesized") != "true":
        os.remove(tmp)
        return False
    os.replace(tmp, path)
    return True


def _date_fully_synthesized(evidence_dir, date):
    """True iff no unsynthesized session remains for `date` - the gate to reclaim that day's
    activity log (shared day-level evidence, so it can only be released once every session is done)."""
    for sp in glob.glob(os.path.join(evidence_dir, "sess-*.md")):
        try:
            fm = _frontmatter(_read(sp))
        except Exception:
            return False
        if _session_date(sp, fm) == date and _get(fm, "synthesized") != "true":
            return False
    return True


def mark(evidence_dir, ids):
    """Mark each session's evidence synthesized; release a day's activity log only when that day
    has no unsynthesized sessions left."""
    marked = {"sessions": [], "intents": [], "activity": []}
    dates = set()
    for sid in ids:
        sp = os.path.join(evidence_dir, f"sess-{sid}.md")
        if os.path.exists(sp):
            dates.add(_session_date(sp))
        if _set_synthesized(sp):
            marked["sessions"].append(sid)
        if _set_synthesized(os.path.join(evidence_dir, f"intents-{sid}.md")):
            marked["intents"].append(sid)
    for d in sorted(dates):
        if _date_fully_synthesized(evidence_dir, d):
            if _set_synthesized(os.path.join(evidence_dir, f"activity-{d}.md")):
                marked["activity"].append(d)
    return marked


def _utf8_stdio():
    """Force UTF-8 on stdout/stderr — session records carry emoji/flag glyphs and a native Windows
    console defaults to cp1252, which would crash the JSON print. A non-Windows console is already UTF-8, so
    this only ever helps."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


if __name__ == "__main__":
    _utf8_stdio()
    a = sys.argv[1:]
    if len(a) < 2:
        print(__doc__)
        sys.exit(1)
    cmd, evidence_dir = a[0], a[1]
    if cmd == "scan":
        stale = 18.0
        if "--stale-hours" in a:
            stale = float(a[a.index("--stale-hours") + 1])
        print(json.dumps(scan(evidence_dir, stale_hours=stale), indent=2))
    elif cmd == "records":
        print(json.dumps(records(evidence_dir), indent=2))
    elif cmd == "mark":
        ids = [a[i + 1] for i, x in enumerate(a) if x == "--id" and i + 1 < len(a)]
        if not ids:
            print("mark needs at least one --id <session-id>")
            sys.exit(1)
        print(json.dumps(mark(evidence_dir, ids), indent=2))
    elif cmd == "prune-empty":
        stale = 18.0
        if "--stale-hours" in a:
            stale = float(a[a.index("--stale-hours") + 1])
        print(json.dumps(prune_empty(evidence_dir, stale_hours=stale), indent=2))
    else:
        print(f"unknown command: {cmd}")
        sys.exit(1)
