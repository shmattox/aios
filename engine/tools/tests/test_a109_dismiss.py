"""A109 task 3 — queue_tx dismiss → reference stage."""
import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]


def _mk_queue(tmp_path, stage="captured"):
    q = {"queue": [{"id": "it1", "kind": "capture", "stage": stage,
                    "conflict_key": "personal/wiki/knowledge/x", "history": []}]}
    p = tmp_path / "queue.json"
    p.write_text(json.dumps(q), encoding="utf-8")
    return p


def _run(*argv):
    return subprocess.run([sys.executable, str(TOOLS / "queue_tx.py"), *argv],
                          capture_output=True, text=True)


def test_dismiss_routes_to_reference(tmp_path):
    p = _mk_queue(tmp_path)
    r = _run("dismiss", str(p), "it1", "--reason", "below worthiness bar", "--by", "dashboard")
    assert r.returncode == 0, r.stderr
    item = json.loads(p.read_text(encoding="utf-8"))["queue"][0]
    assert item["stage"] == "reference"
    assert item["history"][-1]["op"] == "dismiss"
    assert item["history"][-1]["reason"] == "below worthiness bar"


def test_dismiss_unknown_id_fails(tmp_path):
    p = _mk_queue(tmp_path)
    r = _run("dismiss", str(p), "nope", "--reason", "x", "--by", "dashboard")
    assert r.returncode == 1


def test_dismiss_shipped_item_refused(tmp_path):
    p = _mk_queue(tmp_path, stage="shipped")
    r = _run("dismiss", str(p), "it1", "--reason", "x", "--by", "dashboard")
    assert r.returncode == 1
