import json, os, sys
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # engine/tools (state_validate)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "dashboard"))
import inventory_state


def test_plugin_and_skills_from_plugin_root(tmp_path, monkeypatch):
    proot = tmp_path / "plug"
    (proot / ".claude-plugin").mkdir(parents=True)
    (proot / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "aios", "version": "9.9.9"}), encoding="utf-8")
    sk = proot / "skills" / "brief"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text("---\nname: brief\ndescription: The daily launcher\n---\nbody",
                                 encoding="utf-8")
    monkeypatch.setattr(inventory_state, "PLUGIN_ROOT", proot)
    monkeypatch.setattr(inventory_state, "USER_SKILLS_DIR", tmp_path / "nouser")
    monkeypatch.setattr(inventory_state, "USER_CLAUDE_JSON", tmp_path / "noclaude.json")
    s = inventory_state.summary(str(tmp_path))
    assert s["plugin"] == {"name": "aios", "version": "9.9.9"}
    assert {"group": "plugin", "name": "brief", "description": "The daily launcher"} in s["skills"]


def test_mcp_names_only_never_config(tmp_path, monkeypatch):
    cj = tmp_path / "claude.json"
    cj.write_text(json.dumps({"mcpServers": {"notion": {"url": "https://x", "headers": {"Authorization": "Bearer SECRET"}},
                                             "drive": {"command": "npx", "env": {"KEY": "SECRET2"}}},
                              "oauthToken": "SECRET3"}), encoding="utf-8")
    monkeypatch.setattr(inventory_state, "USER_CLAUDE_JSON", cj)
    monkeypatch.setattr(inventory_state, "PLUGIN_ROOT", tmp_path / "noplug")
    monkeypatch.setattr(inventory_state, "USER_SKILLS_DIR", tmp_path / "nouser")
    s = inventory_state.summary(str(tmp_path))
    assert s["mcp"]["servers"] == ["drive", "notion"]
    assert "SECRET" not in json.dumps(s)          # the whole payload carries no config material


def test_unreadable_sources_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr(inventory_state, "USER_CLAUDE_JSON", tmp_path / "absent.json")
    monkeypatch.setattr(inventory_state, "PLUGIN_ROOT", tmp_path / "noplug")
    monkeypatch.setattr(inventory_state, "USER_SKILLS_DIR", tmp_path / "nouser")
    s = inventory_state.summary(str(tmp_path))
    assert s["plugin"]["version"] is None and s["skills"] == []
    assert s["mcp"]["servers"] == [] and s["mcp"]["note"]      # honest note, no exception
