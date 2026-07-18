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


# --- A97: honest revert capture from history (not terminal stage) ---

def _undone(ship_ts, undo_ts, legacy=False):
    undo = {"ts": undo_ts, "stage": "awaiting", "by": "rewind",
            "note": "undo-ship (removed_vault_file=True)"}
    if not legacy:
        undo["undo_of"] = "shipped"          # A97 durable marker
    return _item(stage="awaiting", history=[
        {"ts": ship_ts, "stage": "shipped", "approved_by": "auto-ship"}, undo])


def test_revert_event_dates_reads_undo_marker():
    assert gm.revert_event_dates(
        _undone("2026-07-10T00:00:00Z", "2026-07-11T00:00:00Z")) == ["2026-07-11"]


def test_revert_counted_from_history_not_terminal_stage():
    # shipped then undone → the item now sits at `awaiting`, so the terminal-stage count is blind
    r = gm.rollup([_undone("2026-07-14T00:00:00Z", "2026-07-14T01:00:00Z")], today="2026-07-15")
    assert r["windows"]["all"]["totals"]["reverted"] == 0   # terminal-stage count misses it
    assert r["windows"]["all"]["reverts_hist"] == 1         # history count captures it
    assert r["windows"]["7d"]["reverts_hist"] == 1
    assert r["windows"]["all"]["n"] == 0                    # awaiting is not a terminal decision


def test_revert_legacy_marker_via_by_note_signature():
    # a pre-A97 revert (no undo_of key) is still counted via the by/note signature
    r = gm.rollup([_undone("2026-07-14T00:00:00Z", "2026-07-14T02:00:00Z", legacy=True)],
                  today="2026-07-15")
    assert r["windows"]["all"]["reverts_hist"] == 1


def test_revert_counts_reconcile_reset_from_shipped():
    # rewind.reset() bouncing a shipped item back to awaiting (reconcile desync repair) is a real ship
    # revert — A97 stamps undo_of on it so the honest counter isn't blind (review finding #1)
    it = _item(stage="awaiting", history=[
        {"ts": "2026-07-14T00:00:00Z", "stage": "shipped", "approved_by": "auto-ship"},
        {"ts": "2026-07-14T03:00:00Z", "stage": "awaiting", "by": "rewind",
         "undo_of": "shipped", "note": "rewind from shipped: reconcile"}])
    assert gm.rollup([it], today="2026-07-15")["windows"]["all"]["reverts_hist"] == 1


def test_render_shows_honest_revert_from_history(tmp_path):
    it1 = _item(id="x", history=[{"ts": "2026-07-14T00:00:00Z", "stage": "shipped",
                                  "approved_by": "auto-ship"}])
    it1["conflict_key"] = "demo/wiki/item/x"
    it2 = _undone("2026-07-13T00:00:00Z", "2026-07-13T05:00:00Z")
    it2["id"] = "y"
    it2["conflict_key"] = "demo/wiki/item/y"   # awaiting is a keyed stage
    r = _run(["render", "--queue", _mkqueue(tmp_path, [it1, it2]), "--today", "2026-07-15"])
    assert r.returncode == 0
    # n counts only the 1 terminal ship (it2 is back at awaiting); the revert is surfaced from history
    assert "n=1: 1 ship / 0 reject / 1 revert" in r.stdout


# --- CLI: report + render ---

def _mkqueue(tmp_path, items):
    p = tmp_path / "queue.json"
    p.write_text(json.dumps({"queue": items}), encoding="utf-8")
    return str(p)


def _run(argv):
    return subprocess.run([sys.executable, os.path.join(TOOLS, "gate_metrics.py")] + argv,
                          capture_output=True, encoding="utf-8")


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
    assert "📊 Gate acceptance (30d): 50% accepted (n=2: 1 ship / 1 reject / 0 revert)" in r.stdout
    assert "human 1 / auto 1 / sched 0 / unk 0" in r.stdout
    assert "recommendation overrides (30d): 1 — y" in r.stdout


def test_cli_render_missing_queue_is_loud_not_zeros(tmp_path):
    r = _run(["render", "--queue", str(tmp_path / "absent.json"), "--today", "2026-07-15"])
    assert r.returncode == 0
    assert "📊 Gate acceptance: metrics unavailable" in r.stdout
    assert "0%" not in r.stdout


def test_cli_report_missing_queue_exits_nonzero(tmp_path):
    r = _run(["report", "--queue", str(tmp_path / "absent.json"), "--today", "2026-07-15"])
    assert r.returncode == 1


# --- A73: ship.py normalized decided_by stamped at flip ---

def test_ship_derive_decided_by():
    import ship as shiptool
    assert shiptool._derive_decided_by("auto-ship", False) == "auto"
    assert shiptool._derive_decided_by("auto-ship-scheduled", False) == "scheduled"
    assert shiptool._derive_decided_by("a-person", True) == "human"
    assert shiptool._derive_decided_by("a-person", False) == "human"  # named approver => human even w/o flag

def test_reject_records_decided_by(tmp_path):
    q = _mkqueue(tmp_path, [{"id": "r1", "stage": "awaiting", "lane": "review",
                             "recommended": "reject", "kb": "demo", "history": [],
                             "conflict_key": "demo/wiki/notes/r1.md"}])
    import ship as shiptool
    shiptool.reject(q, "r1", "no draft", decided_by="human")
    item = json.load(open(q, encoding="utf-8"))["queue"][0]
    assert item["stage"] == "rejected"
    assert item["history"][-1]["decided_by"] == "human"
    assert item["history"][-1]["reason"] == "no draft"
