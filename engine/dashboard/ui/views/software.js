// Software — the factory pipeline as a flow graph: backlog → brainstorm → spec → implement →
// subagent builds → review → gate → complete. Click a stage to drill into its items. Factory
// items are backlog pointers: the dashboard doesn't act on them (they drain in a worktree or a
// native session), so every item opens a read-only pointer card. Reads /api/pipeline +
// /api/pipeline/stage/<id>.
import { html, api, useState, useEffect, useRef } from "/lib.js";
import { FlowGraph, StageList } from "./pipeflow.js";

// A read-only pointer card for a factory item.
function PointerCard({ item, stage }) {
  return html`<article id="detail">
    <div class="head">
      <div class="chips">
        <span class="chip id">${item.id}</span>
        ${item.repo ? html`<span class="chip">${item.repo}</span>` : null}
        <span class="chip">${stage}</span>
      </div>
      <h1>${item.title || item.id}</h1>
      <p class="why">In <b>${stage}</b>${item.repo ? ` · ${item.repo}` : ""}. A factory backlog item —
        the dashboard doesn't act on these; they drain in an isolated worktree or a native session.</p>
    </div>
    <div class="verbs">
      <span class="note">Backlog pointer. Open the repo's BACKLOG.md to see the full item, then work
        it in a native session — or let the factory drain it.</span>
    </div>
  </article>`;
}

const toRow = (it) => ({ id: it.id, title: it.title || it.id, silo: "dev", sub: it.repo || "" });

export function SoftwareView() {
  const [model, setModel] = useState(null);
  const [stage, setStage] = useState(null);
  const [detail, setDetail] = useState(null);
  const [sel, setSel] = useState(null);
  const gen = useRef(0);                          // request generation — stale stage loads are dropped

  const load = () => api.get("/api/pipeline").then(setModel).catch(() => {});
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, []);
  useEffect(() => {
    if (stage == null && model?.stages?.length) {
      setStage((model.stages.find((s) => s.count > 0) || model.stages[0]).id);
    }
  }, [model]);

  const loadStage = () => {
    const g = ++gen.current;                       // invalidate any in-flight load for a prior stage
    api.get(`/api/pipeline/stage/${stage}`)
      .then((d) => { if (g === gen.current) setDetail(d); })
      .catch(() => { if (g === gen.current) setDetail({ items: [] }); });
  };
  useEffect(() => { if (stage != null) { setSel(null); loadStage(); } }, [stage]);

  const nodes = model?.stages || [];
  const items = detail?.items || [];
  const selItem = (sel != null && items.find((it) => it.id === sel)) || null;
  const stageLabel = nodes.find((n) => n.id === stage)?.label || "";
  const done = nodes.find((n) => n.id === "complete")?.count || 0;

  return html`<section class="view ai">
    <div class="viewhead"><h1>Software</h1>
      <span class="sub">the factory · backlog → spec → build → review → gate → complete</span></div>

    <${FlowGraph} nodes=${nodes} sel=${stage} onSel=${setStage} />
    ${model ? html`<p class="ai-life">${done} complete, lifetime · auto-drained through the review gate</p>` : null}

    <div class="ai-drill">
      <${StageList} label=${stageLabel} rows=${items.map(toRow)} sel=${sel} onSel=${setSel} />
      <div class="ai-detail">
        ${selItem
          ? html`<${PointerCard} item=${selItem} stage=${stageLabel} />`
          : html`<p class="stub">Select an item to see the pointer.</p>`}
      </div>
    </div>
  </section>`;
}
