#!/usr/bin/env python3
"""brief_session.py - the DETERMINISTIC ledger helper for the stationed brief walk.

Manages install/state/brief-session.json: the single source of truth for walk progress
across sessions. The foreground brief engine reads/writes this; the precompute does NOT
touch it (keeps precompute read-only over records).

Ledger schema (§3.1 of brief-walk-spec.md):
{
  "walk_id": "2026-06-22",
  "started_utc": "<ISO>",
  "updated_utc": "<ISO>",
  "status": "in_progress | complete | abandoned",
  "station_order": ["kb", "<domain-1>", "<domain-2>", "..."],
  "current_station": "kb",
  "stations": {
    "kb":           { "status": "pending|in_progress|complete", "items_total": 13, "decided": 0, "deferred": 0 },
    "system":       { "status": "pending|in_progress|complete", "items_total": 5, "decided": 1, "deferred": 0 },
    "personal":     { "status": "pending", "items_total": 0, "decided": 0, "deferred": 0 },
    "familyoffice": { "status": "pending", "items_total": 0, "decided": 0, "deferred": 0 },
    "dev":          { "status": "pending", "items_total": 0, "decided": 0, "deferred": 0 }
  },

  Two-stage walk: "kb" (Stage 1 — knowledge-base processing) is walked FIRST, then the four
  domain stations (Stage 2 — task cards). The "kb" station's items are NOT carried in the cache's
  `stations` object; they are the held queue drafts classified `kb_class:"hygiene"` (the ones with a
  real staged draft on disk — faithful daily-note merges + reciprocity fixes). The foreground seeds
  this station's items_total from those held items and renders them from `held[]`. `validate_cache`
  therefore still requires only the four DOMAIN keys; an extra "kb" key is tolerated, not required.
  "decisions": [
    { "item_id": "", "title": "", "station": "system", "choice": "system|claude|other|defer",
      "action": "<one-line of what ran/queued>", "executed": true,
      "thread": "state/threads/{id}.md", "ts": "<ISO>" }
  ],
  "deferrals": [
    { "item_id": "", "title": "", "station": "", "reason": "<one word>",
      "deferred_on": "2026-06-22", "resurface": "next-walk" }
  ]
}

Atomic writes: write to a temp file + os.replace (POSIX-safe), then re-read + json.load to
confirm it parses. Retry up to 3x on a torn write.

Stdlib-only. Fact-free. Matches lane_policy.py style.

CLI (same interface style as queue_tx.py — called from SKILL.md prose):
  python brief_session.py load        <state_path>
  python brief_session.py status      <state_path>
  python brief_session.py new_walk    <state_path> <walk_id>
  python brief_session.py resume_or_new <state_path> <walk_id>
  python brief_session.py record_decision <state_path> <item_id> <station> <choice> <action> [--executed] [--thread T] [--title T] [--notion-write JSON]
  python brief_session.py record_deferral <state_path> <item_id> <station> <reason> <deferred_on> [--title T]
  python brief_session.py advance     <state_path>
  python brief_session.py start_over  <state_path> <archive_dir>
  python brief_session.py validate_cache <cache_json_path> --domains a,b,c [--standup standup.json]
"""
import json
import os
import re
import sys
import time
import glob

# Walk order: "kb" (Stage 1 — knowledge-base processing) first, then the install's domain
# stations (Stage 2 — task cards) in the caller's --order (from the profile's domain groups).
# Fact-free: the engine knows only its own "kb" station; with no --order the seed's key order
# is used. No person's domain set is hardcoded here.
DEFAULT_STATION_ORDER = ["kb"]

VALID_CHOICES     = {"system", "claude", "other", "defer"}
VALID_GRADES      = {"1", "2a", "2b"}   # "0" / null = system_voice absent
GRADES_WITH_CITE  = {"1", "2a"}         # cite required for these; optional for "2b"
VALID_STATUSES    = {"pending", "in_progress", "complete"}

# ─────────────────────────── internal helpers ───────────────────────────

def _utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_json(path):
    """Parse a JSON file, or None if missing/unparseable."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _atomic_write(path, obj):
    """Atomic JSON write (tmp + os.replace; short PermissionError retry for a concurrent
    Windows reader holding the destination open). Returns True on success."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(3):
            try:
                os.replace(tmp, path)
                return True
            except PermissionError:
                time.sleep(0.2)
        return False
    except OSError:
        return False


def _die(msg):
    print("FAIL:", msg, file=sys.stderr)
    sys.exit(1)


# ─────────────────────────── public API ───────────────────────────

def load(state_path):
    """Load the ledger dict from state_path, or None if absent/unparseable."""
    if not os.path.exists(state_path):
        return None
    obj = _read_json(state_path)
    if not isinstance(obj, dict):
        return None
    return obj


def new_walk(state_path, walk_id, station_order, stations_seed, carryover_deferrals=None):
    """Create and atomically write a fresh ledger.

    stations_seed: dict mapping station -> items_total  e.g. {"system": 5, "personal": 3, ...}
    carryover_deferrals: list of deferral dicts from the previous walk (resurface: next-walk)
    Returns the new ledger dict.
    """
    now = _utcnow()
    stations = {}
    for s in station_order:
        stations[s] = {
            "status": "in_progress" if s == station_order[0] else "pending",
            "items_total": stations_seed.get(s, 0),
            "decided": 0,
            "deferred": 0,
        }
    ledger = {
        "walk_id": walk_id,
        "started_utc": now,
        "updated_utc": now,
        "status": "in_progress",
        "station_order": list(station_order),
        "current_station": station_order[0],
        "stations": stations,
        "decisions": [],
        "deferrals": list(carryover_deferrals) if carryover_deferrals else [],
    }
    if not _atomic_write(state_path, ledger):
        _die(f"new_walk: failed to write ledger to {state_path} after retries")
    return ledger


def resume_or_new(state_path, walk_id, station_order, stations_seed):
    """Return ("resume", ledger) if an in_progress ledger exists, else ("new", new ledger).

    On "new", carries over open deferrals from the previous ledger (resurface: next-walk).
    """
    existing = load(state_path)
    if isinstance(existing, dict) and existing.get("status") == "in_progress":
        return ("resume", existing)
    # Carry over open deferrals (those with resurface == "next-walk")
    carryover = []
    if isinstance(existing, dict):
        for d in existing.get("deferrals", []):
            if d.get("resurface") == "next-walk":
                carryover.append(d)
    ledger = new_walk(state_path, walk_id, station_order, stations_seed,
                      carryover_deferrals=carryover)
    return ("new", ledger)


def record_decision(state_path, item_id, title, station, choice, action,
                    executed, thread=None, notion_write=None):
    """Append a decision to the ledger, bump station decided count, update updated_utc.

    choice must be one of: system | claude | other | defer
    notion_write, if given, is {"page_id","field","to"} — the Notion write this decision
    intended, so a later reconciler can detect a write that never landed.
    """
    ledger = load(state_path)
    if ledger is None:
        _die(f"record_decision: no ledger at {state_path}")
    if choice not in VALID_CHOICES:
        raise ValueError(f"record_decision: choice {choice!r} not in {sorted(VALID_CHOICES)}")
    if station not in ledger.get("stations", {}):
        raise ValueError(f"record_decision: unknown station {station!r}")
    now = _utcnow()
    entry = {
        "item_id": item_id,
        "title": title,
        "station": station,
        "choice": choice,
        "action": action,
        "executed": bool(executed),
        "ts": now,
    }
    if thread is not None:
        entry["thread"] = thread
    if notion_write is not None:
        entry["notion_write"] = notion_write
    ledger["decisions"].append(entry)
    if station in ledger.get("stations", {}):
        ledger["stations"][station]["decided"] = (
            ledger["stations"][station].get("decided", 0) + 1
        )
    ledger["updated_utc"] = now
    if not _atomic_write(state_path, ledger):
        _die(f"record_decision: failed to write ledger after retries")
    return ledger


def record_deferral(state_path, item_id, title, station, reason, deferred_on):
    """Append a deferral (one-word reason), bump station deferred count, update updated_utc."""
    ledger = load(state_path)
    if ledger is None:
        _die(f"record_deferral: no ledger at {state_path}")
    if station not in ledger.get("stations", {}):
        raise ValueError(f"record_deferral: unknown station {station!r}")
    now = _utcnow()
    entry = {
        "item_id": item_id,
        "title": title,
        "station": station,
        "reason": reason,          # one word, per spec
        "deferred_on": deferred_on,
        "resurface": "next-walk",
    }
    ledger["deferrals"].append(entry)
    if station in ledger.get("stations", {}):
        ledger["stations"][station]["deferred"] = (
            ledger["stations"][station].get("deferred", 0) + 1
        )
    ledger["updated_utc"] = now
    if not _atomic_write(state_path, ledger):
        _die(f"record_deferral: failed to write ledger after retries")
    return ledger


def advance(state_path):
    """Mark current_station complete, set the next pending station to in_progress.

    If no stations remain, set ledger status = complete.
    Returns the new current_station string, or None if the walk is now complete.
    """
    ledger = load(state_path)
    if ledger is None:
        _die(f"advance: no ledger at {state_path}")
    order = ledger.get("station_order", [])
    stations = ledger.get("stations", {})
    current = ledger.get("current_station")

    # Mark the current station complete.
    if current and current in stations:
        stations[current]["status"] = "complete"

    # Find the next pending station.
    next_station = None
    for s in order:
        if stations.get(s, {}).get("status") == "pending":
            next_station = s
            break

    if next_station is not None:
        stations[next_station]["status"] = "in_progress"
        ledger["current_station"] = next_station
    else:
        ledger["status"] = "complete"
        ledger["current_station"] = None

    ledger["updated_utc"] = _utcnow()
    if not _atomic_write(state_path, ledger):
        _die(f"advance: failed to write ledger after retries")
    return next_station


def start_over(state_path, archive_dir):
    """Move the existing ledger to archive_dir/{walk_id}-{n}.json (n increments to avoid clobber).

    Returns None; caller then calls new_walk to create a fresh ledger.
    """
    ledger = load(state_path)
    if ledger is None:
        return None  # nothing to archive
    os.makedirs(archive_dir, exist_ok=True)
    walk_id = ledger.get("walk_id", "unknown")
    # Find next available n.
    n = 1
    while True:
        dest = os.path.join(archive_dir, f"{walk_id}-{n}.json")
        if not os.path.exists(dest):
            break
        n += 1
    # Write archived copy then remove the live ledger.
    if not _atomic_write(dest, ledger):
        _die(f"start_over: failed to write archive to {dest}")
    os.remove(state_path)
    return None


def status_summary(state_path):
    """Return a dict with per-station status/decided/deferred/total + current_station + overall status.

    Backs the resume card in the brief.
    """
    ledger = load(state_path)
    if ledger is None:
        return {"status": "none", "current_station": None, "stations": {}}
    result = {
        "status": ledger.get("status", "unknown"),
        "walk_id": ledger.get("walk_id"),
        "current_station": ledger.get("current_station"),
        "stations": {},
        "decisions_count": len(ledger.get("decisions", [])),
        "deferrals_count": len(ledger.get("deferrals", [])),
    }
    for s, info in ledger.get("stations", {}).items():
        result["stations"][s] = {
            "status": info.get("status", "pending"),
            "decided": info.get("decided", 0),
            "deferred": info.get("deferred", 0),
            "total": info.get("items_total", 0),
        }
    return result


def _validate_system_voice(sv, prefix):
    """Validate one item's `system_voice` field. `sv` is either None (Grade 0, accepted) or a
    dict with `grade` in VALID_GRADES, non-empty `text`, and `cite` required for grades in
    GRADES_WITH_CITE. Returns a list of error strings (empty = valid).

    Shared by validate_cache's station-item loop AND its act-item loop (A88) so the grade/cite
    rules are asserted in exactly one place instead of two copies drifting apart.
    """
    if sv is None:
        return []  # Grade 0 — explicitly accepted
    if not isinstance(sv, dict):
        return [f"{prefix}: system_voice must be null or a dict"]
    errs = []
    grade = sv.get("grade")
    if grade not in VALID_GRADES:
        errs.append(f"{prefix}: system_voice.grade {grade!r} not in {sorted(VALID_GRADES)}")
    if not sv.get("text"):
        errs.append(f"{prefix}: system_voice.text is required when system_voice is present")
    if grade in GRADES_WITH_CITE and not sv.get("cite"):
        errs.append(f"{prefix}: system_voice.cite is required for grade {grade!r}")
    return errs


def validate_cache(cache_obj, required_domains=None, standup=None):
    """Validate a brief-cache.json payload for the new stations/station_counts keys (spec §3.2).

    Returns (ok: bool, errors: list[str]).

    Rules:
    - station_counts/stations cover required_domains (or agree structurally; "kb" counts-only)
      (an extra "kb" count for the Stage-1 knowledge-base station is tolerated, not required)
    - stations has the 4 domain keys (a "kb" key is NOT expected here — Stage-1 items live in held[])
    - every item has: title, domain, claude_voice.text
    - system_voice is either None (Grade 0, accepted) or has:
        - grade in {"1", "2a", "2b"}
        - text
        - cite for grades "1" and "2a" (required); for "2b" cite is optional
    - act is REQUIRED (absent -> error: a gather always writes the key, so an absent `act` can
      only be a stale writer still emitting the pre-rename `needs_you`). `act: []` is valid —
      a real quiet day must still render. Same per-item rules as a station item.
    - headline_bubbles (optional — DERIVED, so the render can recompute it): when present,
      `headline_bubbles[0]` must equal "%d need you" % len(act). A chip that disagrees with the
      list it counts is the 5/7/21 regression.
    - standup (optional): a parsed state/factory/standup.json dict. When given it MUST carry a
      `delta` list of objects — absent/malformed is factory_standup contract drift and errors
      (it used to degrade into a vacuous pass, or raise). Every delta item with an id must have
      a matching card in stations["gm"] — the standup->station hand-off that used to silently
      drop items (A88/Task 7).

    Contract boundary throughout: ABSENCE of authored data is a break; EMPTINESS is a
    legitimate state. Derived data (headline_bubbles) is exempt — it can be recomputed.
    """
    errors = []

    if not isinstance(cache_obj, dict):
        return False, ["cache_obj must be a dict"]
    # Fact-free domain check: with an explicit required_domains (the profile's groups) both
    # blocks must cover it; without one, station_counts and stations must agree on the SAME
    # non-empty PERSON-domain set (a partial/degraded cache disagrees with itself). The
    # engine's own "kb" station is counts-only and exempt from the symmetry rule.
    if required_domains is not None:
        REQUIRED_DOMAINS = set(required_domains)
    else:
        sc0 = cache_obj.get("station_counts")
        st0 = cache_obj.get("stations")
        sc_keys = set(sc0.keys()) if isinstance(sc0, dict) else set()
        st_keys = set(st0.keys()) if isinstance(st0, dict) else set()
        REQUIRED_DOMAINS = (sc_keys | st_keys) - {"kb"}
        if not REQUIRED_DOMAINS:
            errors.append("no domains present in station_counts/stations")

    # station_counts check
    sc = cache_obj.get("station_counts")
    if not isinstance(sc, dict):
        errors.append("station_counts is missing or not a dict")
    else:
        missing = REQUIRED_DOMAINS - set(sc.keys())
        if missing:
            errors.append(f"station_counts missing domains: {sorted(missing)}")

    # stations block check
    stations = cache_obj.get("stations")
    if not isinstance(stations, dict):
        errors.append("stations is missing or not a dict")
    else:
        missing_s = REQUIRED_DOMAINS - set(stations.keys())
        if missing_s:
            errors.append(f"stations missing domains: {sorted(missing_s)}")
        # per-item checks
        for domain, items in stations.items():
            if not isinstance(items, list):
                errors.append(f"stations.{domain} must be a list")
                continue
            for idx, item in enumerate(items):
                prefix = f"stations.{domain}[{idx}]"
                if not isinstance(item, dict):
                    errors.append(f"{prefix}: must be a dict")
                    continue
                # required fields
                if "title" not in item:
                    errors.append(f"{prefix}: missing 'title'")
                if "domain" not in item:
                    errors.append(f"{prefix}: missing 'domain'")
                cv = item.get("claude_voice")
                if not isinstance(cv, dict) or not cv.get("text"):
                    errors.append(f"{prefix}: missing claude_voice.text")
                # system_voice: null is Grade 0 (accepted); dict must be valid
                errors.extend(_validate_system_voice(item.get("system_voice"), prefix))

    # act block (A88): the Act list is the FIRST thing the brief shows and was the least-
    # asserted object in the chain — `needs_you` (now `act`, Task 5's rename) never appeared
    # in this function, so a gather emitting an empty or malformed Act passed with OK.
    #
    # REQUIRED, not optional. The rename carried no fallback (deliberately — a fallback hides
    # a stale writer), so against the pre-rename cache on disk the whole chain reported
    # healthy while the Act list rendered EMPTY: status fresh -> validate OK -> overview ''.
    # An "optional act" made that silence legal. The boundary: ABSENCE is a contract break (a
    # real gather always writes the key, so absent == stale/pre-rename writer); EMPTINESS is a
    # legitimate state (a quiet day where nothing needs the owner must still render — outlawing it
    # would brick the ENTIRE brief via Invariant 4 on a good day). Spec §1's `needs_you: []`
    # failure is caught by the headline-chip conservation below: the harm was never the empty
    # list, it was the empty list under a hand-typed "5 need you".
    act = cache_obj.get("act")
    if act is None:
        errors.append(
            "act is missing — the Act list is the brief's first surface and a gather ALWAYS "
            "writes the key. An absent 'act' means a stale writer still emitting 'needs_you' "
            "(Task 5's rename), not a quiet day; a real quiet day is act: [].")
    else:
        if not isinstance(act, list):
            errors.append(f"act: expected a list, got {type(act).__name__}")
        else:
            for idx, item in enumerate(act):
                prefix = f"act[{idx}]"
                if not isinstance(item, dict):
                    errors.append(f"{prefix}: must be a dict")
                    continue
                if "title" not in item:
                    errors.append(f"{prefix}: missing 'title'")
                if "domain" not in item:
                    errors.append(f"{prefix}: missing 'domain'")
                cv = item.get("claude_voice")
                if not isinstance(cv, dict) or not cv.get("text"):
                    errors.append(f"{prefix}: missing claude_voice.text")
                errors.extend(_validate_system_voice(item.get("system_voice"), prefix))

    # headline_bubbles (A88): the masthead chips must not contradict the list they count. The
    # 5/7/21 regression was exactly this — "5 need you" as model prose over an act[] of 7 and a
    # standup total of 21, three numbers on one screen with nothing comparing them.
    # brief_render.compute_headline_bubbles() derives the chips, but the cache-contract prose is
    # what a cache-writing model actually follows, so the assertion — not the function — is the
    # load-bearing half: it closes the hole regardless of which prose the model reads.
    # DERIVED data, so absence is recoverable (the render just computes them) — unlike `act`
    # and `delta`, which are authored and cannot be reconstructed. Present -> must agree.
    # The chip format is pinned to compute_headline_bubbles' own "%d need you" by
    # test_validate_cache_headline_chip_matches_the_renderer_format_exactly.
    bubbles = cache_obj.get("headline_bubbles")
    if bubbles is not None:
        if not isinstance(bubbles, list):
            errors.append(f"headline_bubbles: expected a list, got {type(bubbles).__name__}")
        elif bubbles:
            expected = "%d need you" % (len(act) if isinstance(act, list) else 0)
            if bubbles[0] != expected:
                errors.append(
                    "headline_bubbles[0] %r disagrees with the Act list it counts (expected %r). "
                    "The chips are computed by brief_render.compute_headline_bubbles(cache, "
                    "standup) — never hand-typed." % (bubbles[0], expected))

    # A88 (A3): conservation across the standup -> station hand-off. The brief used to print
    # "21 need you" from standup.json while the walk rendered four unrelated cards from the cache,
    # and nothing compared them. A delta item with no card is a silent drop.
    if standup:
        # `standup.get("delta") or []` degraded a CROSS-REPO CONTRACT BREAK into a vacuous pass:
        # env-side drift (delta[] -> changes[]) left this check iterating an empty list and
        # reporting ok=True — "absence looks like OK" at exactly the repo boundary where drift is
        # likeliest, since standup.json is written in claude-env and read here. Same rule as
        # `act`: a caller passing standup= asserts there IS a standup to conserve against, so an
        # absent delta[] means the file is not a standup and the check CANNOT run — fail loud.
        # (An empty delta[] is a real quiet day and stays valid.) A non-list delta also used to
        # crash this function outright: a dict iterates to str keys -> AttributeError on .get.
        delta = standup.get("delta")
        if not isinstance(delta, list):
            errors.append(
                "standup: 'delta' is missing or not a list (got %s) — a standup handed to "
                "validate_cache MUST carry delta[]; this is factory_standup contract drift, "
                "not a quiet day (a quiet day is delta: [])." % type(delta).__name__)
            delta = []
        # A gate must RETURN (ok, errors), never raise — a delta of bare ids (["H54"]) used to
        # crash the .get() below with AttributeError, bricking the brief harder than the
        # vacuous pass this block exists to prevent.
        bad = [i for i in delta if not isinstance(i, dict)]
        if bad:
            errors.append(
                "standup: delta[] items must be objects, got %d non-object entr%s (e.g. %r) — "
                "factory_standup contract drift."
                % (len(bad), "y" if len(bad) == 1 else "ies", bad[0]))
            delta = [i for i in delta if isinstance(i, dict)]
        carded = {str(i.get("item_id") or i.get("id") or "")
                  for i in (cache_obj.get("stations", {}).get("gm") or [])}
        # Id-less items (id: "") are the standup collector's deliberate "◷" backlog seeds —
        # it routes them AROUND its dedupe sidecar, so they are ALWAYS in delta and ALWAYS
        # reported via the collector's own errors[] (already surfaced in the factory panel).
        # An id-less item can never be carded BY ID, so holding it to this rule is asserting
        # something structurally impossible — and because Invariant 4 is "INVALID -> don't
        # render", that impossible assertion bricks the ENTIRE brief over a single seed that
        # was never meant to have a card (Task 7). Only real-id items are held to "must have
        # a card"; an id-less item mixed in must not mask a genuine unaccounted real-id item.
        missing = [i for i in delta if i.get("id") and str(i.get("id")) not in carded]
        if missing:
            errors.append(
                "standup delta %d · stations.gm %d · %d unaccounted: %s"
                % (len(delta), len(carded), len(missing),
                   ", ".join(str(i.get("id") or i.get("title")) for i in missing)))

    # settle block (optional; when present, candidates must be well-formed)
    SETTLE_TRANSITIONS = {"done", "in_progress", "due_rolled"}
    settle = cache_obj.get("settle")
    if settle is not None:
        if not isinstance(settle, dict):
            errors.append("settle must be a dict")
        else:
            candidates = settle.get("candidates", [])
            if candidates and not isinstance(candidates, list):
                errors.append("settle.candidates must be a list")
            else:
                for idx, cand in enumerate(candidates or []):
                    pfx = f"settle.candidates[{idx}]"
                    if not isinstance(cand, dict):
                        errors.append(f"{pfx}: must be a dict"); continue
                    for req in ("task_id", "title", "proposed_transition"):
                        if not cand.get(req):
                            errors.append(f"{pfx}: missing {req!r}")
                    tr = cand.get("proposed_transition")
                    if tr is not None and tr not in SETTLE_TRANSITIONS:
                        errors.append(f"{pfx}: proposed_transition {tr!r} not in {sorted(SETTLE_TRANSITIONS)}")

    ok = len(errors) == 0
    return ok, errors


# ─────────────────────────── cache status + scope (A25) ───────────────────────────
# The brief SKILL's "is the cache USABLE" boolean (age + capability parity) and the cwd→scope
# map lookup were prose a model re-derived every trigger. They are pure logic; they live here.

def cache_status(cache_path, max_age_min=720, notion_enabled=False, session_has_notion=False,
                 now_epoch=None):
    """USABILITY of the brief cache — TWO tests, both must pass (a cache can be minutes old
    and still unusable):
      age        : generated_utc within max_age_min.
      capability : if the profile enables Notion AND this session can reach it, a cache
                   gathered notion-blind (source_counts.notion_live false / 0 sources) is
                   DEGRADED — treat exactly like stale (the headless-precompute guard).
    status: 'missing' | 'stale' | 'degraded' | 'fresh'."""
    obj = _read_json(cache_path)
    if obj is None:
        return {"status": "missing", "exists": False}
    gen = obj.get("generated_utc") or ""
    try:
        import calendar
        gen_epoch = calendar.timegm(time.strptime(gen[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        gen_epoch = 0.0
    now = time.time() if now_epoch is None else now_epoch
    age_min = max(0.0, (now - gen_epoch) / 60.0)
    age_ok = gen_epoch > 0 and age_min <= float(max_age_min)
    sc = obj.get("source_counts") or {}
    notion_live = bool(sc.get("notion_live"))
    degraded = bool(notion_enabled and session_has_notion and not notion_live)
    status = "fresh" if (age_ok and not degraded) else ("degraded" if (age_ok and degraded) else "stale")
    return {"status": status, "exists": True, "generated_utc": gen,
            "age_min": round(age_min, 1), "age_ok": age_ok,
            "notion_live": notion_live, "degraded": degraded}


def resolve_scope(cwd, domain_map, default_scope="all", vault_root=None, kb_map=None,
                  override=None, cache_write=False):
    """cwd → brief scope. Precedence: cache-write ('all', always the full superset) > explicit
    override > Projects/<name> via domain_map > a KB-root cwd via kb_map > default ('all' — an
    unmapped cwd shows EVERYTHING, never silently hides a silo)."""
    if cache_write:
        return "all"
    if override:
        return override
    # Compare path segments case-INSENSITIVELY (os.path.normcase): on Windows the case-insensitive FS
    # can surface a cwd as `c:\...\projects\aios` (lowercase `projects`), which a case-sensitive
    # `seg == "Projects"` would miss → fall through to default_scope='all' and show EVERY silo instead
    # of the intended one (A49). normcase is identity on POSIX, so case-sensitive matching is preserved
    # there. Returns the ORIGINAL scope values (only the comparison is normalized).
    _nc = os.path.normcase
    parts = [p for p in re.split(r"[\\/]", os.path.abspath(cwd)) if p]
    ncparts = [_nc(p) for p in parts]
    dm_nc = {_nc(k): v for k, v in (domain_map or {}).items()}
    for i, seg in enumerate(ncparts[:-1]):
        if seg == _nc("Projects") and ncparts[i + 1] in dm_nc:
            return dm_nc[ncparts[i + 1]]
    if vault_root and kb_map:
        vparts = [_nc(p) for p in re.split(r"[\\/]", os.path.abspath(vault_root)) if p]
        if ncparts[:len(vparts)] == vparts and len(parts) > len(vparts):
            folder = ncparts[len(vparts)]
            for kb, f in kb_map.items():
                if _nc(f) == folder:
                    return kb
    return default_scope or "all"


def held_summary(queue_path, now_epoch=None, nag_days=7.0, group_threshold=20):
    """A15: deterministic review-lane AGE + BATCH-GROUPING summary for the brief's Phase-A panel.

    The review lane rotted invisibly for two months because the panel never showed AGE — residue
    hides in an unaged list. This computes, from the queue alone:
      count / oldest (first time the item reached `awaiting`) / nag (oldest older than nag_days),
      age_line — the render-ready one-liner the brief echoes VERBATIM every run, and
      groups[] — mechanical batch classes (kb + target folder + recommended ballot) so a
      >group_threshold lane renders as a handful of batch decisions, not N rows.
    Held = stage `awaiting` AND lane review/confirm — the LANE filter only. The panel's
    kb_class (hygiene vs decision) and scope filters are the brief skill's job: it must
    intersect groups[] ids with its gathered Stage-1 set before offering batch approval.
    Fail-loud: an unreadable/invalid queue returns {"error": ...} — never a silent zero."""
    import calendar
    d = _read_json(queue_path)
    if not isinstance(d, dict) or not isinstance(d.get("queue"), list):
        return {"error": "queue not readable/parseable: %s" % queue_path}
    now = time.time() if now_epoch is None else now_epoch

    def _epoch(ts):
        try:
            return calendar.timegm(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            return None

    held = [it for it in d["queue"] if isinstance(it, dict)
            and it.get("stage") == "awaiting" and it.get("lane") in ("review", "confirm")]

    oldest = None
    for it in held:
        cand = [_epoch(h.get("ts")) for h in (it.get("history") or [])
                if isinstance(h, dict) and h.get("stage") == "awaiting"]
        cand = [c for c in cand if c] or [
            c for c in (_epoch(it.get("first_drafted_utc")), _epoch(it.get("captured_utc"))) if c]
        if not cand:
            continue
        first = min(cand)
        if oldest is None or first < oldest[1]:
            oldest = (it.get("id"), first)

    groups = {}
    for it in held:
        ck = str(it.get("conflict_key") or "")
        parts = ck.split("/")
        folder = "/".join(parts[1:-1]) if len(parts) > 2 else "(unkeyed)"
        key = (it.get("kb") or "?", folder, str(it.get("recommended") or "-"))  # str: a non-str ballot must not make the key unhashable
        g = groups.setdefault(key, {"kb": key[0], "folder": key[1], "recommended": key[2],
                                    "count": 0, "ids": [], "sample_slugs": []})
        g["count"] += 1
        g["ids"].append(it.get("id"))
        if len(g["sample_slugs"]) < 3:
            g["sample_slugs"].append(os.path.splitext(parts[-1])[0] if parts else "")

    count = len(held)
    nag = False
    if count == 0:
        age_line = "Review lane: clear ✓"
    else:
        oid, oepoch = oldest if oldest else (None, None)
        if oepoch is None:
            age_line = "⏳ Review lane: %d held · oldest age unknown" % count
        else:
            days = max(0.0, (now - oepoch) / 86400.0)
            nag = days > float(nag_days)
            since = time.strftime("%Y-%m-%d", time.gmtime(oepoch))
            age_line = "⏳ Review lane: %d held · oldest %dd (%s, %s)%s" % (
                count, int(days), since, oid,
                "  ⚠️ aging past %dd — sit with the panel" % int(nag_days) if nag else "")
    return {"count": count, "nag": nag, "nag_days": float(nag_days),
            "oldest_id": oldest[0] if oldest else None,
            "oldest_days": (round((now - oldest[1]) / 86400.0, 1) if oldest else None),
            "age_line": age_line,
            "grouped": count > int(group_threshold),
            "group_threshold": int(group_threshold),
            "groups": sorted(groups.values(), key=lambda g: -g["count"])}


# ─────────────────────────── CLI ───────────────────────────

def _utf8_stdio():
    """Force UTF-8 on stdout/stderr — walk cards/queue items carry emoji/flag glyphs (⚑, 🔵) and a
    native Windows console defaults to cp1252, which would crash the JSON print. A non-Windows console is
    already UTF-8, so this only ever helps."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


if __name__ == "__main__":
    _utf8_stdio()
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    op = args[0]

    if op == "load":
        path = args[1]
        obj = load(path)
        if obj is None:
            print("null")
        else:
            print(json.dumps(obj, indent=2, ensure_ascii=False))

    elif op == "status":
        path = args[1]
        summary = status_summary(path)
        print(json.dumps(summary, indent=2, ensure_ascii=False))

    elif op == "new_walk":
        # new_walk <state_path> <walk_id> [--order s1,s2,s3,s4] [--seed s1:n,s2:n,...]
        path = args[1]
        walk_id = args[2]
        rest = args[3:]
        order = None                       # resolved after parsing: --order > kb + seed keys
        seed = {}
        i = 0
        while i < len(rest):
            if rest[i] == "--order" and i + 1 < len(rest):
                order = rest[i + 1].split(",")
                i += 2
            elif rest[i] == "--seed" and i + 1 < len(rest):
                for pair in rest[i + 1].split(","):
                    if ":" in pair:
                        k, v = pair.split(":", 1)
                        seed[k.strip()] = int(v.strip())
                i += 2
            else:
                i += 1
        if order is None:
            order = ["kb"] + [k for k in seed if k != "kb"]
        ledger = new_walk(path, walk_id, order, seed)
        print(json.dumps(ledger, indent=2, ensure_ascii=False))

    elif op == "resume_or_new":
        path = args[1]
        walk_id = args[2]
        rest = args[3:]
        order = None                       # resolved after parsing: --order > kb + seed keys
        seed = {}
        i = 0
        while i < len(rest):
            if rest[i] == "--order" and i + 1 < len(rest):
                order = rest[i + 1].split(",")
                i += 2
            elif rest[i] == "--seed" and i + 1 < len(rest):
                for pair in rest[i + 1].split(","):
                    if ":" in pair:
                        k, v = pair.split(":", 1)
                        seed[k.strip()] = int(v.strip())
                i += 2
            else:
                i += 1
        if order is None:
            order = ["kb"] + [k for k in seed if k != "kb"]
        mode, ledger = resume_or_new(path, walk_id, order, seed)
        print(f"mode: {mode}")
        print(json.dumps(ledger, indent=2, ensure_ascii=False))

    elif op == "record_decision":
        # record_decision <path> <item_id> <station> <choice> <action> [--executed] [--thread T]
        #                 [--title T] [--notion-write JSON]
        path, item_id, station, choice, action = args[1], args[2], args[3], args[4], args[5]
        rest = args[6:]
        executed = "--executed" in rest
        thread = None
        title = item_id
        notion_write = None
        i = 0
        while i < len(rest):
            if rest[i] == "--thread" and i + 1 < len(rest):
                thread = rest[i + 1]; i += 2
            elif rest[i] == "--title" and i + 1 < len(rest):
                title = rest[i + 1]; i += 2
            elif rest[i] == "--notion-write" and i + 1 < len(rest):
                notion_write = json.loads(rest[i + 1]); i += 2
            else:
                i += 1
        ledger = record_decision(path, item_id, title, station, choice, action, executed,
                                 thread, notion_write)
        print(json.dumps({"ok": True, "decisions": len(ledger["decisions"])}, indent=2))

    elif op == "record_deferral":
        # record_deferral <path> <item_id> <station> <reason> <deferred_on> [--title T]
        path, item_id, station, reason, deferred_on = (
            args[1], args[2], args[3], args[4], args[5]
        )
        rest = args[6:]
        title = item_id
        i = 0
        while i < len(rest):
            if rest[i] == "--title" and i + 1 < len(rest):
                title = rest[i + 1]; i += 2
            else:
                i += 1
        ledger = record_deferral(path, item_id, title, station, reason, deferred_on)
        print(json.dumps({"ok": True, "deferrals": len(ledger["deferrals"])}, indent=2))

    elif op == "advance":
        path = args[1]
        next_s = advance(path)
        print(json.dumps({"next_station": next_s}, indent=2))

    elif op == "start_over":
        path = args[1]
        archive_dir = args[2]
        start_over(path, archive_dir)
        print(json.dumps({"ok": True, "archived_to": archive_dir}, indent=2))

    elif op == "validate_cache":
        # validate_cache <cache.json> --domains a,b,c  (the profile's domain-group keys)
        # --domains is REQUIRED (A88): without it the expected set is derived FROM THE CACHE, so a
        # cache that dropped a whole silo validates OK — the check defaults to not conserving.
        if "--domains" not in args[2:]:
            print("validate_cache: --domains is required (the profile's domain-group keys). "
                  "Without it the expected set is derived from the cache itself and a dropped "
                  "silo validates OK.", file=sys.stderr)
            sys.exit(2)
        path = args[1]
        rest = args[2:]
        req = [s.strip() for s in rest[rest.index("--domains") + 1].split(",") if s.strip()]
        obj = _read_json(path)
        if obj is None:
            print("FAIL: could not parse cache JSON")
            sys.exit(1)
        standup_obj = None
        if "--standup" in rest:
            with open(rest[rest.index("--standup") + 1], encoding="utf-8") as f:
                standup_obj = json.load(f)
        ok, errs = validate_cache(obj, required_domains=req, standup=standup_obj)
        if ok:
            print("OK")
        else:
            print("INVALID:")
            for e in errs:
                print(" ", e)
            sys.exit(1)

    elif op == "cache-status":
        # cache-status <cache.json> [--max-age-min N] [--notion-enabled] [--session-has-notion]
        #              [--cwd P --domain-map JSON] [--default-scope S] [--vault-root V --kb-map JSON]
        #              [--override S] [--cache-write]
        path = args[1]
        rest = args[2:]
        def _flag(name):
            return name in rest
        def _val(name, default=None):
            return rest[rest.index(name) + 1] if name in rest else default
        st = cache_status(path,
                          max_age_min=float(_val("--max-age-min", 720)),
                          notion_enabled=_flag("--notion-enabled"),
                          session_has_notion=_flag("--session-has-notion"))
        st["scope"] = resolve_scope(_val("--cwd", os.getcwd()),
                                    json.loads(_val("--domain-map", "{}")),
                                    default_scope=_val("--default-scope", "all"),
                                    vault_root=_val("--vault-root"),
                                    kb_map=json.loads(_val("--kb-map", "{}")),
                                    override=_val("--override"),
                                    cache_write=_flag("--cache-write"))
        print(json.dumps(st, indent=2, ensure_ascii=False))

    elif op == "held-summary":
        # held-summary <queue.json> [--nag-days N] [--group-threshold N] [--now-epoch N]
        path = args[1]
        rest = args[2:]
        def _hv(name, default=None):
            return rest[rest.index(name) + 1] if name in rest else default
        hs = held_summary(path,
                          now_epoch=(float(_hv("--now-epoch")) if _hv("--now-epoch") else None),
                          nag_days=float(_hv("--nag-days", 7)),
                          group_threshold=int(_hv("--group-threshold", 20)))
        print(json.dumps(hs, indent=2, ensure_ascii=False))
        if "error" in hs:
            sys.exit(1)

    else:
        print(f"FAIL: unknown op {op!r}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)
