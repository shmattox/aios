# sanitize:allow-file — fixtures use synthetic/out-of-range ids by design (A79)
"""brief_refs — structured drill-down refs on brief items (A109 refs-at-source)."""
import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import brief_refs  # noqa: E402


def test_act_refs_dedupe_thread_file_and_readable_notion():
    item = {"id": "OI-901", "thread_id": "acme-bridge-hedge",
            "system_voice": {"grade": "2a", "text": "…",
                             "cite": "collection://11111111-1111-1111-1111 task X; "
                                     "collection://22222222-2222-2222-2222 decision Y; "
                                     "state/threads/acme-bridge-hedge.md"}}
    refs = brief_refs.build_refs(item)
    labels = [r["label"] for r in refs]
    # the cite's state/threads/<tid>.md is the SAME resource as "thread · <tid>" — one row, not two
    assert "thread · acme-bridge-hedge" in labels
    assert "state/threads/acme-bridge-hedge.md" not in labels
    assert sum(1 for r in refs if r["tag"] == "state") == 1
    # notion refs carry a readable role label, never a raw collection:// uuid
    assert [r["label"] for r in refs if r["tag"] == "notion"] == ["Notion · task", "Notion · decision"]
    assert not any("collection://" in lbl for lbl in labels)
    for r in refs:                       # every ref is still actionable
        assert r.get("route") or r.get("open") or r.get("mock")


def test_act_refs_keep_non_thread_state_path():
    # dedup only drops the thread's OWN file — a different state record in the cite still shows.
    item = {"id": "OI-902", "thread_id": "acme",
            "system_voice": {"cite": "state/domains/fo/tables/liabilities/note.md; state/threads/acme.md"}}
    labels = [r["label"] for r in brief_refs.build_refs(item)]
    assert "thread · acme" in labels
    assert "state/domains/fo/tables/liabilities/note.md" in labels   # non-thread path kept
    assert "state/threads/acme.md" not in labels                     # thread file deduped


def test_held_refs_from_canonical_fields():
    item = {"id": "OI-905", "kb": "familyoffice",
            "state_path": "state/domains/familyoffice/tables/liabilities/bayview-note.md",
            "papered_source": "Family Office/raw/Bayview Loan Modification (executed).pdf",
            "draft_path": "02_FamilyOffice/wiki/staging/bayview.md"}
    refs = brief_refs.build_refs(item)
    assert [r["tag"] for r in refs] == ["state", "drive", "kb"]   # canonical-roles order
    assert next(r for r in refs if r["tag"] == "state")["route"] == "#/mirror"


def test_kb_ref_obsidian_deeplink_when_vault_known():
    refs = brief_refs.build_refs({"draft_path": "02_FamilyOffice/wiki/staging/note here.md"},
                                 vault_name="SecondBrain")
    kb = next(r for r in refs if r["tag"] == "kb")
    assert kb["open"] == ("obsidian://open?vault=SecondBrain"
                          "&file=02_FamilyOffice/wiki/staging/note%20here.md")
    assert "mock" not in kb                      # a real deep-link, not a toast


def test_kb_ref_falls_back_to_mock_without_vault():
    kb = next(r for r in brief_refs.build_refs({"draft_path": "x/wiki/staging/z.md"})
              if r["tag"] == "kb")
    assert kb.get("mock") and "open" not in kb


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
