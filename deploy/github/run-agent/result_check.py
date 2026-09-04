"""Validate a leg's result.json and derive the job outcome (sub-project A, spec §3.1).

Exit 0 only when the file exists, parses, names this leg, and status is ok|degraded. A missing
file is a FAILURE — exit 0 must never mask "the run did nothing".
"""
import argparse
import json
import os
import sys

OK_STATUSES = ("ok", "degraded")
ALL_STATUSES = ("ok", "degraded", "failed")


def check(result_path, leg):
    if not os.path.isfile(result_path):
        return 1, "missing", "### aios `%s` — FAILED: no result file at `%s`\n" % (leg, result_path)
    try:
        with open(result_path, encoding="utf-8") as f:
            r = json.load(f)
    except (ValueError, OSError) as e:
        return 1, "invalid", "### aios `%s` — FAILED: result.json unreadable (%s)\n" % (leg, e)
    if not isinstance(r, dict) or r.get("leg") != leg or r.get("status") not in ALL_STATUSES:
        return 1, "invalid", ("### aios `%s` — FAILED: result.json invalid (leg=%r status=%r)\n"
                              % (leg, r.get("leg") if isinstance(r, dict) else None,
                                 r.get("status") if isinstance(r, dict) else None))
    status = r["status"]
    verify = r.get("verify") or {}
    if "verify" in r and not isinstance(verify, dict):
        return 1, "invalid", "### aios `%s` — FAILED: result.json invalid (verify must be a dict, got %r)\n" % (leg, type(verify).__name__)
    summary = r.get("summary", "")
    if "summary" in r and not isinstance(summary, str):
        return 1, "invalid", "### aios `%s` — FAILED: result.json invalid (summary must be a string, got %r)\n" % (leg, type(summary).__name__)
    md = ("### aios `%s` — %s (run %s)\n\n%s\n\n_VERIFY passed=%s — %s_\n"
          % (leg, status.upper(), r.get("run_id", "?"), summary.strip(),
             verify.get("passed"), verify.get("notes", "")))
    return (0 if status in OK_STATUSES else 1), status, md


def _append(env_var, text):
    path = os.environ.get(env_var)
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)


def main(argv=None):
    p = argparse.ArgumentParser(description="aios GitHub-substrate result check")
    p.add_argument("--leg", required=True)
    p.add_argument("--result", required=True)
    a = p.parse_args(argv)
    code, status, md = check(a.result, a.leg)
    _append("GITHUB_OUTPUT", "status=%s\n" % status)
    _append("GITHUB_STEP_SUMMARY", md)
    sys.stdout.write(md)
    return code


if __name__ == "__main__":
    sys.exit(main())
