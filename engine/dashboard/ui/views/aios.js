// AIOS — the content pipeline as a flow graph: capture → sort → ingest → gate → garden.
// Click a stage to drill into its items (the first is auto-selected). The gate stage opens the
// rich card (Ship / Reject / preview); pre-gate stages open a detail card that pulls the item's
// real source file and links every path to the Files page. Reads /api/content,
// /api/content/stage/<id>, /api/held, /api/files/read.
import { html, api, useState, useEffect, useRef, useLive, toast } from "/lib.js";
import { Card, siloClass } from "./card.js";
import { FlowGraph, StageList } from "./pipeflow.js";
import { openInFiles, toEnvFile, linkifyPaths, onPathClick } from "./filenav.js";
import { remember, recall } from "./viewstate.js";

const REPLY = { respond: 1, append: 1, comment: 1 };
const when = (s) => (s ? String(s).slice(0, 10) : "");
const short = (p) => { const a = String(p || "").replace(/\\/g, "/").split("/"); return a.length > 4 ? "…/" + a.slice(-3).join("/") : a.join("/"); };
const toEnvPath = toEnvFile;

function FileRef({ label, path }) {
  return html`<button class="fileref" title=${path} onClick=${() => openInFiles(path)}>
    <span class="frk">${label}</span><span class="frp">${short(path)}</span><span class="go">→</span></button>`;
}

// Rich read-only card for a pre-gate item: metadata, source-file links (→ Files), and the file body.
function InfoCard({ item, stage, vaultRel }) {
  const [body, setBody] = useState(null);
  const files = [];
  const pay = toEnvPath(vaultRel, item.payload_path);
  const draft = toEnvPath(vaultRel, item.draft_path);
  if (draft) files.push({ label: "draft", path: draft });
  if (pay) files.push({ label: "captured", path: pay });
  const src = draft || pay;
  useEffect(() => {
    setBody(null);
    if (!src) return;
    let alive = true;
    api.get(`/api/files/read?path=${encodeURIComponent(src)}`)
      .then((d) => { if (alive) setBody(d.error ? { err: d.error } : { text: d.content }); })
      .catch(() => { if (alive) setBody({ err: "could not read source" }); });
    return () => { alive = false; };
  }, [src]);
  return html`<article id="detail">
    <div class="head">
      <div class="chips">
        <span class="chip id">${item.id}</span>
        ${item.kb ? html`<span class="chip ${siloClass(item.kb) === "fo" ? "fo-c" : ""}">${item.kb}</span>` : null}
        ${item.source ? html`<span class="chip">${item.source}</span>` : null}
        ${item.lane ? html`<span class="chip">${item.lane}</span>` : null}
        <span class="chip">${stage}</span>
        ${item.recommended ? html`<span class="chip">rec: ${item.recommended}</span>` : null}
      </div>
      <h1>${item.title || item.id}</h1>
      <p class="why">In <b>${stage}</b>${item.when ? ` · ${when(item.when)}` : ""}. ${draft
        ? "Drafted and waiting to advance to the gate."
        : "Captured and queued — it gets drafted, then lands at the gate for your decision."}</p>
    </div>
    ${files.length ? html`<div class="sect"><div class="label">Source files</div>
      <div class="refs">${files.map((f) => html`<${FileRef} label=${f.label} path=${f.path} key=${f.path} />`)}</div></div>` : null}
    ${src ? html`<div class="sect"><div class="label">${draft ? "Draft" : "Captured payload"}</div>
      ${body == null ? html`<pre class="body">loading…</pre>`
        : body.err ? html`<pre class="body">(${body.err})</pre>`
        : html`<pre class="body" onClick=${onPathClick} dangerouslySetInnerHTML=${{ __html: linkifyPaths(body.text, vaultRel) }}></pre>`}</div>` : null}
    <div class="verbs">
      <span class="note">Pre-gate — nothing to decide yet. Open any source file in the editor with
        the links above; the gate stage is where drafts wait on you.</span>
    </div>
  </article>`;
}

const toRow = (it) => ({
  id: it.id, title: it.title || it.id, silo: siloClass(it.kb),
  sub: `${it.kb || ""}${it.source ? " · " + it.source : ""}${it.recommended ? " · rec: " + it.recommended : ""}`,
  when: when(it.when),
});

export function AiosView() {
  const saved = recall("aios") || {};
  const [content, setContent] = useState(null);
  const [stage, setStage] = useState(saved.stage ?? null);
  const [detail, setDetail] = useState(null);
  const [sel, setSel] = useState(null);
  const gen = useRef(0);
  const wantSel = useRef(saved.sel || null);   // item to restore on the first stage load (back-nav)

  const loadContent = () => api.get("/api/content").then(setContent).catch(() => {});
  useEffect(() => { loadContent(); }, []);
  useLive(["queue", "brief"], loadContent);
  useEffect(() => {
    if (stage == null && content?.nodes?.length) setStage((content.nodes.find((n) => n.count > 0) || content.nodes[0]).id);
  }, [content]);
  useEffect(() => { if (stage != null) remember("aios", { stage, sel }); }, [stage, sel]);

  const loadStage = () => {
    const g = ++gen.current;
    const done = (items) => { if (g !== gen.current) return; setDetail({ items });
      const r = wantSel.current; wantSel.current = null;
      setSel(items.find((i) => i.id === r)?.id || items[0]?.id || null); };
    if (stage === "gate") {
      api.get("/api/held").then((d) => done((d.held || []).map((h, i) => ({ ...h, _kind: "held", draft_index: h.draft_index != null ? h.draft_index : i }))))
        .catch(() => { if (g === gen.current) { setDetail({ items: [] }); setSel(null); } });
    } else {
      api.get(`/api/content/stage/${stage}`).then((d) => done(d.items || []))
        .catch(() => { if (g === gen.current) { setDetail({ items: [] }); setSel(null); } });
    }
  };
  useEffect(() => { if (stage != null) loadStage(); }, [stage]);

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
  const selItem = (sel != null && items.find((it) => it.id === sel)) || null;
  const stageLabel = nodes.find((n) => n.id === stage)?.label || "";
  const isGate = stage === "gate";

  return html`<section class="view ai">
    <div class="viewhead"><h1>AIOS</h1>
      <span class="sub">content pipeline · capture → sort → ingest → gate → garden</span></div>

    <${FlowGraph} nodes=${nodes} sel=${stage} onSel=${setStage} />
    ${content ? html`<p class="ai-life">${content.shipped} shipped · ${content.rejected} rejected · ${content.sorted} sorted, lifetime</p>` : null}

    <div class="ai-drill">
      <${StageList} label=${stageLabel} rows=${items.map(toRow)} sel=${sel} onSel=${setSel} />
      <div class="ai-detail">
        ${selItem
          ? (isGate
              ? html`<${Card} item=${selItem} station="needs_you" vaultRel=${content?.vault_rel} onAction=${(a, p) => act(selItem, a, p)} />`
              : html`<${InfoCard} item=${selItem} stage=${stageLabel} vaultRel=${content?.vault_rel} />`)
          : html`<p class="stub">No items in this stage.</p>`}
      </div>
    </div>
  </section>`;
}
