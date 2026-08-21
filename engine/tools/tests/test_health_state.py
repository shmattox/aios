import json, os, time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))
import health_state


def _env(tmp_path, results=None):
    (tmp_path / "state" / "standing-checks").mkdir(parents=True)
    (tmp_path / "state" / "factory").mkdir(parents=True)
    (tmp_path / "state" / "task-logs" / "aios-ingest").mkdir(parents=True)
    (tmp_path / "state" / "task-logs" / "aios-ingest" / "last-run.log").write_text("ok", encoding="utf-8")
    if results is not None:
        (tmp_path / "state" / "standing-checks" / "results.json").write_text(
            json.dumps(results), encoding="utf-8")
    return str(tmp_path)


RESULTS = {"generated_utc": "2026-08-21T10:56:16+00:00", "watching_clear": [], "findings": [],
           "checks": [
               {"id": "a", "kind": "standing", "cadence": "daily", "origin": "H1",
                "on_violation": "fix a", "last_run": "x", "first_red": "2026-08-19", "reason": "boom", "status": "red"},
               {"id": "b", "kind": "standing", "cadence": "daily", "origin": "H2",
                "on_violation": "", "last_run": "x", "first_red": None, "reason": None, "status": "green"},
               {"id": "c", "kind": "watch", "cadence": "daily", "origin": "H3",
                "on_violation": "", "last_run": "x", "first_red": None, "reason": "expired", "status": "observed"},
           ]}


def test_summary_counts_and_checks(tmp_path):
    s = health_state.summary(_env(tmp_path, RESULTS))
    assert s["ok"] is True
    assert s["standing"]["reds"] == 1 and s["standing"]["greens"] == 1
    assert s["standing"]["watch_expired"] == 1
    red = [c for c in s["standing"]["checks"] if c["status"] == "red"][0]
    assert red["id"] == "a" and red["on_violation"] == "fix a"


def test_fleet_and_sources(tmp_path):
    s = health_state.summary(_env(tmp_path, RESULTS))
    fleet = {f["task"]: f for f in s["fleet"]}
    assert "aios-ingest" in fleet and fleet["aios-ingest"]["age_s"] >= 0
    names = {r["name"] for r in s["sources"]}
    assert {"brief-cache", "gate-metrics", "standing-checks"} <= names
    missing = [r for r in s["sources"] if r["name"] == "queue"][0]
    assert missing["age_s"] is None            # absent file keeps its row, age null


def test_fail_open_on_unreadable_results(tmp_path):
    env = _env(tmp_path, None)
    (tmp_path / "state" / "standing-checks" / "results.json").write_text("{not json", encoding="utf-8")
    s = health_state.summary(env)
    assert s["ok"] is False
    assert any(c["id"] == "standing-checks-unreadable" and c["status"] == "red"
               for c in s["standing"]["checks"])
    assert s["standing"]["reds"] >= 1          # the synthetic red is counted


def test_missing_results_is_also_a_visible_red(tmp_path):
    s = health_state.summary(_env(tmp_path, None))
    assert s["ok"] is False
    assert any(c["id"] == "standing-checks-unreadable" for c in s["standing"]["checks"])
