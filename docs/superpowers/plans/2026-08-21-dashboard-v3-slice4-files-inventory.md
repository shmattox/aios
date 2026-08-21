# Dashboard v3 Slice 4 — Files read-only + Open-in-Cursor + Inventory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The dashboard returns to zero write surface outside the gate allowlist (Seth's ruling #3): Files becomes a read-only viewer with an "Open in Cursor" deep link for edit intent; a new read-only Inventory panel lists skills, MCP server names, and the plugin version.

**Architecture:** Server subtraction (remove `/api/files/write` + `/api/files/create` routes and `files_state.write/create`) plus one new read-only aggregator (`inventory_state.py` → `/api/inventory`). UI: `files.js` sheds its editor affordances and gains the Cursor deep link; a new `inventory.js` view registers in the nav.

**Tech Stack:** Python 3 stdlib, Preact+HTM no-build UI, pytest.

**Spec:** `docs/superpowers/specs/2026-08-21-dashboard-v3-aaa-slice-design.md` §3.5, §3.7, §6 Slice 4. Ruling: Seth 2026-08-21 — "read only for dashboard. a 'open in cursor ide' button would be helpful" (`Memory/decisions.md` 2026-08-21 ruling 3).

## Global Constraints

- SUBTRACTION discipline: the write paths are DELETED, not disabled — routes, `files_state` functions, their tests, and the UI affordances all go; `grep -n "files/write\|files/create" engine/dashboard` must return only history/comments afterwards.
- The Inventory aggregator renders NAMES AND METADATA ONLY — never a config body, env var, token, or key value. MCP entries = top-level `mcpServers` KEY NAMES only. If a source file is unreadable, the section says so honestly (fail-open advisory), never guesses.
- `~/.claude.json` (the one deliberate outside-env-root read) is parsed for exactly one thing: `sorted(list(data.get("mcpServers", {}).keys()))`. No other key is read, logged, or returned. Same for `<env_root>/.mcp.json` if present.
- No new write path; `/api/inventory` is GET-only. Monochrome tokens; new classes `iv-*`; NO new hex. Every panel names its source. Match existing idioms (health_state/health.js are the closest siblings).
- The Cursor deep link uses the `cursor://file/<absolute-path>` scheme (VS Code-compatible); the button renders as a plain `<a href>` (no JS nav) so the OS handler owns it. Absolute path = `env_root` (from `/api/health`, already served) + `/` + relPath, forward slashes.
- Commit per task (H131); FOREGROUND tests; `PYTHONIOENCODING=utf-8`. Known environmental non-failures: port-5174 test; SSE-contention test (isolation-passes).
- `ui/panels/` may already be DELETED by A127's factory merge — do not recreate, reference, or mourn it.

## Baseline facts (verified 2026-08-21)

- Write surface today: `files_state.create` (:149-164) + `write` (:167-186); routes in `_api_post` (`dashboard_server.py:713+`: `/api/files/write`, `/api/files/create`); UI affordances in `files.js`: `post()` helper (:17-21), Save/Revert buttons (:192-195), `newFile` (:149-156) + its toolbar button (:165), `dirty` tracking (:137), textarea editing in `Editor` (:54-64), dirty-dot in tabs (:188).
- `files.js` must KEEP: tree, tabs, search, reveal/openPath, syntax highlight, `takePendingFile`/`subscribeOpenFile` deep-link intake (other views open files through it).
- Skills on disk: plugin skills `<plugin_root>/skills/*/SKILL.md` (plugin_root = `HERE.parent.parent` from `engine/dashboard/`); user skills `~/.claude/skills/*/SKILL.md`. Frontmatter reader exists: `from state_validate import _extract_frontmatter, _parse_yaml` (already imported by dashboard_server; import the same pair in the new module).
- Plugin version: `<plugin_root>/.claude-plugin/plugin.json` → `name`, `version`.
- Skill last-invocation: NO standing feed exists (activity records don't log skill names; the usage-audit extract is monthly and currently recovering). Ruling: render descriptions, not fake timestamps.
- `test_files_state.py` exists and covers write/create — those tests are REPLACED with route-absence + read-only assertions, not deleted silently.

---

### Task 1: Worktree + base

- [ ] **Step 1:** `git status --short` clean on main; note HEAD (post-slice-3 merge; A127 may also have landed).
- [ ] **Step 2:** Worktree branch `v3-slice4-files-inventory` from `main` at `.worktrees/v3-slice4`.
- [ ] **Step 3:** Baseline `python -m pytest engine -q`; record count.

### Task 2: Server — delete write paths, add `inventory_state` + `/api/inventory` (TDD)

**Files:**
- Modify: `engine/dashboard/files_state.py` (delete `create` + `write`; module docstring → read-only contract)
- Modify: `engine/dashboard/dashboard_server.py` (delete the two POST routes; add `import inventory_state` + GET `/api/inventory`)
- Create: `engine/dashboard/inventory_state.py`
- Test: `engine/tools/tests/test_files_state.py` (replace write/create tests), `engine/tools/tests/test_a63_dashboard_api.py` (route-absence + inventory tests), create `engine/tools/tests/test_inventory_state.py`

**Interfaces:**
- Produces: `inventory_state.summary(env_root: str) -> dict`:
  `{"plugin": {"name": str|None, "version": str|None}, "skills": [{"group": "plugin"|"user", "name": str, "description": str}], "mcp": {"servers": [str], "sources": [str], "note": str|None}}` — `note` carries the honest failure line when a source was unreadable.
- Produces: `GET /api/inventory` → that payload. `POST /api/files/write|create` → 404.

- [ ] **Step 1: Failing tests.** `test_inventory_state.py`:

```python
import json, os, sys
from pathlib import Path
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
```

In `test_a63_dashboard_api.py` append:

```python
def test_files_write_routes_are_gone(server):
    port = server.server_address[1]
    for route in ("/api/files/write", "/api/files/create"):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{route}", method="POST",
                                     data=b"{}", headers={"Content-Type": "application/json",
                                                          "X-Aios-Token": server.token})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                assert False, f"{route} should 404, got {r.status}"
        except urllib.error.HTTPError as e:
            assert e.code == 404


def test_inventory_route_shape(server):
    inv = _get_json(server, "/api/inventory")
    assert set(inv) == {"plugin", "skills", "mcp"}
    assert isinstance(inv["skills"], list) and isinstance(inv["mcp"]["servers"], list)
```

In `test_files_state.py`: delete the write/create test functions; add `def test_module_is_read_only(): assert not hasattr(files_state, "write") and not hasattr(files_state, "create")`.
- [ ] **Step 2:** Run all three test files — new tests FAIL (module missing / routes present / attrs present).
- [ ] **Step 3: Implement.** `inventory_state.py`:

```python
"""inventory_state — read-only install inventory for the dashboard.

Lists the aios plugin's skills, the user's ~/.claude/skills, MCP server NAMES, and the
plugin version. HARD CONTRACT: names and descriptions only — never a config body, env
var, token, or key value. ~/.claude.json is read for exactly one thing: the top-level
mcpServers KEY NAMES. Fail-open: an unreadable source becomes an honest note, never an
exception, never a guess."""
import json
import os
from pathlib import Path

from state_validate import _extract_frontmatter, _parse_yaml

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
            fm = _parse_yaml(_extract_frontmatter(f.read_text(encoding="utf-8"))) or {}
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
        plugin = {"name": pj.get("name"), "version": pj.get("version")}
    except (OSError, ValueError):
        pass
    skills = _skill_rows(PLUGIN_ROOT / "skills", "plugin") + _skill_rows(USER_SKILLS_DIR, "user")
    return {"plugin": plugin, "skills": skills, "mcp": _mcp_names(env_root)}
```

`dashboard_server.py`: `import inventory_state` beside the other aggregators; in `_api_get` add `if route == "/api/inventory": return self._send_json(inventory_state.summary(str(env)))`; in `_api_post` DELETE the `/api/files/write` and `/api/files/create` branches. `files_state.py`: delete `create()` and `write()`, rewrite the module docstring to the read-only contract, drop `_WRITE_MAX`.
- [ ] **Step 4:** All three test files green; full suite green (fix any other test that asserted the write routes — in this task, never skipped).
- [ ] **Step 5:** Commit: `feat(dashboard)!: Files backend read-only (write/create removed) + /api/inventory aggregator`

### Task 3: `files.js` read-only + Open in Cursor

**Files:**
- Modify: `engine/dashboard/ui/views/files.js`
- Modify: `engine/dashboard/ui/tokens.css` (only if a class is needed)
- Modify: `skills/dashboard/SKILL.md` (if its Files line says "editor", say "viewer + Open-in-Cursor" — one line; skip if A127's refresh already reworded it)

- [ ] **Step 1:** Strip the editor: delete `post()`, `save`, `revert`, `newFile`, `dirty`, `setText`, the Save/Revert buttons, the New-file toolbar button, the tab dirty-dot; `Editor` drops the `<textarea>` overlay and renders the highlighted `<pre>` alone (keep scroll; rename nothing other views import — check `filenav.js` consumers unaffected). Header comment updated: "read-only viewer — edit intent hands off to Cursor".
- [ ] **Step 2:** Open in Cursor: fetch `env_root` once (`api.get("/api/health").then((h) => setEnvRoot(h.env_root))`); in the `fm-acts` slot render, when a tab is active:

```javascript
            ${cur && envRoot ? html`<a class="verb ok" title="Open this file in Cursor"
                href=${"cursor://file/" + String(envRoot).replace(/\\/g, "/") + "/" + cur.path}>Open in Cursor</a>` : null}
```

- [ ] **Step 3:** `node --check`; headless probe (`/api/files/read` still 200, `/` 200); `python -m pytest engine/tools/tests/test_a63_dashboard_api.py engine/tools/tests/test_files_state.py -q` foreground.
- [ ] **Step 4:** Commit: `feat(dashboard): Files view read-only with Open-in-Cursor handoff`

### Task 4: Inventory view + nav

**Files:**
- Create: `engine/dashboard/ui/views/inventory.js`
- Modify: `engine/dashboard/ui/app.js` (import, `ICONS.inventory`, NAV after `sessions`, chord `g v`)
- Modify: `engine/dashboard/ui/tokens.css` (`iv-*` via existing tokens if `.hl-*` doesn't already fit — prefer reuse)

- [ ] **Step 1: `inventory.js`:**

```javascript
// Inventory — what this install is made of: the plugin's skills, the user's skills, MCP server
// names, and the plugin version. Read-only; names and descriptions only — config bodies, tokens,
// and key values never reach this payload (see inventory_state.py's hard contract).
import { html, api, useState, useEffect } from "/lib.js";

function Metric({ k, v, sub }) {
  return html`<div class="ov-cell"><div class="ov-k">${k}</div><div class="ov-v">${v}</div><div class="ov-s">${sub || ""}</div></div>`;
}

export function InventoryView() {
  const [inv, setInv] = useState(null);
  useEffect(() => { api.get("/api/inventory").then(setInv).catch(() => setInv({ error: true })); }, []);
  if (inv == null) return html`<section class="view"><div class="viewhead"><h1>Inventory</h1></div><p class="stub">…</p></section>`;
  if (inv.error) return html`<section class="view"><div class="viewhead"><h1>Inventory</h1></div><p class="stub">/api/inventory did not answer.</p></section>`;

  const plug = inv.skills.filter((s) => s.group === "plugin");
  const user = inv.skills.filter((s) => s.group === "user");
  const mcp = inv.mcp || { servers: [] };
  return html`<section class="view">
    <div class="viewhead"><h1>Inventory</h1><span class="sub">skills · MCP · plugin</span></div>

    <div class="ov-strip">
      <${Metric} k="plugin" v=${inv.plugin?.version || "—"} sub=${inv.plugin?.name || "not found"} />
      <${Metric} k="plugin skills" v=${plug.length} sub="skills/" />
      <${Metric} k="user skills" v=${user.length} sub="~/.claude/skills" />
      <${Metric} k="MCP servers" v=${mcp.servers.length} sub=${(mcp.sources || []).join(" + ") || "no config found"} />
    </div>

    <h3 class="ov-sect">Plugin skills <span class="uz-src">· skills/*/SKILL.md</span></h3>
    <div class="hl-list">
      ${plug.map((s) => html`<div class="hl-row" key=${"p" + s.name}>
        <span class="hl-id">${s.name}</span><span class="hl-meta">${s.description || "—"}</span></div>`)}
    </div>

    <h3 class="ov-sect">User skills <span class="uz-src">· ~/.claude/skills/*/SKILL.md</span></h3>
    ${user.length ? html`<div class="hl-list">
      ${user.map((s) => html`<div class="hl-row" key=${"u" + s.name}>
        <span class="hl-id">${s.name}</span><span class="hl-meta">${s.description || "—"}</span></div>`)}
    </div>` : html`<p class="stub">No user-level skills found.</p>`}

    <h3 class="ov-sect">MCP servers <span class="uz-src">· names only — config never leaves the machine's files</span></h3>
    ${mcp.servers.length ? html`<div class="hl-list">
      ${mcp.servers.map((n) => html`<div class="hl-row" key=${n}>
        <span class="hl-id">${n}</span><span class="hl-meta">configured</span></div>`)}
    </div>` : html`<p class="stub">${mcp.note || "None found."}</p>`}
    ${mcp.servers.length && mcp.note ? html`<p class="uz-note">${mcp.note}</p>` : null}
  </section>`;
}
```

- [ ] **Step 2:** Register in `app.js`: import; `ICONS.inventory = html`<svg class="ic" viewBox="0 0 16 16"><rect x="2.5" y="2.5" width="11" height="4" rx="1"/><rect x="2.5" y="9.5" width="11" height="4" rx="1"/></svg>``; NAV `{ key: "inventory", label: "Inventory", view: InventoryView }` after `sessions`; chord `g v` in the `g` block.
- [ ] **Step 3:** `node --check` both; headless `/api/inventory` 200 against the real env root (spot-check: 12 plugin skills, MCP names present, NO secret-looking strings in the payload — grep the JSON for "token"/"Bearer"/"key:" as a belt-and-braces check); targeted pytest foreground.
- [ ] **Step 4:** Commit: `feat(dashboard): Inventory view — skills, MCP names, plugin version`

### Task 5: Verification, review, merge

- [ ] **Step 1 (controller): browser pass** — Files: tree/tabs/search intact, NO Save/Revert/New controls anywhere, "Open in Cursor" renders with an absolute `cursor://file/` href; click it (or `Start-Process` the href) and confirm Cursor opens the file on this machine; Inventory renders real skills + MCP names, zero secret material on the wire (`read_network_requests` on `/api/inventory`); console clean. Screenshots.
- [ ] **Step 2:** Rebase onto latest main; full suite.
- [ ] **Step 3:** Fresh-context whole-branch review (most capable model) — special attention: the deletion is complete (grep), the inventory payload can never carry config values, `~/.claude.json` read is surgical.
- [ ] **Step 4:** ff-merge + version bump + A130 annotation (Slice 4 shipped — all four slices done; A130's remaining scope = the "operating UI" week on A109 + noted deferrals) in one commit; push; suite on merged main; worktree removed from outside.
- [ ] **Step 5:** Screenshots to Seth — his look closes the slice AND the A130 build phase.

---

## Self-review (done at authoring)

- Spec §3.5 (inventory: plugin+user skills, MCP names-only, honest unknowns — last-invocation deliberately omitted per the no-standing-feed ruling, recorded), §3.7 (read-only + Cursor handoff per Seth's ruling 3), §6 Slice 4 acceptance → Tasks 2-5. The A63 "dashboard never writes state" boundary is fully restored (the 7-action gate allowlist becomes the ONLY write surface).
- Placeholders: none. Types: `inventory_state.summary` keys ↔ route ↔ `inventory.js` reads (`plugin.version`, `skills[].{group,name,description}`, `mcp.{servers,sources,note}`); `files.js` keeps the `filenav.js` contract (`takePendingFile`/`subscribeOpenFile` intake unchanged).
- Monkeypatch seams (`PLUGIN_ROOT`, `USER_SKILLS_DIR`, `USER_CLAUDE_JSON` as module-level Paths) are deliberate module attributes so tests never touch the real home dir.
