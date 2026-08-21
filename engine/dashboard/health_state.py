"""health_state — read-only health aggregation for the dashboard.

Builds one payload from files the engine already writes: standing-check
results (A94 runner), the scheduled-task fleet's last-run ages, and the
age of each key state source. ADVISORY surface: fails OPEN — an unreadable
results.json becomes a synthetic red check, never a hidden panel.
Never writes anything.
"""
import json, os, time
from pathlib import Path

_SOURCES = [
    ("brief-cache",     "state/brief-cache.json"),
    ("standup",         "state/factory/standup.json"),
    ("gate-metrics",    "state/factory/gate-metrics.json"),
    ("queue",           "state/queue.json"),
    ("standing-checks", "state/standing-checks/results.json"),
]

_CHECK_FIELDS = ("id", "kind", "cadence", "origin", "on_violation",
                 "first_red", "reason", "status")


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return None


def _age(m, now):
    return max(0, int(now - m)) if m is not None else None


def summary(env_root):
    env = Path(env_root)
    now = time.time()
    ok, generated, checks = True, None, []
    rp = env / "state" / "standing-checks" / "results.json"
    try:
        data = json.loads(rp.read_text(encoding="utf-8"))
        generated = data.get("generated_utc")
        for c in data.get("checks", []):
            checks.append({k: c.get(k) for k in _CHECK_FIELDS})
    except (OSError, ValueError) as e:
        ok = False
        checks.append({"id": "standing-checks-unreadable", "kind": "standing",
                       "cadence": None, "origin": "health_state fail-open",
                       "on_violation": "standing-check results could not be read - run standing_checks.py / check the nightly gather",
                       "first_red": None, "reason": "%s: %s" % (type(e).__name__, rp),
                       "status": "red"})
    reds = sum(1 for c in checks if c["status"] == "red")
    greens = sum(1 for c in checks if c["status"] in ("green", "observed"))
    watch_expired = sum(1 for c in checks if c["status"] == "expired")

    fleet = []
    logs = env / "state" / "task-logs"
    if logs.is_dir():
        for d in sorted(p for p in logs.iterdir() if p.is_dir()):
            m = max((x for x in (_mtime(d / "last-run.log"),
                                 _mtime(d / "last-result.txt")) if x), default=None)
            fleet.append({"task": d.name, "last_run_epoch": m, "age_s": _age(m, now)})

    sources = []
    for name, rel in _SOURCES:
        sources.append({"name": name, "path": rel, "age_s": _age(_mtime(env / rel), now)})
    spends = sorted((env / "state" / "factory").glob("spend-*.json"))
    sources.append({"name": "spend", "path": "state/factory/spend-*.json",
                    "age_s": _age(_mtime(spends[-1]) if spends else None, now)})

    return {"ok": ok, "generated_utc": generated,
            "standing": {"reds": reds, "greens": greens,
                          "watch_expired": watch_expired, "checks": checks},
            "fleet": fleet, "sources": sources}
