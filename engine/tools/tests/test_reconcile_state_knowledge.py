# sanitize:allow-file — fixtures use synthetic slugs/values by design (A79)
"""Tests for reconcile_state_knowledge.py (A104) — state→wiki economic-figure drift detector."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reconcile_state_knowledge as R  # noqa: E402

TOOL = Path(__file__).resolve().parents[1] / "reconcile_state_knowledge.py"


def _write(p, body):
    p.write_text(body, encoding="utf-8")
    return p


# ─────────────────────────── Task 1: anchor parser ───────────────────────────

def test_parse_anchor_happy(tmp_path):
    page = _write(tmp_path / "btc.md",
                  '---\ntitle: x\nsnapshots:\n  - "fo/assets/loan|balance|2059169|2026-05-30|true"\n---\nbody\n')
    assert R.parse_anchors(page) == [{
        "state_key": "fo/assets/loan", "field": "balance", "value": 2059169.0,
        "as_of": "2026-05-30", "track": True,
        "raw": "fo/assets/loan|balance|2059169|2026-05-30|true"}]


def test_parse_no_snapshots(tmp_path):
    assert R.parse_anchors(_write(tmp_path / "p.md", "---\ntitle: x\n---\nbody\n")) == []


def test_parse_no_frontmatter(tmp_path):
    assert R.parse_anchors(_write(tmp_path / "p.md", "just body, no frontmatter\n")) == []


def test_parse_malformed_skipped(tmp_path):
    page = _write(tmp_path / "p.md",
                  '---\nsnapshots:\n  - "too|few|fields"\n  - "fo/a/x|balance|NaNish|2026-01-01|true"\n---\n')
    assert R.parse_anchors(page) == []
    assert len(R.parse_errors(page)) == 2


def test_parse_non_finite_rejected(tmp_path):
    page = _write(tmp_path / "p.md",
                  '---\nsnapshots:\n  - "fo/a/x|balance|nan|2026-01-01|true"\n'
                  '  - "fo/a/y|balance|inf|2026-01-01|true"\n---\n')
    assert R.parse_anchors(page) == []
    assert len(R.parse_errors(page)) == 2


# ─────────────────────────── Task 2: state-row reader ───────────────────────────

def _row(tmp_path, silo, table, slug, body):
    d = tmp_path / "state" / "domains" / silo / "tables" / table
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.md").write_text(body, encoding="utf-8")


def test_read_state_field_present(tmp_path):
    _row(tmp_path, "familyoffice", "assets", "loan",
         "---\ntype: state-asset\nbalance: 3310000\nlast_synced: 2026-07-19\n---\n")
    assert R.read_state_field(tmp_path, "familyoffice/assets/loan", "balance") == {
        "value": 3310000.0, "last_synced": "2026-07-19", "found": True}


def test_read_state_field_absent_file(tmp_path):
    assert R.read_state_field(tmp_path, "familyoffice/assets/nope", "balance") is None


def test_read_state_field_null(tmp_path):
    _row(tmp_path, "familyoffice", "assets", "loan", "---\ntype: state-asset\nbalance: null\n---\n")
    assert R.read_state_field(tmp_path, "familyoffice/assets/loan", "balance")["found"] is False


def test_read_state_field_bad_key(tmp_path):
    assert R.read_state_field(tmp_path, "onlytwo/parts", "balance") is None


def test_read_state_field_traversal_guarded(tmp_path):
    # a `..` segment in the state_key must never escape state/domains (defense-in-depth)
    assert R.read_state_field(tmp_path, "familyoffice/../../secrets", "balance") is None


# ─────────────────────────── Task 3: drift comparison ───────────────────────────

A = {"state_key": "fo/assets/loan", "field": "balance", "value": 2059169.0,
     "as_of": "2026-05-30", "track": True, "raw": "..."}


def test_value_drift_flags():
    st = {"value": 3310000.0, "last_synced": "2026-07-19", "found": True}
    got = R.evaluate(A, st, today="2026-07-20")
    assert got["reason"] == "value" and got["target_value"] == 3310000.0


def test_within_threshold_silent():
    st = {"value": 2060000.0, "last_synced": "2026-07-19", "found": True}  # ~0.04% delta
    assert R.evaluate(A, st, today="2026-07-20") is None


def test_track_false_silent():
    st = {"value": 9999999.0, "last_synced": "2026-07-19", "found": True}
    assert R.evaluate({**A, "track": False}, st, today="2026-07-20") is None


def test_state_absent_silent():
    assert R.evaluate(A, None, today="2026-07-20") is None


def test_state_unfound_silent():
    st = {"value": None, "last_synced": "2026-07-19", "found": False}
    assert R.evaluate(A, st, today="2026-07-20") is None


def test_stale_with_newer_state_flags():
    st = {"value": 2059169.0, "last_synced": "2026-07-19", "found": True}  # same value…
    got = R.evaluate(A, st, today="2026-07-20", stale_days=30)  # …but 51d-old anchor + newer state
    assert got["reason"] == "stale"


def test_stale_but_state_not_newer_silent():
    st = {"value": 2059169.0, "last_synced": "2026-05-01", "found": True}  # state OLDER than anchor
    assert R.evaluate(A, st, today="2026-07-20", stale_days=30) is None


# ─────────────────────────── Task 4: dedup ───────────────────────────

def test_dedupe_key_stable():
    assert R.dedupe_key("fo/wiki/knowledge/btc.md", "fo/assets/loan", 3310000.0) \
        == "fo/wiki/knowledge/btc.md|fo/assets/loan|3310000.00"


def test_already_proposed(tmp_path):
    key = "p|s|3310000.00"
    q = tmp_path / "queue.json"
    q.write_text(json.dumps({"queue": [
        {"id": "x", "stage": "rejected", "reconcile": {"dedupe_key": key}}]}), encoding="utf-8")
    assert R.already_proposed(q, key) is True
    assert R.already_proposed(q, "other|k|0.00") is False


def test_already_proposed_missing_queue(tmp_path):
    assert R.already_proposed(tmp_path / "nope.json", "k") is False


# ─────────────────────────── Task 5: proposal emission ───────────────────────────

def test_build_refresh_rewrites_anchor_and_prose(tmp_path):
    page = tmp_path / "btc-treasury.md"
    page.write_text('---\ntitle: BTC\nsnapshots:\n'
                    '  - "familyoffice/assets/loan|balance|2059169|2026-05-30|true"\n---\n'
                    'Loan $2059169 today.\n', encoding="utf-8")
    anchor = {"state_key": "familyoffice/assets/loan", "field": "balance",
              "value": 2059169.0, "as_of": "2026-05-30", "track": True,
              "raw": "familyoffice/assets/loan|balance|2059169|2026-05-30|true"}
    verdict = {"reason": "value", "target_value": 3310000.0,
               "as_of_new": "2026-07-20", "delta": 1250831.0}
    out = R.build_refresh(page, anchor, verdict, kb="familyoffice", vault_folder="02_FamilyOffice")
    assert "|balance|3310000|2026-07-20|true" in out["staged_text"]
    assert "$3310000" in out["staged_text"]  # prose rewritten
    it = out["item"]
    assert it["lane"] == "review" and it["stage"] == "awaiting" and it["kb"] == "familyoffice"
    assert it["conflict_key"] == "familyoffice/wiki/knowledge/btc-treasury.md" \
        or it["draft_path"].endswith("staging/btc-treasury.md")
    assert it["reconcile"]["dedupe_key"].endswith("|familyoffice/assets/loan|3310000.00")


def test_build_refresh_prose_absent_notes_it(tmp_path):
    page = tmp_path / "wiki" / "knowledge" / "p.md"
    page.parent.mkdir(parents=True)
    page.write_text('---\nsnapshots:\n  - "familyoffice/assets/loan|balance|100|2026-05-30|true"\n---\n'
                    'No figure in this prose.\n', encoding="utf-8")
    anchor = R.parse_anchors(page)[0]
    verdict = {"reason": "value", "target_value": 200.0, "as_of_new": "2026-07-20", "delta": 100.0}
    out = R.build_refresh(page, anchor, verdict, kb="familyoffice", vault_folder="02_FamilyOffice")
    assert "prose figure not auto-found" in out["item"]["rec_reason"]
    assert out["item"]["conflict_key"] == "familyoffice/wiki/knowledge/p.md"


def test_build_refresh_item_validates(tmp_path):
    """The emitted item must pass queue_tx.validate (wiki-shape conflict_key + draft_path + awaiting)."""
    from queue_tx import validate
    anchor = {"state_key": "familyoffice/assets/loan", "field": "balance", "value": 100.0,
              "as_of": "2026-05-30", "track": True,
              "raw": "familyoffice/assets/loan|balance|100|2026-05-30|true"}
    verdict = {"reason": "value", "target_value": 200.0, "as_of_new": "2026-07-20", "delta": 100.0}
    real = tmp_path / "wiki" / "knowledge" / "p.md"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text('---\nsnapshots:\n  - "familyoffice/assets/loan|balance|100|2026-05-30|true"\n---\nbody\n',
                    encoding="utf-8")
    out = R.build_refresh(real, anchor, verdict, kb="familyoffice", vault_folder="02_FamilyOffice")
    assert validate({"queue": [out["item"]]}) is None


# ─────────────────────────── Task 6/7: CLI scan + emit ───────────────────────────

def _mini_env(tmp_path, *, drifted=True):
    """Build a minimal env-root: profile knobs, a vault KB page with anchors, state rows, empty queue.

    Layout: one DRIFTED anchor (loan), one MATCHING anchor (btc, within threshold), one track:false."""
    (tmp_path / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path / "profile" / "domains.yaml").write_text(
        "reconcile:\n  value_threshold: 0.02\n  abs_floor: 1.0\n  stale_days: 30\n", encoding="utf-8")
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "queue.json").write_text(json.dumps({"queue": []}), encoding="utf-8")

    # state rows
    _row(tmp_path, "familyoffice", "assets", "loan",
         "---\ntype: state-asset\nbalance: 3310000\nlast_synced: 2026-07-19\n---\n")
    _row(tmp_path, "familyoffice", "assets", "btc",
         "---\ntype: state-asset\nbalance: 1000000\nlast_synced: 2026-07-19\n---\n")

    # vault page with three anchors
    kb_dir = tmp_path / "vault" / "02_FamilyOffice" / "wiki" / "knowledge"
    kb_dir.mkdir(parents=True, exist_ok=True)
    loan_val = "2059169" if drifted else "3310000"
    loan_as_of = "2026-05-30" if drifted else "2026-07-19"  # non-drift anchor is fresh (no stale flag)
    (kb_dir / "assets.md").write_text(
        "---\ntitle: assets\nsnapshots:\n"
        f'  - "familyoffice/assets/loan|balance|{loan_val}|{loan_as_of}|true"\n'
        '  - "familyoffice/assets/btc|balance|1000000|2026-07-19|true"\n'
        '  - "familyoffice/assets/loan|balance|999|2026-05-30|false"\n'
        "---\n"
        f"The loan balance is ${loan_val} as of the last statement.\n", encoding="utf-8")
    return {"env_root": str(tmp_path), "vault_root": str(tmp_path / "vault"),
            "kb_map": json.dumps({"familyoffice": "02_FamilyOffice"})}


def test_cli_json_reports_one_proposal(tmp_path):
    env = _mini_env(tmp_path, drifted=True)
    out = subprocess.run([sys.executable, str(TOOL), "run", "--env-root", env["env_root"],
                          "--vault-root", env["vault_root"], "--kb-map", env["kb_map"],
                          "--today", "2026-07-20", "--json"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["proposals"] == 1 and data["parse_warnings"] == 0


def test_cli_json_matching_reports_zero(tmp_path):
    env = _mini_env(tmp_path, drifted=False)
    out = subprocess.run([sys.executable, str(TOOL), "run", "--env-root", env["env_root"],
                          "--vault-root", env["vault_root"], "--kb-map", env["kb_map"],
                          "--today", "2026-07-20", "--json"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["proposals"] == 0


def test_cli_emit_enqueues_valid_review_item(tmp_path):
    """Task 7: an emitted item passes queue_tx.validate and surfaces on the review lane."""
    env = _mini_env(tmp_path, drifted=True)
    out = subprocess.run([sys.executable, str(TOOL), "run", "--env-root", env["env_root"],
                          "--vault-root", env["vault_root"], "--kb-map", env["kb_map"],
                          "--today", "2026-07-20", "--emit", "--json"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["emitted"] == 1
    # queue validates and the item is a review-lane awaiting draft
    from queue_tx import load
    q = load(str(Path(env["env_root"]) / "state" / "queue.json"))
    recon = [it for it in q["queue"] if it.get("source") == "reconcile"]
    assert len(recon) == 1
    assert recon[0]["lane"] == "review" and recon[0]["stage"] == "awaiting"
    # the staged draft was written under the vault
    staged = Path(env["vault_root"]) / recon[0]["draft_path"]
    assert staged.is_file() and "|balance|3310000|2026-07-20|true" in staged.read_text(encoding="utf-8")


def test_cli_emit_is_idempotent(tmp_path):
    """A second emit run dedupes — no duplicate proposal for the same drift."""
    env = _mini_env(tmp_path, drifted=True)
    for _ in range(2):
        subprocess.run([sys.executable, str(TOOL), "run", "--env-root", env["env_root"],
                        "--vault-root", env["vault_root"], "--kb-map", env["kb_map"],
                        "--today", "2026-07-20", "--emit", "--json"], capture_output=True, text=True)
    second = subprocess.run([sys.executable, str(TOOL), "run", "--env-root", env["env_root"],
                             "--vault-root", env["vault_root"], "--kb-map", env["kb_map"],
                             "--today", "2026-07-20", "--json"], capture_output=True, text=True)
    assert json.loads(second.stdout)["proposals"] == 0  # already proposed
    from queue_tx import load
    q = load(str(Path(env["env_root"]) / "state" / "queue.json"))
    assert len([it for it in q["queue"] if it.get("source") == "reconcile"]) == 1
