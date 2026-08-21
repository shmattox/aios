// Software — the factory pipeline as a flow graph: backlog → brainstorm → spec → implement →
// subagent builds → review → gate → complete. Click a stage to drill in (first item auto-selected,
// or the one you last had open). Each item shows its real backlog block plus any linked plan / spec
// / finding docs — all clickable into the Files editor. Reads /api/pipeline,
// /api/pipeline/stage/<id>, /api/pipeline/item.
import { html, api, useState, useEffect, useRef, useLive } from "/lib.js";
import { FlowGraph, StageList } from "./pipeflow.js";
import { openInFiles, linkifyPaths, onPathClick } from "./filenav.js";
import { remember, recall } from "./viewstate.js";
import { traceForRun, SpanTreeModal } from "/spantree.js";

const base = (p) => (p || "").split("/").pop();
const backlogPath = (repo) => (!repo || repo === "env-ops") ? "BACKLOG.md" : `Projects/${repo}/BACKLOG.md`;

function DocRef({ kind, path }) {
  return html`<button class="fileref" title=${path} onClick=${() => openInFiles(path)}>
    <span class="frk">${kind}</span><span class="frp">${base(path)}</span><span class="go">→</span></button>`;
}

function PointerCard({ item, stage }) {
  const [d, setD] = useState(null);
  useEffect(() => {
    setD(null);
    let alive = true;
    api.get(`/api/pipeline/item?repo=${encodeURIComponent(item.repo || "")}&id=${encodeURIComponent(item.id)}`)
      .then((r) => { if (alive) setD(r); }).catch(() => { if (alive) setD({ body: "", docs: [] }); });
    return () => { alive = false; };
  }, [item.id, item.repo]);
  const bl = d?.backlog_path || backlogPath(item.repo);
  const docs = d?.docs || [];
  return html`<article id="detail">
    <div class="head">
      <div class="chips">
        <span class="chip id">${item.id}</span>
        ${item.repo ? html`<span class="chip">${item.repo}</span>` : null}
        <span class="chip">${stage}</span>
      </div>
      <h1>${item.title || item.id}</h1>
    </div>
    ${(docs.length || bl) ? html`<div class="sect"><div class="label">Source${docs.length ? " & plans" : ""}</div>
      <div class="refs">
        <button class="fileref" title=${bl} onClick=${() => openInFiles(bl)}><span class="frk">backlog</span><span class="frp">${base(bl)}</span><span class="go">→</span></button>
        ${docs.map((x) => html`<${DocRef} kind=${x.kind} path=${x.path} key=${x.path} />`)}
      </div></div>` : null}
    <div class="sect grow"><div class="label">Backlog item</div>
      ${d == null ? html`<pre class="body">loading…</pre>`
        : d.body
          ? html`<pre class="body" onClick=${onPathClick} dangerouslySetInnerHTML=${{ __html: linkifyPaths(d.body) }}></pre>`
          : html`<pre class="body">(no backlog block found for ${item.id} — open ${base(bl)} to view it)</pre>`}
    </div>
    <div class="verbs">
      <span class="note">Backlog pointer — the dashboard doesn't act on these; they drain in an
        isolated worktree or a native session. Open the backlog or a linked plan above to read more.</span>
    </div>
  </article>`;
}

const toRow = (it) => ({ id: it.id, title: it.title || it.id, silo: "dev", sub: it.repo || "" });

export function SoftwareView() {
  const saved = recall("software") || {};
  const [model, setModel] = useState(null);
  const [stage, setStage] = useState(saved.stage ?? null);
  const [detail, setDetail] = useState(null);
  const [sel, setSel] = useState(null);
  const [act, setAct] = useState(null);
  const [otel, setOtel] = useState(null);
  const [spans, setSpans] = useState(null);
  const gen = useRef(0);
  const wantSel = useRef(saved.sel || null);
  const stageRef = useRef(stage);
  useEffect(() => { stageRef.current = stage; }, [stage]);

  const load = () => api.get("/api/pipeline").then(setModel).catch(() => {});
  const loadLive = () => {
    api.get("/api/activity").then((d) => setAct(d.runs || [])).catch(() => setAct([]));
    api.get("/api/otel/runs").then((d) => setOtel(d.runs || [])).catch(() => setOtel([]));
  };
  useEffect(() => { load(); }, []);
  useLive(["board", "activity", "brief"], () => { load(); if (stageRef.current != null) loadStage(); });
  useEffect(() => { loadLive(); const t = setInterval(loadLive, 12000); return () => clearInterval(t); }, []);
  useEffect(() => {
    if (stage == null && model?.stages?.length) setStage((model.stages.find((s) => s.count > 0) || model.stages[0]).id);
  }, [model]);
  useEffect(() => { if (stage != null) remember("software", { stage, sel }); }, [stage, sel]);

  const loadStage = () => {
    const g = ++gen.current;
    api.get(`/api/pipeline/stage/${stageRef.current}`).then((data) => {
      if (g !== gen.current) return;
      const items = data.items || []; setDetail({ items });
      const r = wantSel.current; wantSel.current = null;
      setSel(items.find((i) => i.id === r)?.id || items[0]?.id || null);
    }).catch(() => { if (g === gen.current) { setDetail({ items: [] }); setSel(null); } });
  };
  useEffect(() => { if (stage != null) loadStage(); }, [stage]);

  const nodes = model?.stages || [];
  const items = detail?.items || [];
  const selItem = (sel != null && items.find((it) => it.id === sel)) || null;
  const stageLabel = nodes.find((n) => n.id === stage)?.label || "";
  const done = nodes.find((n) => n.id === "complete")?.count || 0;

  const stageIds = new Set(items.map((i) => i.id));
  const liveRuns = (act || []).filter((r) => r.live && (r.item_ids || []).some((id) => stageIds.has(id)));

  return html`<section class="view ai">
    <div class="viewhead"><h1>Software</h1>
      <span class="sub">the factory · backlog → spec → build → review → gate → complete</span></div>

    <${FlowGraph} nodes=${nodes} sel=${stage} onSel=${setStage} />
    ${model ? html`<p class="ai-life">${done} complete, lifetime · auto-drained through the review gate</p>` : null}

    ${liveRuns.length ? html`<div class="ov-run-list">
      ${liveRuns.map((r) => {
        // factory run → its session twin, joined by worktree field equality (not string-matching)
        const twin = (act || []).find((s) => s.surface === "session" && s.worktree && s.worktree === r.worktree);
        const tid = traceForRun(r, otel) || traceForRun(twin, otel);
        return html`<div class="ov-run" key=${r.id}>
          <span class="badge">${(r.surface || "").slice(0, 4).toUpperCase()}</span>
          <span class="t">${r.title || r.id}</span>
          ${tid ? html`<button class="verb sp-open" title="Open the span waterfall"
              onClick=${(e) => { e.stopPropagation(); setSpans({ tid, title: r.title || r.id }); }}>spans</button>` : null}
        </div>`;
      })}
    </div>` : null}

    <div class="ai-drill">
      <${StageList} label=${stageLabel} rows=${items.map(toRow)} sel=${sel} onSel=${setSel} />
      <div class="ai-detail">
        ${selItem
          ? html`<${PointerCard} item=${selItem} stage=${stageLabel} />`
          : html`<p class="stub">No items in this stage.</p>`}
      </div>
    </div>

    ${spans ? html`<${SpanTreeModal} traceId=${spans.tid} title=${spans.title} onClose=${() => setSpans(null)} />` : null}
  </section>`;
}
