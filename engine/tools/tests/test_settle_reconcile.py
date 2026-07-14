import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import settle_reconcile as sr  # noqa: E402

DECISIONS = [
    {"item_id": "OI-901", "title": "Pay tax", "executed": True,
     "notion_write": {"page_id": "p1", "field": "Status", "to": "Done"}, "ts": "2026-07-07T10:00:00Z"},
    {"item_id": "OI-902", "title": "Landed one", "executed": True,
     "notion_write": {"page_id": "p2", "field": "Status", "to": "Done"}, "ts": "2026-07-07T10:00:00Z"},
    {"item_id": "OI-903", "title": "No notion intent", "executed": True, "ts": "2026-07-07T10:00:00Z"},
    {"item_id": "OI-904", "title": "Not executed", "executed": False,
     "notion_write": {"page_id": "p4", "field": "Status", "to": "Done"}, "ts": "2026-07-07T10:00:00Z"},
]
CHANGELOG = [
    {"page_id": "p2", "field": "Status", "new": "Done", "ts": "2026-07-07T10:00:05Z"},  # OI-902 landed
]

def test_finds_only_unlanded_intended_executed_writes():
    out = sr.find_unlanded_writes(DECISIONS, CHANGELOG)
    ids = {r["item_id"] for r in out}
    assert ids == {"OI-901"}                 # OI-902 landed; OI-903 no intent; OI-904 not executed
    assert out[0] == {"item_id": "OI-901", "title": "Pay tax",
                      "page_id": "p1", "field": "Status", "to": "Done"}

def test_landed_requires_matching_value():
    cl = [{"page_id": "p1", "field": "Status", "new": "In Progress", "ts": "2026-07-07T11:00:00Z"}]
    out = sr.find_unlanded_writes([DECISIONS[0]], cl)   # wrote a DIFFERENT value -> still unlanded
    assert [r["item_id"] for r in out] == ["OI-901"]

def test_run_dry_run_offline(tmp_path):
    import os, json
    state = tmp_path / "state"; state.mkdir()
    (state / "brief-session.json").write_text(json.dumps({"decisions": DECISIONS}), encoding="utf-8")
    (state / "notion-changelog.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in CHANGELOG), encoding="utf-8")
    res = sr.run(str(tmp_path), dry_run=True)
    assert res["unlanded_found"] == 1
    assert res["auto_healed"][0]["item_id"] == "OI-901"
    assert res["auto_healed"][0]["dry_run"] is True


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-q"]))
