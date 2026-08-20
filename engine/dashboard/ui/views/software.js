// Software — the factory pipeline as a flow graph: backlog → brainstorm → spec → implement →
// subagent builds → review → gate → complete. Click a stage to drill in (first item auto-selected).
// Factory items are backlog pointers, so each opens a read-only card that links to the repo's
// BACKLOG.md in the Files editor. Reads /api/pipeline + /api/pipeline/stage/<id>.
import { html, api, useState, useEffect, useRef } from "/lib.js";
import { FlowGraph, StageList } from "./pipeflow.js";
import { openInFiles } from "./filenav.js";

// the env-relative BACKLOG.md for a repo (env-ops lives at the env root)
const backlogPath = (repo) => (!repo || repo === "env-ops") ? "BACKLOG.md" : `Projects/${repo}/BACKLOG.md`;

function PointerCard({ item, stage }) {
  const bl = backlogPath(item.repo);
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
    <div class="sect"><div class="label">Source</div>
      <div class="refs"><button class="fileref" title=${bl} onClick=${() => openInFiles(bl)}>
        <span class="frk">backlog</span><span class="frp">${bl}</span><span class="go">→</span></button></div></div>
    <div class="verbs">
      <span class="note">Backlog pointer. Open ${bl} to see the full item, then work it in a native
        session — or let the factory drain it.</span>
    </div>
  </article>`;
}

const toRow = (it) => ({ id: it.id, title: it.title || it.id, silo: "dev", sub: it.repo || "" });

export function SoftwareView() {
  const [model, setModel] = useState(null);
  const [stage, setStage] = useState(null);
  const [detail, setDetail] = useState(null);
  const [sel, setSel] = useState(null);
  const gen = useRef(0);

  const load = () => api.get("/api/pipeline").then(setModel).catch(() => {});
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, []);
  useEffect(() => {
    if (stage == null && model?.stages?.length) setStage((model.stages.find((s) => s.count > 0) || model.stages[0]).id);
  }, [model]);

  const loadStage = () => {
    const g = ++gen.current;
    api.get(`/api/pipeline/stage/${stage}`)
      .then((d) => { if (g !== gen.current) return; const items = d.items || []; setDetail({ items }); setSel(items[0]?.id || null); })
      .catch(() => { if (g === gen.current) { setDetail({ items: [] }); setSel(null); } });
  };
  useEffect(() => { if (stage != null) loadStage(); }, [stage]);

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
          : html`<p class="stub">No items in this stage.</p>`}
      </div>
    </div>
  </section>`;
}
