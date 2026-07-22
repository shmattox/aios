"""A109 task 4 — one reply-op shape on the session ledger."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import brief_session  # noqa: E402


@pytest.fixture()
def ledger(tmp_path):
    p = tmp_path / "brief-session.json"
    # minimal valid ledger: create via the module's own entry point so the shape stays true
    subprocess.run([sys.executable, str(TOOLS / "brief_session.py"),
                    "new_walk", str(p), "w1"], check=True, capture_output=True)
    return p


@pytest.mark.parametrize("kind", ["respond", "append", "comment"])
def test_record_reply_kinds(ledger, kind):
    out = brief_session.record_reply(str(ledger), "FX-88", kind, "hold until Monday")
    rep = out["replies"][-1]
    assert rep["target_id"] == "FX-88" and rep["reply_kind"] == kind
    assert rep["consumed"] is False and rep["text"] == "hold until Monday"


def test_record_reply_bad_kind_raises(ledger):
    with pytest.raises(ValueError):
        brief_session.record_reply(str(ledger), "FX-88", "shout", "x")


def test_record_reply_cli(ledger):
    r = subprocess.run([sys.executable, str(TOOLS / "brief_session.py"), "record_reply",
                        str(ledger), "FX-88", "append", "use the smaller batch"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    data = json.loads(ledger.read_text(encoding="utf-8"))
    assert data["replies"][-1]["reply_kind"] == "append"
