#!/usr/bin/env python3
r"""A19 — live-vault resolution for the orphan sweep + rewind.reconcile.

Two tools resolved vault paths by KB SHORT NAME (`<vault>/personal/wiki/...`), which is only the
TEST-vault layout. The REAL vault maps kb -> folder via the profile's `vault.live_kb_map`
(`personal` -> `01_Personal`), so against the live vault:
  - `rewind._reconcile_scan` false-positived EVERY legacy awaiting/shipped item (draft/file exists
    at the mapped folder, scan looked at the short name) — a live `reconcile --apply` would have
    bounced healthy items (the A14 finding);
  - `garden_sweep.py` only swept `<install>/vault/`, so real-vault orphan staging drafts were
    unreachable (dormant since 07-01).
Also: `_present`/`_file_present` read via plain open(), which on Windows fails for >260-char
paths (ERROR 206/3) — a healthy long-path draft read as ABSENT.

The fix under test:
  - both tools accept a kb->folder map (`--kb-map` JSON / kwarg) and resolve short-name joins
    through it; `draft_path` (already real-folder vault-relative per A13) is honored as-is;
  - `garden_sweep` accepts `--vault-root` (defaults to the old `<install>/vault`), and with a map
    sweeps ONLY mapped folders (an unmapped folder is skipped, never treated as orphan);
  - file-presence checks go through a Windows long-path shim (`\\?\` prefix past ~250 chars).

Hermetic: everything under tempfile.mkdtemp(); the live install is never touched.
Run: python engine/tools/tests/test_a19_live_vault.py
"""
import os, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)
import rewind
import garden_sweep

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok   {name}")
    else:
        FAIL += 1; print(f"  FAIL {name}")


KB_MAP = {"personal": "01_Personal", "familyoffice": "02_FamilyOffice", "dev": "03_Dev"}


def _mkfile(path, body="x\n"):
    """Create a file, long-path-safe on Windows (the test itself must be able to build >260-char
    fixtures, so it uses the same prefix trick the shim under test applies)."""
    ap = os.path.abspath(path)
    if os.name == "nt" and len(ap) > 240 and not ap.startswith("\\\\?\\"):
        ap = "\\\\?\\" + ap
    os.makedirs(os.path.dirname(ap), exist_ok=True)
    with open(ap, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    return path


def item(iid, stage, kb, ck, **kw):
    it = {"id": iid, "stage": stage, "kb": kb, "conflict_key": ck}
    it.update(kw)
    return it


def main():
    root = tempfile.mkdtemp(prefix="aios-a19-")
    try:
        vault = os.path.join(root, "SecondBrain")   # live-vault layout (mapped folders)

        # ---- rewind._reconcile_scan — kb-map resolution -------------------------
        _mkfile(os.path.join(vault, "01_Personal", "wiki", "staging", "note-a.md"))
        _mkfile(os.path.join(vault, "01_Personal", "wiki", "journal", "2026-05-01.md"))
        _mkfile(os.path.join(vault, "03_Dev", "wiki", "staging", "custom-name.md"))
        d = {"queue": [
            # healthy awaiting: draft exists at the MAPPED folder (fallback join must map kb)
            item("aw1", "awaiting", "personal", "personal/wiki/staging/note-a.md"),
            # healthy awaiting with an explicit real-folder draft_path (A13 form) — honored as-is
            item("aw2", "awaiting", "dev", "dev/wiki/staging/whatever.md",
                 draft_path="03_Dev/wiki/staging/custom-name.md"),
            # declared draftless (garden action-proposal) — always skipped
            item("aw3", "awaiting", "personal", "personal/wiki/staging/none.md", draftless=True),
            # genuinely missing draft — must STILL be flagged even with the map
            item("aw4", "awaiting", "personal", "personal/wiki/staging/gone.md"),
            # healthy shipped: vault file exists at the mapped conflict_key path
            item("sh1", "shipped", "personal", "personal/wiki/journal/2026-05-01.md"),
            # genuinely missing shipped file — flagged with or without the map
            item("sh2", "shipped", "personal", "personal/wiki/journal/void.md"),
            # legacy EXTENSIONLESS conflict_key — the note exists as <ck>.md; must not be flagged
            item("sh3", "shipped", "personal", "personal/wiki/journal/2026-05-01"),
        ]}

        # WITHOUT the map (legacy behavior): healthy live-vault items false-positive
        sm0, shm0 = rewind._reconcile_scan(d, "unused", vault)
        check("scan(no map): live-vault awaiting false-positives (the A14 bug, documented)",
              "aw1" in sm0 and "sh1" in shm0)

        # WITH the map: only the real desyncs are flagged
        sm, shm = rewind._reconcile_scan(d, "unused", vault, kb_map=KB_MAP)
        check("scan(map): healthy awaiting draft at mapped folder NOT flagged", "aw1" not in sm)
        check("scan(map): explicit draft_path honored as-is", "aw2" not in sm)
        check("scan(map): draftless item skipped", "aw3" not in sm)
        check("scan(map): genuinely missing draft still flagged", "aw4" in sm)
        check("scan(map): healthy shipped file at mapped path NOT flagged", "sh1" not in shm)
        check("scan(map): genuinely missing shipped file still flagged", "sh2" in shm)
        check("scan(map): legacy extensionless ck tolerated (.md appended)", "sh3" not in shm)

        # ---- long-path presence (the _file_present >260-char miss) --------------
        deep = os.path.join(vault, "01_Personal", "wiki", "staging",
                            "x" * 80, "y" * 80, "z" * 80)
        longp = os.path.join(deep, "long-draft.md")
        _mkfile(longp)
        check("fixture: long path really is past the classic limit", len(os.path.abspath(longp)) > 260)
        check("_file_present: reads a >260-char path (long-path shim)", rewind._file_present(longp))
        d2 = {"queue": [item("awL", "awaiting", "personal",
                             "personal/wiki/staging/long-draft.md",
                             draft_path="01_Personal/wiki/staging/" + "x" * 80 + "/" + "y" * 80
                                        + "/" + "z" * 80 + "/long-draft.md")]}
        smL, _ = rewind._reconcile_scan(d2, "unused", vault, kb_map=KB_MAP)
        check("scan(map): healthy >260-char draft NOT flagged", "awL" not in smL)

        # ---- garden_sweep — --vault-root + kb-map on the live layout ------------
        install = os.path.join(root, "install")
        os.makedirs(os.path.join(install, "state", "queue.json.d"))
        os.makedirs(os.path.join(install, "vault"))
        _mkfile(os.path.join(vault, "01_Personal", "wiki", "staging", "orphan-shipped.md"))
        _mkfile(os.path.join(vault, "01_Personal", "wiki", "staging", "orphan-absent.md"))
        _mkfile(os.path.join(vault, "01_Personal", "wiki", "staging", "live-work.md"))
        _mkfile(os.path.join(vault, "03_Dev", "wiki", "staging", "odd-name.md"))
        _mkfile(os.path.join(vault, "00_Inbox", "wiki", "staging", "unmapped.md"))
        _mkfile(os.path.join(vault, "01_Personal", "wiki", "staging", "README.md"))  # folder doc
        _mkfile(os.path.join(vault, "01_Personal", "wiki", "staging", "collide.md"))  # live, collides
        sweep_q = [
            item("g1", "shipped", "personal", "personal/wiki/staging/orphan-shipped.md"),
            item("g2", "awaiting", "personal", "personal/wiki/staging/live-work.md"),
            # awaiting item whose DRAFT NAME diverges from its conflict_key slug — must be
            # protected via its draft_path, not orphaned by the (kb, ck-slug) index alone
            item("g3", "awaiting", "dev", "dev/wiki/journal/2026-06-01.md",
                 draft_path="03_Dev/wiki/staging/odd-name.md"),
            # SLUG COLLISION: a DONE item shares g5's (kb, slug) — the live item's draft_path
            # pointer must win, or the collision deletes live work (review IMPORTANT-1)
            item("g4", "shipped", "personal", "personal/wiki/knowledge/collide.md"),
            item("g5", "awaiting", "personal", "personal/wiki/journal/collide.md",
                 draft_path="01_Personal/wiki/staging/collide.md"),
        ]
        real_load = garden_sweep._load_queue_clean
        garden_sweep._load_queue_clean = lambda qp: (sweep_q, True)
        try:
            backups, orphans, evidence = garden_sweep.sweep(
                install, ttl_days=7, apply=False, vault_root=vault, kb_map=KB_MAP)
            names = {os.path.basename(p) for p, st in orphans}
            check("sweep(dry): shipped item's leftover draft flagged orphan", "orphan-shipped.md" in names)
            check("sweep(dry): absent item's stray draft flagged orphan", "orphan-absent.md" in names)
            check("sweep(dry): awaiting (live) draft KEPT", "live-work.md" not in names)
            check("sweep(dry): divergent-name draft protected via draft_path", "odd-name.md" not in names)
            check("sweep(dry): unmapped folder SKIPPED (never orphaned)", "unmapped.md" not in names)
            check("sweep(dry): staging README.md excluded (folder doc, not a draft)",
                  "README.md" not in names)
            check("sweep(dry): slug-collision live draft KEPT (draft_path beats a same-slug done item)",
                  "collide.md" not in names)
            # --vault-root WITHOUT --kb-map: a real vault with no map must REFUSE the orphan
            # sweep (folders can't match kb short names — everything would read absent)
            _, orphans_nomap, _ = garden_sweep.sweep(install, ttl_days=7, apply=False, vault_root=vault)
            check("sweep(vault-root, NO map): orphan sweep refused (nothing flagged)",
                  orphans_nomap == [])
            check("sweep(dry): nothing deleted",
                  os.path.exists(os.path.join(vault, "01_Personal", "wiki", "staging", "orphan-shipped.md")))

            garden_sweep.sweep(install, ttl_days=7, apply=True, vault_root=vault, kb_map=KB_MAP)
            check("sweep(apply): orphan deleted",
                  not os.path.exists(os.path.join(vault, "01_Personal", "wiki", "staging", "orphan-shipped.md")))
            check("sweep(apply): live draft survives",
                  os.path.exists(os.path.join(vault, "01_Personal", "wiki", "staging", "live-work.md")))
            check("sweep(apply): unmapped folder untouched",
                  os.path.exists(os.path.join(vault, "00_Inbox", "wiki", "staging", "unmapped.md")))

            # default path (no vault_root): old test-vault behavior unchanged (folder == kb)
            _mkfile(os.path.join(install, "vault", "personal", "wiki", "staging", "tv-orphan.md"))
            _, orphans2, _ = garden_sweep.sweep(install, ttl_days=7, apply=False)
            check("sweep(default): test-vault layout still works (folder == kb identity)",
                  {os.path.basename(p) for p, st in orphans2} == {"tv-orphan.md"})
        finally:
            garden_sweep._load_queue_clean = real_load

        print(f"\n{PASS} passed, {FAIL} failed")
        sys.exit(1 if FAIL else 0)
    finally:
        # long paths need the prefix for rmtree on Windows too
        rt = os.path.abspath(root)
        if os.name == "nt" and not rt.startswith("\\\\?\\"):
            rt = "\\\\?\\" + rt
        shutil.rmtree(rt, ignore_errors=True)


if __name__ == "__main__":
    main()
