// AIOS — the content pipeline as a flow graph: capture → sort → ingest → gate → garden.
// Click a stage to drill into its items; the gate stage lists the real held drafts and opens
// the rich card (Ship / Reject / preview) — the same gated actions as the Inbox. Pre-gate
// stages open a read-only info card. Reads /api/content, /api/content/stage/<id>, /api/held.
import { html, api, useState, useEffect, toast } from "/lib.js";
import { Card, siloClass } from "./card.js";

const REPLY = { respond: 1, append: 1, comment: 1 };
const when = (s) => (s ? String(s).slice(0, 10) : "");

// One flow node in the horizontal chain.
function Node({ node, sel, onSel }) {
  const empty = !node.count;
  return html`<button class="ai-node ${sel ? "sel" : ""} ${empty ? "empty" : ""}"
      aria-selected=${String(sel)} title=${node.label + " · " + node.count}
      onClick=${() => onSel(node.id)}>
    <span class="ai-count">${node.count}</span>
    <span class="ai-lbl">${node.label}</span>
  </button>`;
}

// The connector between two nodes — animates only when the upstream node holds work to advance.
function Edge({ active }) {
  return html`<div class="ai-edge ${active ? "on" : ""}" aria-hidden="true">
    <span class="d"></span><span class="d"></span><span class="d"></span></div>`;
}

// A read-only info card for a pre-gate item (no draft / no gated action here).
function InfoCard({ item, stage }) {
  return html`<article id="detail">
    <div class="head">
      <div class="chips">
        <span class="chip id">${item.id}</span>
        ${item.kb ? html`<span class="chip ${siloClass(item.kb) === "fo" ? "fo-c" : ""}">${item.kb}</span>` : null}
        ${item.source ? html`<span class="chip">${item.source}</span>` : null}
        ${item.lane ? html`<span class="chip">${item.lane}</span>` : null}
      </div>
      <h1>${item.title || item.id}</h1>
      <p class="why">In <b>${stage}</b>${item.when ? ` · ${when(item.when)}` : ""}. This item becomes
        actionable when it reaches the gate — the pipeline advances it on the nightly run.</p>
    </div>
    ${item.payload_path ? html`
      <div class="sect"><div class="label">Captured payload</div>
        <pre class="body">${item.payload_path}</pre></div>` : null}
    <div class="verbs">
      <span class="note">Pre-gate items are shown for visibility. Nothing to decide until the
        gate — the gate stage above is where drafts wait on you.</span>
    </div>
  </article>`;
}

export function AiosView() {
  const [content, setContent] = useState(null);
  const [stage, setStage] = useState(null);      // null until content picks the first populated node
  const [detail, setDetail] = useState(null);   // {items:[…]} for the selected stage
  const [held, setHeld] = useState([]);          // rich gate items (draft_index-mapped)
  const [sel, setSel] = useState(null);          // selected item id

  const loadContent = () => api.get("/api/content").then(setContent).catch(() => {});
  useEffect(() => { loadContent(); const t = setInterval(loadContent, 5000); return () => clearInterval(t); }, []);
  // first paint: land on the first node that actually has work (never a dead, empty stage)
  useEffect(() => {
    if (stage == null && content?.nodes?.length) {
      setStage((content.nodes.find((n) => n.count > 0) || content.nodes[0]).id);
    }
  }, [content]);

  // load the drill-in for the selected stage (gate uses the rich /api/held; others the queue rows)
  const loadStage = () => {
    if (stage === "gate") {
      api.get("/api/held").then((d) => {
        const rows = (d.held || []).map((h, i) => ({ ...h, _kind: "held", draft_index: h.draft_index != null ? h.draft_index : i }));
        setHeld(rows); setDetail({ items: rows });
      }).catch(() => { setHeld([]); setDetail({ items: [] }); });
    } else {
      api.get(`/api/content/stage/${stage}`).then((d) => { setHeld([]); setDetail(d); }).catch(() => setDetail({ items: [] }));
    }
  };
  useEffect(() => { if (stage != null) { setSel(null); loadStage(); } }, [stage]);

  // gate actions — the same gated CLI wiring the Inbox uses; every write shells an allowlisted CLI
  const act = async (item, a, params = {}) => {
    try {
      if (a === "approve") { await api.post("gate_ship", { id: item.id }); loadStage(); loadContent(); }
      else if (a === "reject") { const r = window.prompt("Reject reason:"); if (!r) return; await api.post("gate_reject", { id: item.id, reason: r }); loadStage(); loadContent(); }
      else if (a === "dismiss") { const r = window.prompt("Dismiss reason:") || "below the worthiness bar"; await api.post("dismiss", { id: item.id, reason: r }); loadStage(); loadContent(); }
      else if (a === "gate_edit") { await api.post("gate_edit", { id: item.id, content: params.content }); loadStage(); loadContent(); }
      else if (REPLY[a]) { await api.post("reply", { target_id: item.item_id || item.id, reply_kind: a, text: params.text }); }
      else toast("that action isn't wired here");
    } catch (e) { /* api.post toasted */ }
  };

  const nodes = content?.nodes || [];
  const items = detail?.items || [];
  const selItem = items.find((it) => it.id === sel) || null;
  const stageLabel = nodes.find((n) => n.id === stage)?.label || "";

  return html`<section class="view ai">
    <div class="viewhead"><h1>AIOS</h1>
      <span class="sub">content pipeline · capture → sort → ingest → gate → garden</span></div>

    <div class="ai-flow">
      ${nodes.length ? nodes.map((n, i) => html`
        <${Node} node=${n} sel=${n.id === stage} onSel=${setStage} key=${n.id} />
        ${i < nodes.length - 1 ? html`<${Edge} active=${n.count > 0} key=${"e" + n.id} />` : null}
      `) : html`<p class="stub">Loading pipeline…</p>`}
    </div>
    ${content ? html`<p class="ai-life">${content.shipped} shipped · ${content.rejected} rejected · ${content.sorted} sorted, lifetime</p>` : null}

    <div class="ai-drill">
      <div class="ai-list">
        <div class="ai-listhead">${stageLabel} · <span class="num">${items.length}</span></div>
        ${items.length ? items.map((it) => html`
          <div class="ai-row ${it.id === sel ? "sel" : ""}" key=${it.id} tabindex="0"
               aria-current=${String(it.id === sel)} onClick=${() => setSel(it.id)}>
            <span class="silo ${siloClass(it.kb)}"></span>
            <span class="t"><span class="title">${it.title || it.id}</span>
              <span class="sub">${it.kb || ""}${it.source ? " · " + it.source : ""}${it.recommended ? " · rec: " + it.recommended : ""}</span></span>
            ${it.when ? html`<span class="age num">${when(it.when)}</span>` : null}
          </div>`)
          : html`<p class="stub">Nothing in ${(stageLabel || "this stage").toLowerCase()} right now.</p>`}
      </div>
      <div class="ai-detail">
        ${selItem
          ? (stage === "gate"
              ? html`<${Card} item=${selItem} station="needs_you" onAction=${(a, p) => act(selItem, a, p)} />`
              : html`<${InfoCard} item=${selItem} stage=${stageLabel} />`)
          : html`<p class="stub">Select an item to see the ${stage === "gate" ? "draft and decide" : "details"}.</p>`}
      </div>
    </div>
  </section>`;
}
