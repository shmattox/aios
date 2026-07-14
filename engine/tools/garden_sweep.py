#!/usr/bin/env python3
"""garden_sweep.py - the MECHANICAL teardown half of Stage 5 (garden).

Deterministic operational-residue cleanup — the teardown counterpart to the atomic-write/self-heal
protection (queue_tx) and the staging lifecycle. This is the AUTO / fix-then-tell part of garden:
it sweeps litter, NOT knowledge. Wiki/skill CONTENT changes are NObusiness of this tool — those go
through the gate. No deps beyond the stdlib + queue_tx.

Sweeps:
  1. Stale state backups: *.last-good / *.tmp / *.proposed older than the TTL (default 7 days).
  2. Orphan staging drafts: a `<vault>/{kb}/wiki/staging/{slug}.md` whose queue item is
     shipped/rejected/reverted/absent — its reason to exist is gone (the G5 litter class).
     A draft whose item is still `sorted`/`awaiting` is KEPT (it is live work, never swept).
  3. Synthesized session evidence (G16c): sess-/intents-/activity- files in the evidence dir whose
     frontmatter is `synthesized: true` AND older than the evidence TTL — the session-capture stage
     has already mined them into a raw/sessions record, so the mechanical trace is spent. Un-synthesized
     evidence is NEVER swept (live work, same rule as sorted/awaiting drafts). Only runs when an
     --evidence-dir is given: pre-cutover the profile leaves it unset (the live hook still owns real
     evidence), so this sweep is dormant until cutover points it at a vault-local evidence dir.

File presence checks go through _present (long-path-safe isfile).

Usage:
  python garden_sweep.py <install_dir>            # dry-run (report only)
  python garden_sweep.py <install_dir> --apply    # delete the residue
  python garden_sweep.py <install_dir> --ttl-days 7
  python garden_sweep.py <install_dir> --evidence-dir <path> [--evidence-ttl-days 7]
  python garden_sweep.py <install_dir> --vault-root <path> --kb-map '{"personal":"01_Personal",...}'

--vault-root (A19): sweep orphan staging drafts under THIS vault instead of the default
`<install_dir>/vault` (the test-vault layout) — this is what un-dormants the REAL-vault orphan
sweep. --kb-map is the profile's `vault.live_kb_map` (kb short name -> vault folder); when given,
ONLY mapped folders are swept (an unmapped folder is skipped, never treated as orphan) and queue
kb short names resolve to their folders. Without a map, folder == kb (test-vault identity).
"""
import json, os, sys, glob, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import queue_tx
import session_synth as ss

DONE_STAGES = {"shipped", "rejected", "reverted"}   # an orphan's item is in one of these (or absent)
# ^ update when adding a terminal stage: an unknown stage counts as LIVE (fail-closed), so a
#   terminal stage missing here makes its litter immortal rather than sweepable.


def _win_long(path):
    """Windows long-path shim (A19, same as rewind.py): plain open()/stat() fail past ~260 chars
    without the `\\\\?\\` prefix, so a healthy deep-vault file read as absent. No-op off Windows,
    on short paths, and on already-prefixed/UNC paths (a >250-char UNC vault is NOT supported —
    it would need the `\\\\?\\UNC\\` form; the vault is local by design)."""
    if os.name == "nt":
        ap = os.path.abspath(path)
        if len(ap) > 250 and not ap.startswith("\\\\"):
            return "\\\\?\\" + ap
    return path


def _present(p):
    return os.path.isfile(_win_long(p))


def _age_days(p):
    try:
        return (time.time() - os.path.getmtime(_win_long(p))) / 86400.0
    except Exception:
        return 0.0


def _norm_rel(p):
    """Normalize a vault-relative path for set comparison: forward slashes, no leading `./` or
    edge slashes, casefolded (Windows FS is case-insensitive; a case-diverging pointer must still
    protect its file — over-matching only ever KEEPS more, the safe direction)."""
    p = p.replace("\\", "/").strip("/")
    while p.startswith("./"):
        p = p[2:]
    return p.casefold()


def _load_queue_clean(queue_path):
    """Return (queue_list, ok). ok only if the queue parses + validates (or is a legitimately
    absent fresh store) — a deletion decision must never run off unreadable/invalid state
    (independent-review finding)."""
    if not os.path.exists(queue_path):
        return [], True                      # fresh store: nothing enqueued yet
    d = queue_tx._read_json(queue_path)
    if isinstance(d, dict) and queue_tx.validate(d) is None:
        return d["queue"], True
    return [], False


def _synthesized_evidence(evidence_dir, ttl_days):
    """G16c: session evidence (sess-/intents-/activity-) that is `synthesized: true` AND older than
    the TTL. Reads the flag (never trusts mtime alone for synthesized-ness); a torn/unreadable file
    is skipped, not swept — never delete evidence we can't confirm was mined."""
    spent = []
    if not evidence_dir or not os.path.isdir(evidence_dir):
        return spent
    for pat in ("sess-*.md", "intents-*.md", "activity-*.md"):
        for p in glob.glob(os.path.join(evidence_dir, pat)):
            try:
                fm = ss._frontmatter(ss._read(p))
            except Exception:
                continue
            if ss._get(fm, "synthesized") == "true" and _age_days(p) > ttl_days:
                spent.append(p)
    return spent


def sweep(install_dir, ttl_days=7, apply=False, evidence_dir=None, evidence_ttl_days=None,
          vault_root=None, kb_map=None):
    state = os.path.join(install_dir, "state")
    vault = vault_root or os.path.join(install_dir, "vault")   # A19: real vault via --vault-root
    folder_to_kb = {v: k for k, v in (kb_map or {}).items()}   # vault folder -> kb short name
    queue_path = os.path.join(state, "queue.json")

    # 1. stale state backups (queue-independent; age-based)
    backups = []
    for pat in ("*.last-good", "*.tmp", "*.proposed"):
        for p in glob.glob(os.path.join(state, pat)):
            if _age_days(p) > ttl_days:
                backups.append(p)

    # 2. orphan staging drafts — KB-AWARE match, and only when the queue is cleanly loadable.
    # A23: liveness is SET membership, never a last-writer-wins dict — a file referenced by ANY
    # live item (draft_path, payload_path, or ck-derived (kb, slug)) is protected regardless of
    # iteration order; DONE stages are collected for reporting only. An item with a missing
    # stage counts as live (fail-closed). The 07-04 incident: a live journal item shared its
    # (kb, slug) with later shipped/rejected daily siblings and the dict collapse deleted its
    # fresh draft.
    q, healthy = _load_queue_clean(queue_path)
    live_keys, live_paths = set(), set()              # (kb, slug) / normalized vault-rel path
    done_by_key, done_by_path = {}, {}                # same shapes -> stage, REPORTING ONLY
    for it in q:
        ck = it.get("conflict_key") or ""
        kb = it.get("kb") or (ck.split("/")[0] if "/" in ck else "")
        slug = os.path.splitext(os.path.basename(ck))[0]
        stage = it.get("stage")
        is_live = stage not in DONE_STAGES            # missing/unknown stage -> live (fail-closed)
        paths = [_norm_rel(p) for p in (it.get("draft_path"), it.get("payload_path"))
                 if isinstance(p, str) and p.strip()]
        if is_live:
            if slug:
                live_keys.add((kb, slug))
            live_paths.update(paths)
        else:
            if slug:
                done_by_key.setdefault((kb, slug), stage)
            for p in paths:
                done_by_path.setdefault(p, stage)

    orphans, kept_files, skipped_unmapped = [], [], []
    real_vault_unmapped = bool(vault_root) and not kb_map   # a REAL vault handed in with no map:
    if real_vault_unmapped:                                 # folder names can't match kb short
        pass                                                # names -> everything would read absent.
    elif healthy:                                           # Refuse the orphan sweep (fail-safe,
                                                            # same posture as the unhealthy-queue
                                                            # refusal below).
        # staging lives at <vault>/{folder}/wiki/staging/ (legacy state/staging/ is retired).
        # Default vault: folder == kb short name. Real vault (--vault-root + --kb-map): folder is
        # the mapped name (01_Personal, ...) — resolve it back to the kb; an UNMAPPED folder is
        # skipped entirely (never guessed at, never orphaned).
        for sp in glob.glob(os.path.join(vault, "*", "wiki", "staging", "*.md")):
            if os.path.basename(sp).lower() == "readme.md":
                continue   # folder documentation, not a draft — never an orphan (A19 triage finding)
            rel = os.path.relpath(sp, vault).replace("\\", "/")
            folder = rel.split("/")[0]
            if kb_map:
                kb = folder_to_kb.get(folder)
                if kb is None:
                    skipped_unmapped.append(sp)
                    continue
            else:
                kb = folder
            slug = os.path.splitext(os.path.basename(sp))[0]
            # A draft is LIVE if ANY live item references it — by path pointer or (kb, slug).
            # Slug collisions are routine (journal dates, recurring titles); one live reference
            # protects the file no matter how many DONE items share the key.
            nrel = _norm_rel(rel)
            if nrel in live_paths or (kb, slug) in live_keys:
                kept_files.append(sp)
            else:                                      # item gone, or already past staging -> orphan
                st = done_by_path.get(nrel) or done_by_key.get((kb, slug)) or "absent"
                orphans.append((sp, st))

    # 3. synthesized session evidence past TTL (G16c) — dormant unless an evidence_dir is provided
    ev_ttl = evidence_ttl_days if evidence_ttl_days is not None else ttl_days
    evidence = _synthesized_evidence(evidence_dir, ev_ttl)

    print("GARDEN SWEEP report:")
    print(f"  stale backups (>{ttl_days}d)     : {len(backups)}")
    for p in backups:
        print(f"      {os.path.basename(p)}  ({_age_days(p):.1f}d)")
    if real_vault_unmapped:
        print("  orphan staging drafts         : SKIPPED (--vault-root given WITHOUT --kb-map — real-")
        print("                                   vault folders can't match kb short names; refusing)")
    elif not healthy:
        print("  orphan staging drafts         : SKIPPED (queue not cleanly loadable — refusing to")
        print("                                   delete drafts off rebuilt/stale state)")
    else:
        print(f"  orphan staging drafts         : {len(orphans)}   (vault: {vault})")
        for p, st in orphans:
            print(f"      {os.path.relpath(p, vault)}  (item stage={st})")
        print(f"  live staging drafts KEPT      : {len(kept_files)} (sorted/awaiting - never swept)")
        if skipped_unmapped:
            print(f"  unmapped-folder drafts SKIPPED: {len(skipped_unmapped)} (folder not in kb-map - never orphaned)")

    if evidence_dir:
        print(f"  synthesized evidence (>{ev_ttl}d) : {len(evidence)}")
        for p in evidence:
            print(f"      {os.path.basename(p)}  ({_age_days(p):.1f}d)")
    # (no evidence_dir -> G16c sweep dormant; pre-cutover the live hook still owns real evidence)

    if not apply:
        print("  (dry-run; pass --apply to delete)")
        return backups, orphans, evidence

    swept = 0
    for p in backups + [o[0] for o in orphans] + evidence:
        try:
            os.remove(_win_long(p)); swept += 1
        except Exception as e:
            print(f"  WARN could not remove {p}: {e}")
    print(f"  swept {swept} residue file(s).")
    return backups, orphans, evidence


def _utf8_stdio():
    """Force UTF-8 on stdout/stderr — vault slugs/filenames can carry non-cp1252 glyphs and a native
    Windows console defaults to cp1252, which would crash the print. A non-Windows console is already UTF-8, so
    this only ever helps."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


if __name__ == "__main__":
    _utf8_stdio()
    a = sys.argv[1:]
    if not a:
        print(__doc__); sys.exit(1)
    install = a[0]
    ttl = 7
    if "--ttl-days" in a:
        ttl = float(a[a.index("--ttl-days") + 1])
    evidence_dir = a[a.index("--evidence-dir") + 1] if "--evidence-dir" in a else None
    ev_ttl = float(a[a.index("--evidence-ttl-days") + 1]) if "--evidence-ttl-days" in a else None
    vault_root = a[a.index("--vault-root") + 1] if "--vault-root" in a else None
    kb_map = json.loads(a[a.index("--kb-map") + 1]) if "--kb-map" in a else None
    sweep(install, ttl_days=ttl, apply=("--apply" in a),
          evidence_dir=evidence_dir, evidence_ttl_days=ev_ttl,
          vault_root=vault_root, kb_map=kb_map)
