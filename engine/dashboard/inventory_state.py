"""inventory_state — read-only install inventory for the dashboard.

Lists the aios plugin's skills, the user's ~/.claude/skills, MCP server NAMES, and the
plugin version. HARD CONTRACT: names and descriptions only — never a config body, env
var, token, or key value. ~/.claude.json is read for exactly one thing: the top-level
mcpServers KEY NAMES. Fail-open: an unreadable source becomes an honest note, never an
exception, never a guess."""
import json
import os
from pathlib import Path

from state_validate import _extract_frontmatter

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent.parent
USER_SKILLS_DIR = Path(os.path.expanduser("~")) / ".claude" / "skills"
USER_CLAUDE_JSON = Path(os.path.expanduser("~")) / ".claude.json"


def _skill_rows(base, group):
    rows = []
    if not base.is_dir():
        return rows
    for d in sorted(base.iterdir()):
        f = d / "SKILL.md"
        if not d.is_dir() or not f.is_file():
            continue
        desc = ""
        try:
            fm = _extract_frontmatter(f.read_text(encoding="utf-8")) or {}
            desc = str(fm.get("description") or "").strip().split("\n")[0][:200]
        except (OSError, ValueError):
            pass
        rows.append({"group": group, "name": d.name, "description": desc})
    return rows


def _mcp_names(env_root):
    servers, sources, note = set(), [], None
    for label, path in (("~/.claude.json", USER_CLAUDE_JSON),
                        (".mcp.json", Path(env_root) / ".mcp.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            note = "%s unreadable — MCP list may be incomplete" % label
            continue
        if not isinstance(data, dict):
            note = "%s unreadable — MCP list may be incomplete" % label
            continue
        keys = data.get("mcpServers")
        if isinstance(keys, dict):
            servers.update(str(k) for k in keys)
            sources.append(label)
    if not sources and note is None:
        note = "no MCP config found"
    return {"servers": sorted(servers), "sources": sources, "note": note}


def summary(env_root):
    plugin = {"name": None, "version": None}
    try:
        pj = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        if isinstance(pj, dict):
            plugin = {"name": pj.get("name"), "version": pj.get("version")}
    except (OSError, ValueError):
        pass
    skills = _skill_rows(PLUGIN_ROOT / "skills", "plugin") + _skill_rows(USER_SKILLS_DIR, "user")
    return {"plugin": plugin, "skills": skills, "mcp": _mcp_names(env_root)}
