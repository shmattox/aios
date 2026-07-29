# sanitize:allow-file — fixtures use synthetic/out-of-range ids by design (A79)
"""brief_refs — structured drill-down refs on brief items (A109 refs-at-source)."""
import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import brief_refs  # noqa: E402


def test_act_refs_from_thread_and_citation():
    item = {"id": "OI-901", "thread_id": "acme-bridge-hedge",
            "system_voice": {"grade": "2a", "text": "…",
                             "cite": "collection://11111111-1111-1111-1111 task X; "
                                     "collection://22222222-2222-2222-2222 decision Y; "
                                     "state/threads/acme-bridge-hedge.md"}}
    refs = brief_refs.build_refs(item)
    labels = [r["label"] for r in refs]
    assert "thread · acme-bridge-hedge" in labels
    assert "state/threads/acme-bridge-hedge.md" in labels
    assert sum(1 for r in refs if r["tag"] == "notion") == 2
    for r in refs:                       # every ref is actionable
        assert r.get("route") or r.get("open") or r.get("mock")


def test_held_refs_from_canonical_fields():
    item = {"id": "OI-905", "kb": "familyoffice",
            "state_path": "state/domains/familyoffice/tables/liabilities/bayview-note.md",
            "papered_source": "Family Office/raw/Bayview Loan Modification (executed).pdf",
            "draft_path": "02_FamilyOffice/wiki/staging/bayview.md"}
    refs = brief_refs.build_refs(item)
    assert [r["tag"] for r in refs] == ["state", "drive", "kb"]   # canonical-roles order
    assert next(r for r in refs if r["tag"] == "state")["route"] == "#/mirror"


def test_http_papered_source_opens():
    refs = brief_refs.build_refs({"papered_source": "https://drive.google.com/file/d/abc/view"})
    assert refs == [{"tag": "drive", "label": "https://drive.google.com/file/d/abc/view",
                     "open": "https://drive.google.com/file/d/abc/view"}]


def test_no_refs_for_bare_item():
    assert brief_refs.build_refs({"id": "OI-909", "title": "a backlog seed"}) == []


def test_annotate_cache_cli_and_idempotent(tmp_path):
    cache = {"act": [{"id": "OI-901", "thread_id": "t1", "system_voice": {"cite": "state/threads/t1.md"}}],
             "held": [{"id": "OI-905", "state_path": "state/domains/x/tables/y/z.md"}]}
    p = tmp_path / "brief-cache.json"
    p.write_text(json.dumps(cache), encoding="utf-8")
    r = subprocess.run([sys.executable, str(TOOLS / "brief_refs.py"), "annotate", str(p)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(p.read_text(encoding="utf-8"))
    assert out["act"][0]["refs"] and out["held"][0]["refs"]
    n1 = len(out["act"][0]["refs"])
    # idempotent — rebuilt from source, never accumulates
    subprocess.run([sys.executable, str(TOOLS / "brief_refs.py"), "annotate", str(p)],
                   capture_output=True, text=True)
    out2 = json.loads(p.read_text(encoding="utf-8"))
    assert len(out2["act"][0]["refs"]) == n1
