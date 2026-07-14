#!/usr/bin/env python3
"""pipeline_health.py test harness — the one-line overnight-pipeline renderer for the
brief headline. Scratch; safe to delete. Run: python tools/tests/test_pipeline_health.py"""
import os, sys, tempfile, shutil

for _s in (sys.stdout, sys.stderr):  # rendered lines carry emoji; piped Windows stdout is cp1252
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

HARNESS_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../engine/tools
sys.path.insert(0, HARNESS_TOOLS)
import pipeline_health

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ok  " if cond else " FAIL ") + name)

NOW = "2026-07-05T12:00:00Z"

d = tempfile.mkdtemp(prefix="pipehealth_")
try:
    p = os.path.join(d, "context-log.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        f.write(
            # in-window runs (NOW - 30h reaches back to 2026-07-04T06:00Z)
            '{"ts": "2026-07-05T05:20:00Z", "stage": "capture-router", "run_id": "2026-07-05", "repairs": [], "anomalies": []}\n'
            '{"ts": "2026-07-05T06:10:00Z", "stage": "ingest", "run_id": "2026-07-05", "drafted": 4, "repairs": [], "anomalies": ["one malformed raw skipped"]}\n'
            '{"ts": "2026-07-05T08:03:00Z", "stage": "gate-auto", "run_id": "2026-07-05", "shipped": 1, "held": 57, "rejected": 0, "anomalies": []}\n'
            'NOT JSON — torn line from the 07-04 clobber\n'
            '{"ts": "2026-07-05T10:54:00Z", "stage": "brief", "run_id": "2026-07-05", "anomalies": ["notion-mcp permission-blocked", "staging draft missing on disk"]}\n'
            # out-of-window run (must be excluded)
            '{"ts": "2026-07-03T08:00:00Z", "stage": "gate-auto", "run_id": "2026-07-03", "shipped": 9, "held": 99, "anomalies": ["old"]}\n'
        )

    line = pipeline_health.render(p, hours=30, now=NOW)
    print("  rendered: " + line)
    check("single line (no newlines)", "\n" not in line)
    check("counts 4 in-window runs", "4 runs" in line)
    check("counts 4 distinct stages", "4 stages" in line)
    check("sums shipped across window (1, not 10)", "shipped 1" in line and "shipped 10" not in line)
    check("held is the latest snapshot (57, not 99/156)", "57" in line and "99" not in line and "156" not in line)
    check("counts 3 anomalies", "3 anomalies" in line)
    check("names the anomalous stages", "brief" in line and "ingest" in line)
    check("old run excluded (no shipped 9 / old anomaly)", "old" not in line)

    # clean window → explicit all-clear, not silence
    p2 = os.path.join(d, "clean.jsonl")
    with open(p2, "w", encoding="utf-8") as f:
        f.write('{"ts": "2026-07-05T05:00:00Z", "stage": "ingest", "run_id": "2026-07-05", "anomalies": []}\n')
    line2 = pipeline_health.render(p2, hours=30, now=NOW)
    print("  rendered: " + line2)
    check("clean window says no anomalies", "no anomalies" in line2)

    # empty / missing log → a line, never a crash (the brief must always paint)
    line3 = pipeline_health.render(os.path.join(d, "absent.jsonl"), hours=30, now=NOW)
    print("  rendered: " + line3)
    check("missing log renders a no-runs line", "no runs" in line3)

    # hostile lines — parses-but-wrong-shape JSON, naive ts, string counts (torn-line history
    # + stages improvising log writes): tolerate, never crash (review finding 2026-07-05)
    p4 = os.path.join(d, "hostile.jsonl")
    with open(p4, "w", encoding="utf-8") as f:
        f.write(
            'null\n'
            '123\n'
            '[1, 2]\n'
            '"just a string"\n'
            '{"ts": "2026-07-05T05:00:00", "stage": "ingest", "anomalies": []}\n'          # naive ts
            '{"ts": "2026-07-05T06:00:00Z", "stage": "gate-auto", "shipped": "3", "held": 12, "anomalies": []}\n'  # string count
        )
    line4 = pipeline_health.render(p4, hours=30, now=NOW)
    print("  rendered: " + line4)
    check("non-dict JSON lines tolerated", "runs" in line4)
    check("naive-ts record counted as UTC (2 runs)", "2 runs" in line4)
    check("string shipped coerced (shipped 3)", "shipped 3" in line4)
    check("naive --now tolerated", "no runs" in pipeline_health.render(
        os.path.join(d, "absent.jsonl"), hours=30, now="2026-07-05T12:00:00"))

    # CLI shape: prints the line, exit 0
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(HARNESS_TOOLS, "pipeline_health.py"),
                        "--path", p, "--hours", "30", "--now", NOW],
                       capture_output=True, text=True, encoding="utf-8")
    check("CLI exit 0", r.returncode == 0)
    check("CLI prints the same line", r.stdout.strip() == line)
finally:
    shutil.rmtree(d, ignore_errors=True)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
