"""Sub-project A — 'no result file = failure' is the rule that keeps exit 0 from masking a leg
that did nothing (the env-usage-audit lesson). status is written to GITHUB_OUTPUT even on failure."""
import json, os, sys, tempfile
_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO = os.path.normpath(os.path.join(_TOOLS, "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "deploy", "github", "run-agent"))
import result_check as rc


def _result(**kw):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "result.json")
    base = {"leg": "ingest", "run_id": "1", "status": "ok", "summary": "drafted 3",
            "verify": {"passed": True, "notes": "queue ok"}}
    base.update(kw)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(base, f)
    return p


def test_missing_file_is_exit_1_status_missing():
    code, status, md = rc.check(os.path.join(tempfile.mkdtemp(), "nope.json"), "ingest")
    assert (code, status) == (1, "missing") and "no result file" in md


def test_invalid_json_is_exit_1():
    p = _result(); open(p, "w").write("{not json")
    assert rc.check(p, "ingest")[:2] == (1, "invalid")


def test_leg_mismatch_is_invalid():
    assert rc.check(_result(leg="garden"), "ingest")[:2] == (1, "invalid")


def test_ok_and_degraded_exit_0_failed_exit_1():
    assert rc.check(_result(status="ok"), "ingest")[0] == 0
    assert rc.check(_result(status="degraded"), "ingest")[:2] == (0, "degraded")
    assert rc.check(_result(status="failed"), "ingest")[:2] == (1, "failed")


def test_unknown_status_is_invalid():
    assert rc.check(_result(status="fine"), "ingest")[:2] == (1, "invalid")


def test_summary_mentions_leg_status_and_summary_text():
    _, _, md = rc.check(_result(), "ingest")
    assert "ingest" in md and "ok" in md and "drafted 3" in md


def test_cli_writes_output_and_summary_files_even_on_failure(tmp_path, monkeypatch):
    out, summ = tmp_path / "out.txt", tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summ))
    code = rc.main(["--leg", "ingest", "--result", str(tmp_path / "absent.json")])
    assert code == 1
    assert "status=missing" in out.read_text()
    assert "no result file" in summ.read_text()


def test_wrong_typed_verify_or_summary_is_invalid_not_crash():
    assert rc.check(_result(verify="not a dict"), "ingest")[:2] == (1, "invalid")
    assert rc.check(_result(summary=123), "ingest")[:2] == (1, "invalid")


def test_falsy_typed_verify_is_invalid_not_silently_coalesced():
    # `verify = r.get("verify") or {}` before the isinstance check let every falsy wrong type
    # through as an empty dict, so the guard only ever caught truthy ones.
    for bad in ([], "", 0):
        assert rc.check(_result(verify=bad), "ingest")[:2] == (1, "invalid"), bad


def test_ok_with_failed_verify_is_exit_1_and_status_failed():
    # Anti-green-wash (spec §4/§5): VERIFY mismatch => failed, nothing committed or pushed.
    code, status, md = rc.check(
        _result(status="ok", verify={"passed": False, "notes": "queue drifted"}), "ingest")
    assert (code, status) == (1, "failed")
    assert "VERIFY" in md and "queue drifted" in md


def test_ok_with_passing_verify_is_exit_0_ok():
    assert rc.check(_result(status="ok", verify={"passed": True, "notes": "n"}), "ingest")[:2] == (0, "ok")


def test_ok_without_a_verify_block_is_failed():
    # An absent verify block on an "ok" run is not evidence of verification.
    p = _result()
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    d.pop("verify")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f)
    assert rc.check(p, "ingest")[:2] == (1, "failed")


def test_cli_still_writes_status_on_wrong_typed_verify(tmp_path, monkeypatch):
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert rc.main(["--leg", "ingest", "--result", _result(verify="nope")]) == 1
    assert "status=invalid" in out.read_text()
