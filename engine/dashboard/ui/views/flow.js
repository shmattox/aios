// Flow view — native-OTel agent runs (Jaeger store) rendered our way: a runs list (master),
// a flow-graph of the selected run (depth-ranked columns; subagent fan-out visible), and the
// cost/token/tool/notes insight Jaeger's own UI never surfaces. Reads GET /api/otel/runs +
// GET /api/otel/run/<traceid>. Jaeger is the invisible store; this is the face.
import { html, api, useState, useEffect, useRef } from "/lib.js";

function fmtDur(ms) {
  if (!ms) return "—";
  if (ms < 1000) return ms + "ms";
  const s = ms / 1000;
  if (s < 60) return s.toFixed(1) + "s";
  const m = Math.floor(s / 60);
  return m + "m" + String(Math.round(s % 60)).padStart(2, "0") + "s";
}
function fmtTok(n) {
  n = n || 0;
  return n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n);
}
function cleanTitle(r) {
  const t = (r.title || "").replace("claude_code.", "");
  return t === "interaction" ? "session interaction" : t;
}
function nodeClass(n) {
  if (!n.ok) return "n-err";
  if (n.tool === "Agent" || n.tool === "Task") return "n-agent";     // a subagent (the fan-out)
  return ({ interaction: "n-root", llm_request: "n-llm", tool: "n-tool",
            "tool.execution": "n-exec" }[n.type] || "n-misc");
}

function FlowGraph({ data }) {
  const nodes = data.nodes || [], edges = data.edges || [];
  if (!nodes.length) return html`<p class="stub">No spans in this run.</p>`;
  const byDepth = {};
  for (const n of nodes) (byDepth[n.depth] = byDepth[n.depth] || []).push(n);
  const depths = Object.keys(byDepth).map(Number).sort((a, b) => a - b);
  const COLW = 168, ROWH = 50, PADX = 8, PADY = 8;
  const pos = {};
  depths.forEach((d, ci) => byDepth[d].forEach((n, ri) => { pos[n.id] = { x: PADX + ci * COLW, y: PADY + ri * ROWH }; }));
  const maxRows = Math.max(1, ...depths.map((d) => byDepth[d].length));
  const width = PADX * 2 + depths.length * COLW;
  const height = PADY * 2 + maxRows * ROWH;
  const NODEW = 138, NODEH = 40;
  const edgePath = (e) => {
    const a = pos[e.source], b = pos[e.target];
    if (!a || !b) return "";
    const x1 = a.x + NODEW, y1 = a.y + NODEH / 2, x2 = b.x, y2 = b.y + NODEH / 2;
    const mx = (x1 + x2) / 2;
    return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
  };
  return html`<div class="flowgraph" style="width:${width}px;height:${height}px">
    <svg class="fg-edges" width=${width} height=${height}>
      ${edges.map((e, i) => html`<path key=${i} class="fg-edge" d=${edgePath(e)} />`)}
    </svg>
    ${nodes.map((n) => html`
      <div class="fg-node ${nodeClass(n)}" key=${n.id}
           style="left:${pos[n.id].x}px;top:${pos[n.id].y}px;width:${NODEW}px"
           title="${n.op} · ${fmtDur(n.dur_ms)}${n.tokens ? " · " + n.tokens + " tok" : ""}">
        <div class="fg-lbl">${n.label || n.type}</div>
        <div class="fg-sub">${fmtDur(n.dur_ms)}${n.tokens ? " · " + fmtTok(n.tokens) : ""}${n.agent ? " · " + String(n.agent).slice(0, 6) : ""}</div>
      </div>`)}
  </div>`;
}

function Detail({ run, graph }) {
  if (!run) return html`<p class="stub">Select a run.</p>`;
  const g = graph || {};
  const cacheTot = (run.cache_w || 0) + (run.cache_r || 0);
  return html`<article id="detail" class="flowdetail">
    <div class="head">
      <div class="chips">
        <span class="chip id">${run.trace_id.slice(0, 10)}</span>
        <span class="chip">${run.model}</span>
        <span class="chip ${run.status === "error" ? "badge-blocked" : ""}">${run.status}</span>
      </div>
      <h1>${cleanTitle(run)}</h1>
      <p class="why">
        <span class="num">${fmtDur(run.duration_ms)}</span> · ${run.span_count} spans ·
        ${run.tool_count} tools · <b>${run.agent_count}</b> subagents
        ${run.errors ? html` · <span class="fg-errtext">${run.errors} error(s)</span>` : null}
      </p>
    </div>

    <div class="sect"><div class="label">Flow</div>
      <div class="fg-scroll"><${FlowGraph} data=${g} /></div>
    </div>

    <div class="sect"><div class="label">Cost &amp; tokens</div>
      <div class="fg-cost">
        <div class="vt"><div class="vl">Cost</div><div class="vn"><small>$</small>${run.cost_usd.toFixed(3)}</div></div>
        <div class="vt"><div class="vl">In</div><div class="vn">${fmtTok(run.in_tokens)}</div></div>
        <div class="vt"><div class="vl">Out</div><div class="vn">${fmtTok(run.out_tokens)}</div></div>
        <div class="vt"><div class="vl">Cache</div><div class="vn">${fmtTok(cacheTot)}</div></div>
      </div>
    </div>

    ${(run.top_tools || []).length ? html`
      <div class="sect"><div class="label">Tools used</div>
        <div class="chips">${run.top_tools.map((t) => html`<span class="chip" key=${t.name}>${t.name} <span class="num">×${t.n}</span></span>`)}</div>
      </div>` : null}
  </article>`;
}

export function FlowView() {
  const [data, setData] = useState(null);
  const [sel, setSel] = useState(null);
  const [graph, setGraph] = useState(null);
  const selRef = useRef(null); selRef.current = sel;

  const load = () => api.get("/api/otel/runs").then(setData).catch(() => setData({ runs: [], agg: { jaeger_up: false } }));
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, []);
  useEffect(() => { if (data && data.runs && data.runs.length && sel == null) setSel(data.runs[0].trace_id); }, [data]);
  useEffect(() => {
    if (!sel) { setGraph(null); return; }
    api.get("/api/otel/run/" + sel).then(setGraph).catch(() => setGraph(null));
  }, [sel]);

  if (data == null) return html`<section class="view"><p class="stub">Loading runs…</p></section>`;
  const runs = data.runs || [], agg = data.agg || {};
  const selRun = runs.find((r) => r.trace_id === sel) || null;

  return html`<section class="view">
    <div class="viewhead"><h1>Flow</h1>
      <span class="sub">agent runs · native OpenTelemetry</span></div>

    <div class="fg-strip">
      <div class="s"><span class="sl">Runs</span><span class="sn">${agg.runs ?? 0}</span></div>
      <div class="s"><span class="sl">Tokens</span><span class="sn">${fmtTok(agg.tokens)}</span></div>
      <div class="s"><span class="sl">Cost</span><span class="sn"><small>$</small>${(agg.cost_usd ?? 0).toFixed(2)}</span></div>
      <div class="s ${agg.errors ? "hot" : ""}"><span class="sl">Errors</span><span class="sn">${agg.errors ?? 0}</span></div>
      ${agg.jaeger_up === false ? html`<div class="s warn"><span class="sl">telemetry store</span><span class="sn">down</span></div>` : null}
    </div>

    ${runs.length === 0
      ? html`<p class="stub">No agent runs captured yet. New sessions, subagent fan-outs, Workflows, and unattended <code>claude -p</code> runs appear here as they run.${agg.jaeger_up === false ? " (The local telemetry store isn't reachable — is the jaeger container up?)" : ""}</p>`
      : html`<div id="inbox">
          <div id="cards">
            ${runs.map((r) => html`
              <div class="card-row" key=${r.trace_id} tabindex="0" aria-current=${String(r.trace_id === sel)}
                   onClick=${() => setSel(r.trace_id)}>
                <span class="silo ${r.status === "error" ? "dev" : "per"}"></span>
                <span class="t">
                  <span class="title">${cleanTitle(r)}</span>
                  <span class="sub">${r.model} · ${fmtDur(r.duration_ms)} · ${fmtTok(r.total_tokens)} tok · $${r.cost_usd.toFixed(2)}${r.agent_count ? " · " + r.agent_count + " ⑃" : ""}</span>
                </span>
                <span class="age num">$${r.cost_usd.toFixed(2)}</span>
              </div>`)}
          </div>
          <div><${Detail} run=${selRun} graph=${graph} /></div>
        </div>`}
  </section>`;
}
