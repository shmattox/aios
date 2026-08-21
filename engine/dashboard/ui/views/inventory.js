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
