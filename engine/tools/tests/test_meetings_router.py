#!/usr/bin/env python3
"""Standalone checks for meetings_router.py. Run via suite_test.py (subprocess) or directly.
Ends with sys.exit(1) on any failure so pytest-through-suite_test stays green iff all pass."""
import json, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent / "meetings_router.py"
sys.path.insert(0, str(HERE.parent))
import meetings_router as mr  # noqa: E402

FAILS = []
def check(cond, msg):
    if not cond: FAILS.append(msg)

NOTE = """---
granola_id: abc-123
type: note
title: Deal Sync
folders:
  - Shared/Family Office
updated: 2026-07-09T10:00:00Z
---
body
"""
TRANSCRIPT = """---
granola_id: abc-123
type: transcript
title: Deal Sync
folders:
  - Shared/Family Office
---
transcript body
"""
NO_FOLDER = "---\ngranola_id: z-9\ntype: note\ntitle: Loose\n---\nbody\n"

# --- read_frontmatter ---
fm = mr.read_frontmatter(NOTE)
check(fm.get("granola_id") == "abc-123", "granola_id parse")
check(fm.get("type") == "note", "type parse")
check(fm.get("folders") == ["Shared/Family Office"], f"folders block-list parse: {fm.get('folders')!r}")
check(mr.read_frontmatter("no frontmatter here") == {}, "no-frontmatter -> {}")

# --- route_one: leaf match, default fallback, id_dest inheritance ---
MAP = {"Family Office": "familyoffice/meetings", "GM": "gm/meetings", "Personal": "personal/meetings",
       "Private Equity": "familyoffice/meetings/private-equity"}
check(mr.route_one(mr.read_frontmatter(NOTE), MAP, "personal/meetings") == "familyoffice/meetings", "leaf-match Family Office -> FO")
check(mr.route_one(mr.read_frontmatter(NO_FOLDER), MAP, "personal/meetings") == "personal/meetings", "no folder -> default")
check(mr.route_one({"folders": ["Shared/Unmapped Thing"]}, MAP, "personal/meetings") == "personal/meetings", "unmapped -> default")
check(mr.route_one({"granola_id": "p-1"}, MAP, "personal/meetings", id_dest={"p-1": "familyoffice/meetings/private-equity"}) == "familyoffice/meetings/private-equity", "no own folder -> inherit from id_dest")
check(mr.route_one({"folders": ["Shared/GM"], "granola_id": "p-1"}, MAP, "personal/meetings", id_dest={"p-1": "familyoffice/meetings/private-equity"}) == "gm/meetings", "own mapped folder wins over id_dest inheritance")

def run(args):
    r = subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr

# --- end-to-end move: note+transcript co-locate; source drained; idempotent ---
with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    dz = root / "drop"; (dz / "2026" / "07").mkdir(parents=True)
    (dz / "2026" / "07" / "Deal Sync.md").write_text(NOTE, encoding="utf-8")
    (dz / "2026" / "07" / "Deal Sync-transcript.md").write_text(TRANSCRIPT, encoding="utf-8")
    (dz / "2026" / "07" / "Loose.md").write_text(NO_FOLDER, encoding="utf-8")
    dest = root / "domains"; logs = root / "logs"
    code, out = run(["--drop-zone", str(dz), "--dest-root", str(dest),
                     "--map", json.dumps(MAP), "--default", "personal/meetings", "--log-dir", str(logs)])
    check(code == 0, f"router exit 0 (got {code}): {out}")
    fo = dest / "familyoffice/meetings/2026/07"
    check((fo / "Deal Sync.md").is_file(), "note routed to FO")
    check((fo / "Deal Sync-transcript.md").is_file(), "transcript co-located in FO")
    check((dest / "personal/meetings/2026/07/Loose.md").is_file(), "no-folder note -> personal default")
    check(not (dz / "2026" / "07" / "Deal Sync.md").exists(), "source drained after move")
    check((fo / "Deal Sync.md").read_text(encoding="utf-8") == NOTE, "content byte-preserved")
    # idempotent re-run over an empty drop-zone: exit 0, no error, no dup
    code2, out2 = run(["--drop-zone", str(dz), "--dest-root", str(dest),
                       "--map", json.dumps(MAP), "--default", "personal/meetings", "--log-dir", str(logs)])
    check(code2 == 0, f"empty re-run exit 0: {out2}")
    # re-drop the same note -> overwrites same dest path, no duplicate filename
    (dz / "2026" / "07").mkdir(parents=True, exist_ok=True)
    (dz / "2026" / "07" / "Deal Sync.md").write_text(NOTE, encoding="utf-8")
    run(["--drop-zone", str(dz), "--dest-root", str(dest), "--map", json.dumps(MAP),
         "--default", "personal/meetings", "--log-dir", str(logs)])
    check(len(list((fo).glob("Deal Sync.md"))) == 1, "re-drop overwrites, no duplicate")

# --- transcript-without-folders inherits its note's dest via shared granola_id ---
PE_NOTE = """---
granola_id: p-1
type: note
title: Alex__Seth
folders:
  - Shared/Private Equity
---
body
"""
PE_TRANSCRIPT = """---
granola_id: p-1
type: transcript
note: Alex__Seth
---
transcript body
"""
with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    dz = root / "drop"; (dz / "2026" / "07").mkdir(parents=True)
    (dz / "2026" / "07" / "Alex__Seth.md").write_text(PE_NOTE, encoding="utf-8")
    (dz / "2026" / "07" / "Alex__Seth-transcript.md").write_text(PE_TRANSCRIPT, encoding="utf-8")
    dest = root / "domains"; logs = root / "logs"
    code, out = run(["--drop-zone", str(dz), "--dest-root", str(dest),
                     "--map", json.dumps(MAP), "--default", "personal/meetings", "--log-dir", str(logs)])
    check(code == 0, f"pairing-test router exit 0 (got {code}): {out}")
    pe = dest / "familyoffice/meetings/private-equity/2026/07"
    check((pe / "Alex__Seth.md").is_file(), "PE note routed to familyoffice/meetings/private-equity")
    check((pe / "Alex__Seth-transcript.md").is_file(), "folder-less transcript inherits note's dest via granola_id")
    check(not (dest / "personal/meetings/2026/07/Alex__Seth-transcript.md").exists(), "transcript did NOT fall through to personal default")
    check(not (dest / "personal").exists() or not list((dest / "personal").rglob("Alex__Seth*")), "no stray copies under personal for this pair")

# --- usage error ---
code, _ = run(["--drop-zone", "x"])
check(code == 2, "missing required args -> exit 2")

# --- shell resolves argv from a fixture profile ---
import os
SHELL = HERE.parent / "meetings_router_task.py"
with tempfile.TemporaryDirectory() as d:
    er = Path(d)
    (er / "profile").mkdir()
    (er / "profile" / "domains.yaml").write_text(
        "meetings:\n"
        "  drop_zone: SecondBrain/00_Inbox/meetings\n"
        "  default: personal/meetings\n"
        "  folder_map:\n"
        "    Family Office: familyoffice/meetings\n"
        "    GM: gm/meetings\n"
        "    Personal: personal/meetings\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(SHELL), "--env-root", str(er), "--print-argv"],
                       capture_output=True, text=True)
    argv_line = r.stdout.strip()
    check(r.returncode == 0, f"shell --print-argv exit 0: {r.stdout+r.stderr}")
    check("--drop-zone" in argv_line and "SecondBrain/00_Inbox/meetings".replace("/", os.sep) in argv_line.replace("/", os.sep), "shell resolves drop-zone under env-root")
    check("--dest-root" in argv_line and ("state" + os.sep + "domains") in argv_line.replace("/", os.sep), "shell resolves dest-root to env-root/state/domains")
    check('"Family Office": "familyoffice/meetings"' in argv_line or '"Family Office":"familyoffice/meetings"' in argv_line, f"shell passes folder_map as json: {argv_line}")
    check("--default" in argv_line and "personal/meetings" in argv_line.replace(os.sep, "/"), "shell passes default")

if FAILS:
    print("FAIL:"); [print("  -", m) for m in FAILS]; sys.exit(1)
print("ok - all meetings_router checks passed"); sys.exit(0)
