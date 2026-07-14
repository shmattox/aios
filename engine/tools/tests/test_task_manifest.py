"""A46 — the setup VERIFY gate must DERIVE the enabled-native task count from the manifest,
never hardcode it. These checks pin the derivation and prove adding an 8th enabled-native task
does not re-break the gate (the exact failure mode of the stale literal `5`).
"""
import json
import os
import subprocess
import sys
import tempfile

_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _TOOLS)
import task_manifest as tm

_REAL_MANIFEST = os.path.normpath(os.path.join(_TOOLS, "..", "..", "deploy", "tasks.manifest.json"))
_TOOL = os.path.join(_TOOLS, "task_manifest.py")


def _write_manifest(tasks):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "tasks.manifest.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"tasks": tasks}, f)
    return p


def test_real_manifest_enabled_native_count_matches_registrar_filter():
    # The real manifest today has 7 enabled-native tasks (capture-router, session-capture, ingest,
    # gate-auto, garden, resolve-sweep, brief-cache). meetings-router is native but disabled; the
    # gate/manual/cloud entries are non-native. This is what registration produces.
    ids = tm.enabled_native_ids(_REAL_MANIFEST)
    assert tm.enabled_native_count(_REAL_MANIFEST) == 7, ids
    assert "aios-brief-cache" in ids, "brief-cache is enabled:true now (not false as the old prose claimed)"
    assert "aios-meetings-router" not in ids, "meetings-router is native but enabled:false — excluded"
    assert "aios-gate" not in ids, "manual entries are not scheduled"


def test_disabled_and_non_native_excluded():
    tasks = [
        {"id": "a", "substrate": "native", "enabled": True},
        {"id": "b", "substrate": "native", "enabled": False},   # disabled
        {"id": "c", "substrate": "manual", "enabled": True},     # non-native
        {"id": "d", "substrate": "schedule-cloud", "enabled": True},
    ]
    assert tm.enabled_native_count(_write_manifest(tasks)) == 1
    assert tm.enabled_native_ids(_write_manifest(tasks)) == ["a"]


def test_adding_an_eighth_enabled_task_does_not_re_break_the_gate():
    # The whole point of deriving from the manifest: an 8th enabled-native task moves the expected
    # count to 8 with NO edit to the gate. A hardcoded literal would still assert the old number.
    base = tm.enabled_native_tasks(_REAL_MANIFEST)
    plus_one = [dict(t) for t in base] + [{"id": "aios-future", "substrate": "native", "enabled": True}]
    assert tm.enabled_native_count(_write_manifest(plus_one)) == len(base) + 1


def test_cli_prints_the_count_and_ids():
    r = subprocess.run([sys.executable, _TOOL, "--manifest", _REAL_MANIFEST],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "7", r.stdout
    r2 = subprocess.run([sys.executable, _TOOL, "--manifest", _REAL_MANIFEST, "--ids"],
                        capture_output=True, text=True)
    assert r2.returncode == 0, r2.stderr
    assert "aios-ingest" in r2.stdout and "aios-brief-cache" in r2.stdout


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print("  ok  " + fn.__name__)
        except Exception:
            failed += 1; print(" FAIL " + fn.__name__); traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
