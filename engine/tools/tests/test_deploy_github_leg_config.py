"""Sub-project A — a GitHub-substrate leg inherits the native entry and overrides only what the
substrate changes (cron, enabled). Missing native entry / missing override are loud failures."""
import json, os, sys, tempfile
import pytest

_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO = os.path.normpath(os.path.join(_TOOLS, "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "deploy", "github"))
import leg_config as lc

_REAL = os.path.join(_REPO, "deploy", "tasks.manifest.json")


def _manifest(tasks):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "tasks.manifest.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"tasks": tasks}, f)
    return p


NATIVE = {"id": "aios-ingest", "substrate": "native", "cron": "0 2 * * *",
          "body_path": "deploy/tasks/ingest.md", "allowed_tools": ["Read", "Bash(python:*)"],
          "max_turns": 200, "enabled": True}
GH = {"id": "aios-gh-ingest", "substrate": "github", "cron": "0 8 * * *", "enabled": False}


def test_merge_inherits_native_and_overrides_cron_enabled():
    r = lc.resolve("ingest", _manifest([NATIVE, GH]))
    assert r == {"leg": "ingest", "body_path": "deploy/tasks/ingest.md", "model": "",
                 "max_turns": 200, "allowed_tools": "Read,Bash(python:*)", "cron": "0 8 * * *",
                 "enabled": False}


def test_model_from_native_passes_through():
    r = lc.resolve("garden", _manifest([dict(NATIVE, id="aios-garden", model="sonnet"),
                                        dict(GH, id="aios-gh-garden")]))
    assert r["model"] == "sonnet"


def test_missing_override_is_keyerror():
    with pytest.raises(KeyError):
        lc.resolve("ingest", _manifest([NATIVE]))


def test_missing_native_is_keyerror():
    with pytest.raises(KeyError):
        lc.resolve("ingest", _manifest([GH]))


def test_cli_prints_one_json_line(capsys):
    assert lc.main(["ingest", _manifest([NATIVE, GH])]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and json.loads(out[0])["leg"] == "ingest"


def test_real_manifest_has_all_wave1_legs_disabled_until_flip():
    for leg in ("ingest", "gate-auto", "garden"):
        r = lc.resolve(leg, _REAL)
        assert r["body_path"] == "deploy/tasks/%s.md" % leg
        assert r["max_turns"] > 0
        # Each flip commit (Tasks 8–10) changes this to True for its leg.
        assert r["enabled"] is False, leg
