#!/usr/bin/env python3
"""A12 — capture->gate completes on a NON-GIT vault, with zero git dependency.

The engine's undo is file-based (ship.py revert pointer + rewind.py snapshot), NOT git — no engine
tool shells out to git. This guard drives the real chain (capture -> sort -> ingest -> ship ->
undo-ship) in a temp install that is NOT a git repo, under a PATH-shadowed `git` stub that records
any invocation. It is green today; it BITES if a future change makes the ship/undo path call git.

Standalone; run: python tools/tests/test_a12_non_git_vault.py"""
import json, os, sys, time, tempfile, shutil

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../engine/tools
sys.path.insert(0, HARNESS)
import queue_tx, ship, rewind

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ok  " if cond else " FAIL ") + name)

def _now(): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

KB = "dev"
KB_MAP = {"dev": "dev"}
CID = "a12-nogit-note"
CK = "dev/wiki/knowledge/a12-nogit-note.md"           # <kb>/wiki/... shaped (ship._resolve_facts)
DRAFT_REL = "dev/wiki/staging/a12-nogit-note.md"      # draft_path, relative to vault

_saved_path = os.environ.get("PATH")
_saved_marker = os.environ.get("GIT_STUB_MARKER")
d = tempfile.mkdtemp(prefix="a12_nogit_")
try:
    # ── a `git` stub on PATH that records any call and exits non-zero (the WIRED guard) ──
    gitdir = os.path.join(d, "gitstub"); os.makedirs(gitdir)
    marker = os.path.join(d, "git-was-called")
    if os.name == "nt":
        with open(os.path.join(gitdir, "git.cmd"), "w", encoding="ascii") as f:
            f.write("@echo off\r\n>>\"%GIT_STUB_MARKER%\" echo called\r\nexit /b 2\r\n")
    else:
        gp = os.path.join(gitdir, "git")
        with open(gp, "w", encoding="ascii") as f:
            f.write('#!/bin/sh\necho called >> "$GIT_STUB_MARKER"\nexit 2\n')
        os.chmod(gp, 0o755)
    os.environ["GIT_STUB_MARKER"] = marker
    _kept_path = (_saved_path or "").split(os.pathsep)
    if os.name == "nt":
        # WINDOWS FIX (verified on a native session with real git installed): CreateProcess's
        # bare-name resolution (what `subprocess.run(["git", ...])`, i.e. shell=False, uses) only
        # auto-appends ".exe" — it never considers our ".cmd" stub — so merely *prepending* gitdir
        # leaves a real git.exe elsewhere on PATH reachable, and the guard goes silently vacuous
        # for that call shape (confirmed empirically: prepend-only let a live
        # `subprocess.run(["git", "--version"])` regression pass through undetected). True
        # PATH-shadowing on Windows requires occluding any real git.exe directory, not just
        # out-prioritizing it.
        _kept_path = [p for p in _kept_path if p and not os.path.isfile(os.path.join(p, "git.exe"))]
    os.environ["PATH"] = gitdir + os.pathsep + os.pathsep.join(_kept_path)

    # ── a NON-GIT temp install ──
    install = os.path.join(d, "install")
    state, vault = os.path.join(install, "state"), os.path.join(install, "vault")
    revert_dir, raw_dir = os.path.join(state, "revert"), os.path.join(install, "raw", "inbox")
    for p in (state, vault, revert_dir, raw_dir):
        os.makedirs(p, exist_ok=True)
    live = os.path.join(state, "queue.json")
    items_file = live + ".items"
    check("setup: install has NO .git before the run",
          not os.path.exists(os.path.join(install, ".git")) and not os.path.exists(os.path.join(vault, ".git")))

    raw = os.path.join(raw_dir, CID + ".md")
    with open(raw, "w", encoding="utf-8") as f:
        f.write(f"# {CID}\n\nsynthetic non-git raw.\n")

    # ── STAGE 1: capture (add captured) ──
    cap = [{"id": CID, "source": "bookmark", "stage": "captured", "payload_path": raw,
            "captured_utc": _now(), "history": [{"ts": _now(), "stage": "captured"}]}]
    json.dump(cap, open(items_file, "w", encoding="utf-8"), indent=2)
    queue_tx._apply_items(live, queue_tx._read_items(items_file), "add")

    # ── STAGE 2: sort (assign kb/conflict_key/lane) ──
    by = {it["id"]: it for it in queue_tx.load(live)["queue"]}
    it = dict(by[CID]); it.update(stage="sorted", kb=KB, conflict_key=CK, lane="auto-ship")
    it["history"] = it.get("history", []) + [{"ts": _now(), "stage": "sorted"}]
    json.dump([it], open(items_file, "w", encoding="utf-8"), indent=2)
    queue_tx._apply_items(live, queue_tx._read_items(items_file), "update")

    # ── STAGE 3: ingest (write staging draft, advance to awaiting) ──
    sp = os.path.join(vault, DRAFT_REL.replace("/", os.sep)); os.makedirs(os.path.dirname(sp), exist_ok=True)
    with open(sp, "w", encoding="utf-8") as f:
        f.write("# a12-nogit-note\n\nDraft distilled from the non-git raw.\n")
    by = {it["id"]: it for it in queue_tx.load(live)["queue"]}
    it = dict(by[CID]); it.update(stage="awaiting", first_drafted_utc=_now(),
                                  recommended="approve", rec_reason="dev entity, reversible",
                                  draft_path=DRAFT_REL)
    it["history"] = it.get("history", []) + [{"ts": _now(), "stage": "awaiting"}]
    json.dump([it], open(items_file, "w", encoding="utf-8"), indent=2)
    queue_tx._apply_items(live, queue_tx._read_items(items_file), "update")

    # ── STAGE 4: gate SHIP — the real ship.py path ──
    ship.ship(live, vault, KB_MAP, CID, "auto-ship", revert_dir)
    target = os.path.join(vault, "dev", "wiki", "knowledge", "a12-nogit-note.md")
    pointer = os.path.join(revert_dir, CID + ".json")
    by = {it["id"]: it for it in queue_tx.load(live)["queue"]}
    check("ship: item advanced to 'shipped'", by[CID]["stage"] == "shipped")
    check("ship: canonical vault file written", os.path.isfile(target))
    check("ship: a file-based revert pointer exists (not a git commit)", os.path.isfile(pointer))
    check("ship: install still has NO .git after shipping",
          not os.path.exists(os.path.join(install, ".git")) and not os.path.exists(os.path.join(vault, ".git")))

    # ── UNDO — the real rewind.py path proves undo is file-based, not git ──
    rewind.undo_ship(live, CID, vault, revert_dir, to_stage="awaiting", kb_map=KB_MAP)
    by = {it["id"]: it for it in queue_tx.load(live)["queue"]}
    check("undo-ship: canonical vault file removed (file-based undo)", not os.path.exists(target))
    check("undo-ship: item back to 'awaiting'", by[CID]["stage"] == "awaiting")
    check("undo-ship: staging husk restored (re-shippable)", os.path.isfile(sp))

    # ── THE WIRED ASSERTION: no git was ever invoked across the whole chain ──
    check("guard: `git` was NEVER invoked by the engine (capture->gate->undo is git-free)",
          not os.path.exists(marker))
finally:
    if _saved_path is not None: os.environ["PATH"] = _saved_path
    if _saved_marker is None: os.environ.pop("GIT_STUB_MARKER", None)
    else: os.environ["GIT_STUB_MARKER"] = _saved_marker
    shutil.rmtree(d, ignore_errors=True)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
sys.exit(1 if FAIL else 0)
