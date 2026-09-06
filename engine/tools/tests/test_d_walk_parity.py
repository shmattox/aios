"""D Task 1 — the cockpit's walk_decision is a thin wrapper over the chat walk's CLI.

If this fails, the cockpit's walk button is NOT redundant and must not be removed: D's spec §3.3
assumes the walk survives ruling 4 because the same ledger is reachable from chat. These two tests
are the evidence for that assumption, so they gate Tasks 3-4 of
docs/superpowers/plans/2026-09-06-d-hosted-cockpit.md.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard_server import ACTIONS  # noqa: E402

TOOLS = Path(__file__).resolve().parents[1]


def test_walk_decision_shells_out_to_record_decision():
    """The cockpit adds nothing the CLI cannot be given directly."""
    validators, build = ACTIONS["walk_decision"]
    assert set(validators) == {"item_id", "station", "choice", "action"}
    argv = build(Path("/env"), TOOLS, {"item_id": "i1", "station": "kb",
                                       "choice": "claude", "action": "ship"})
    assert argv[1].endswith("brief_session.py")
    assert argv[2] == "record_decision"
    assert argv[4:] == ["i1", "kb", "claude", "ship"]


def test_cli_alone_writes_the_same_ledger_entry(tmp_path):
    """Driving record_decision directly — as a chat walk does — produces the ledger entry."""
    # `stations` must carry the station: record_decision raises "unknown station" otherwise
    # (brief_session.py:228). Shape mirrors the live ledger — station_order and stations are
    # settle/kb/system/personal/familyoffice/gm, each {status,items_total,decided,deferred}.
    state = tmp_path / "brief-session.json"
    state.write_text(json.dumps({
        "station_order": ["kb"], "current_station": "kb",
        "stations": {"kb": {"status": "pending", "items_total": 1,
                            "decided": 0, "deferred": 0}},
        "decisions": [], "deferrals": []}), encoding="utf-8")
    r = subprocess.run([sys.executable, str(TOOLS / "brief_session.py"),
                        "record_decision", str(state), "i1", "kb", "claude", "ship"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    d = json.loads(state.read_text(encoding="utf-8"))["decisions"]
    assert len(d) == 1
    assert d[0]["item_id"] == "i1" and d[0]["station"] == "kb"
    assert d[0]["choice"] == "claude" and d[0]["action"] == "ship"
