// Shared pipeline-flow skeleton — the horizontal node/edge graph and the stage drill-in list,
// used by every pipeline page (AIOS content, Software factory, and Marketing/Ops to come). Each
// page owns its data fetching and its detail card; this owns the reusable chrome. Nodes are
// {id,label,count[,kind]}; rows are pre-shaped {id,title,silo,sub,when} so this file stays
// domain-agnostic. An edge animates only when its upstream node holds work to advance.
import { html } from "/lib.js";

function Node({ node, sel, onSel }) {
  const empty = !node.count;
  return html`<button class="ai-node ${sel ? "sel" : ""} ${empty ? "empty" : ""}"
      aria-selected=${String(sel)} title=${node.label + " · " + node.count}
      onClick=${() => onSel(node.id)}>
    <span class="ai-count">${node.count}</span>
    <span class="ai-lbl">${node.label}</span>
    ${node.kind === "phase" ? html`<span class="ai-tag">24h</span>` : null}
  </button>`;
}

function Edge({ active }) {
  return html`<div class="ai-edge ${active ? "on" : ""}" aria-hidden="true">
    <span class="d"></span><span class="d"></span><span class="d"></span></div>`;
}

export function FlowGraph({ nodes, sel, onSel }) {
  if (!nodes.length) return html`<p class="stub">Loading pipeline…</p>`;
  return html`<div class="ai-flow">
    ${nodes.map((n, i) => html`
      <${Node} node=${n} sel=${n.id === sel} onSel=${onSel} key=${n.id} />
      ${i < nodes.length - 1 ? html`<${Edge} active=${n.count > 0} key=${"e" + n.id} />` : null}
    `)}
  </div>`;
}

// Master list for the selected stage. `rows` are pre-shaped; `sel`/`onSel` track selection.
export function StageList({ label, rows, sel, onSel }) {
  return html`<div class="ai-list">
    <div class="ai-listhead">${label} · <span class="num">${rows.length}</span></div>
    ${rows.length ? rows.map((r) => html`
      <div class="ai-row ${r.id === sel ? "sel" : ""}" key=${r.id} tabindex="0"
           aria-current=${String(r.id === sel)} onClick=${() => onSel(r.id)}>
        <span class="silo ${r.silo || "dev"}"></span>
        <span class="t"><span class="title">${r.title || r.id}</span>
          <span class="sub">${r.sub || ""}</span></span>
        ${r.when ? html`<span class="age num">${r.when}</span>` : null}
      </div>`)
      : html`<p class="stub">Nothing in ${(label || "this stage").toLowerCase()} right now.</p>`}
  </div>`;
}
