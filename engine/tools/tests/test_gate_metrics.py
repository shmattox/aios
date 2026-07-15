import json, os, subprocess, sys
import pytest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import gate_metrics as gm  # noqa: E402


def _item(stage="shipped", history=None, recommended="approve", kb="demo", lane="auto-ship", id="x"):
    return {"id": id, "stage": stage, "recommended": recommended, "kb": kb, "lane": lane,
            "history": history if history is not None else []}


# --- decider_class: documented extraction = most recent history entry CARRYING the key ---

def test_decider_prefers_normalized_decided_by():
    it = _item(history=[{"ts": "t1", "stage": "shipped", "approved_by": "auto-ship",
                         "decided_by": "human"}])
    assert gm.decider_class(it) == "human"

def test_decider_reverse_scan_takes_most_recent_carrying_entry():
    it = _item(history=[{"ts": "t1", "stage": "shipped", "approved_by": "auto-ship"},
                        {"ts": "t2", "stage": "reverted"},          # no key — skipped
                        {"ts": "t3", "stage": "shipped", "approved_by": "a-person"}])
    assert gm.decider_class(it) == "human"                          # t3 wins, t2 skipped

def test_decider_legacy_vocabulary_fact_free():
    # any non-auto value is a named human approver — NO instance-name prefixes in the engine
    for raw, want in [("auto-ship", "auto"), ("auto-ship-scheduled", "scheduled"),
                      ("a-person", "human"), ("A-Person-batch-hygiene", "human"),
                      ("someone-brief-2026-07-08", "human")]:
        it = _item(history=[{"ts": "t", "stage": "shipped", "approved_by": raw}])
        assert gm.decider_class(it) == want, raw

def test_decider_missing_is_unknown_never_dropped():
    assert gm.decider_class(_item(history=[{"ts": "t", "stage": "shipped"}])) == "unknown"


# --- outcome / agreement matrix ---

def test_outcome_mapping():
    assert gm.outcome(_item(stage="shipped")) == "accepted"
    assert gm.outcome(_item(stage="rejected")) == "rejected"
    assert gm.outcome(_item(stage="reverted")) == "reverted"

@pytest.mark.parametrize("stage,rec,want", [
    ("shipped", "approve", "agree"), ("shipped", "reject", "override"),
    ("rejected", "reject", "agree"), ("rejected", "approve", "override"),
    ("shipped", "hold", "hold"), ("rejected", "hold", "hold"),
    ("shipped", None, "na"), ("reverted", "approve", "na"),   # reverted excluded from agreement
])
def test_agreement_matrix(stage, rec, want):
    assert gm.agreement(_item(stage=stage, recommended=rec)) == want


# --- terminal_date + windowing ---

def test_terminal_date_from_terminal_history_entry():
    it = _item(history=[{"ts": "2026-07-01T05:00:00Z", "stage": "awaiting"},
                        {"ts": "2026-07-03T05:00:00Z", "stage": "shipped", "approved_by": "auto-ship"}])
    assert gm.terminal_date(it) == "2026-07-03"

def test_terminal_date_missing_goes_unknown_ts_bucket():
    r = gm.rollup([_item(history=[])], today="2026-07-15")
    assert r["windows"]["all"]["unknown_ts"] == 1
    assert r["windows"]["all"]["n"] == 1          # still counted all-time
    assert r["windows"]["30d"]["n"] == 0          # but never inside a dated window

def test_rollup_windows_and_by_kb_lane():
    old = _item(history=[{"ts": "2026-01-01T00:00:00Z", "stage": "shipped", "approved_by": "auto-ship"}])
    new = _item(stage="rejected", recommended="approve", kb="fo", lane="review",
                history=[{"ts": "2026-07-14T00:00:00Z", "stage": "rejected", "reason": "r",
                          "decided_by": "human"}])
    r = gm.rollup([old, new], today="2026-07-15")
    assert r["windows"]["all"]["totals"] == {"accepted": 1, "rejected": 1, "reverted": 0}
    assert r["windows"]["7d"]["totals"] == {"accepted": 0, "rejected": 1, "reverted": 0}
    assert r["windows"]["7d"]["deciders"]["human"] == 1
    assert r["windows"]["7d"]["agreement"]["override"] == 1
    assert r["windows"]["7d"]["override_ids"] == ["x"]
    assert r["windows"]["7d"]["by_kb_lane"] == {"fo|review": {"accepted": 0, "rejected": 1, "reverted": 0}}

def test_rollup_ignores_non_terminal_stages():
    r = gm.rollup([_item(stage="awaiting"), _item(stage="sorted")], today="2026-07-15")
    assert r["windows"]["all"]["n"] == 0


# --- CLI: report + render ---

def _mkqueue(tmp_path, items):
    p = tmp_path / "queue.json"
    p.write_text(json.dumps({"queue": items}), encoding="utf-8")
    return str(p)


def _run(argv):
    return subprocess.run([sys.executable, os.path.join(TOOLS, "gate_metrics.py")] + argv,
                          capture_output=True, text=True)


def test_cli_report_writes_out_and_prints_json(tmp_path):
    it = _item(id="x", history=[{"ts": "2026-07-14T00:00:00Z", "stage": "shipped",
                                 "approved_by": "auto-ship"}])
    it["conflict_key"] = "demo/wiki/item/x"
    q = _mkqueue(tmp_path, [it])
    out = str(tmp_path / "gate-metrics.json")
    r = _run(["report", "--queue", q, "--today", "2026-07-15", "--out", out])
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["windows"]["7d"]["totals"]["accepted"] == 1
    assert json.load(open(out, encoding="utf-8")) == payload


def test_cli_render_fixed_format(tmp_path):
    it1 = _item(id="x", history=[{"ts": "2026-07-14T00:00:00Z", "stage": "shipped", "approved_by": "auto-ship"}])
    it1["conflict_key"] = "demo/wiki/item/x"
    it2 = _item(id="y", stage="rejected", recommended="approve",
                history=[{"ts": "2026-07-13T00:00:00Z", "stage": "rejected", "decided_by": "human"}])
    q = _mkqueue(tmp_path, [it1, it2])
    r = _run(["render", "--queue", q, "--today", "2026-07-15"])
    assert r.returncode == 0
    assert "Gate acceptance (30d): 50% accepted (n=2: 1 ship / 1 reject / 0 revert)" in r.stdout
    assert "human 1 / auto 1 / sched 0 / unk 0" in r.stdout
    assert "recommendation overrides (30d): 1" in r.stdout
    assert "y" in r.stdout


def test_cli_render_missing_queue_is_loud_not_zeros(tmp_path):
    r = _run(["render", "--queue", str(tmp_path / "absent.json"), "--today", "2026-07-15"])
    assert r.returncode == 0
    assert "metrics unavailable" in r.stdout
    assert "0%" not in r.stdout


def test_cli_report_missing_queue_exits_nonzero(tmp_path):
    r = _run(["report", "--queue", str(tmp_path / "absent.json"), "--today", "2026-07-15"])
    assert r.returncode == 1
