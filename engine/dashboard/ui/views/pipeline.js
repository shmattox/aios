// Pipeline cockpit — the factory as a live execution-flow graph, native to the dashboard
// (no iframe, no build). Metrics strip + hand-drawn SVG flow graph + a working feed that
// SCOPES to whichever stage you click. Reads /api/pipeline, /api/pipeline/stage/<id>,
// /api/activity[/<id>/log], /api/git. Refreshes on the SSE `activity` signal (useLive) + polls.
import { html, api, useState, useEffect, useRef, useLive } from "/lib.js";

const STAGES = [
  ["backlog", "Backlog"], ["brainstorm", "Brainstorm"], ["spec", "Spec"], ["implement", "Implement"],
  ["subagent", "Subagent"], ["review", "Review"], ["gate", "Gate"], ["complete", "Complete"],
];
const SURFACE = { factory: "FAC", pipeline: "PIPE", session: "SESS", goal: "GOAL", workflow: "FLOW" };

const lastTs = (r) => Math.max(r.ended || 0, r.heartbeat || 0, r.started || 0);
function ago(sec) {
  if (sec == null || sec < 0) return "";
  if (sec < 60) return Math.round(sec) + "s";
  if (sec < 3600) return Math.round(sec / 60) + "m";
  if (sec < 86400) return Math.round(sec / 3600) + "h";
  return Math.round(sec / 86400) + "d";
}
// stale = record still says "running" but neither pid nor heartbeat is fresh — rendered loud,
// never conflated with a genuinely live run (the activity contract's liveness invariant).
function runClass(r) {
  if (r.status === "running") return r.live ? "run" : "stale";
  if (r.live) return "run";
  if (r.status === "shipped" || r.status === "ended") return "ok";
  if (r.status === "failed") return "bad";
  if (r.status === "parked" || r.pending_approval) return "warn";
  return "idle";
}
function runStatusText(r) {
  if (r.status === "running") return r.live ? "running" : "stale";
  return r.status || "—";
}
function stageKind(id) { return id === "gate" ? "gate" : id === "complete" ? "done" : null; }
const fmtUsd = (n) => "$" + (n || 0).toFixed(2);
function fmtTok(n) { if (!n) return "0"; if (n >= 1e6) return (n / 1e6).toFixed(1) + "M"; if (n >= 1e3) return (n / 1e3).toFixed(1) + "k"; return String(n); }
const sum = (a, f) => a.reduce((s, r) => s + (f(r) || 0), 0);
const startOfTodaySec = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d.getTime() / 1000; };

// ── metrics strip ─────────────────────────────────────────────
function Metrics({ runs, gate, fresh }) {
  const today = startOfTodaySec();
  const tr = runs.filter((r) => lastTs(r) >= today);
  const cells = [
    { k: "spend · today", v: fmtUsd(sum(tr, (r) => r.cost_usd)), sub: fmtUsd(sum(runs, (r) => r.cost_usd)) + " all" },
    { k: "tokens · today", v: fmtTok(sum(tr, (r) => r.tokens)), sub: fmtTok(sum(runs, (r) => r.tokens)) + " all" },
    { k: "drains live", v: String(runs.filter((r) => r.surface === "factory" && r.live).length), sub: "factory" },
    { k: "ships · today", v: String(tr.filter((r) => r.status === "shipped").length), sub: "shipped", cls: "ok" },
    { k: "gate depth", v: String(gate), sub: "awaiting you", cls: gate ? "warn" : "" },
  ];
  return html`<div class="pv-metrics">
    ${cells.map((c) => html`<div class="pv-mcell" key=${c.k}>
      <div class="pv-mk">${c.k}</div>
      <div class="pv-mrow"><div class="pv-mv ${c.cls || ""}">${c.v}</div><div class="pv-msub">${c.sub}</div></div>
    </div>`)}
    ${fresh ? html`<div class="pv-fresh"><span class="pv-pulse"></span>updated ${fresh} ago</div>` : null}
  </div>`;
}

// ── flow graph: HTML stages + a measured SVG edge overlay ──────
function FlowGraph({ stages, flows, scope, onPick }) {
  const wrapRef = useRef(null);
  const [edges, setEdges] = useState([]);
  const maxc = Math.max(1, ...stages.map((s) => s.count));
  const active = new Set((flows || []).map((f) => f.from + ">" + f.to));

  useEffect(() => {
    const draw = () => {
      const wrap = wrapRef.current; if (!wrap) return;
      const box = wrap.getBoundingClientRect();
      const nodes = [...wrap.querySelectorAll(".pv-stage")];
      const out = [];
      for (let i = 0; i < nodes.length - 1; i++) {
        const a = nodes[i].getBoundingClientRect(), b = nodes[i + 1].getBoundingClientRect();
        const x1 = a.right - box.left - 3, x2 = b.left - box.left + 3, y = a.top - box.top + 40;
        const mid = (x1 + x2) / 2;
        out.push({ d: `M${x1} ${y} C${mid} ${y}, ${mid} ${y}, ${x2} ${y}`,
          w: 1 + ((stages[i] && stages[i].count || 0) / maxc) * 5,
          live: active.has((stages[i] && stages[i].id) + ">" + (stages[i + 1] && stages[i + 1].id)),
          dur: (0.9 + i * 0.12).toFixed(2) });
      }
      setEdges(out);
    };
    draw();
    window.addEventListener("resize", draw);
    return () => window.removeEventListener("resize", draw);
  }, [stages, scope]);

  return html`<div class="pv-flow" ref=${wrapRef}>
    <svg class="pv-edges" preserveAspectRatio="none">
      ${edges.map((e, i) => html`<g key=${i}>
        <path d=${e.d} stroke="var(--border-hi)" stroke-width=${e.w.toFixed(1)} fill="none" />
        <path d=${e.d} stroke="var(--text-0)" stroke-width=${Math.min(1.6, e.w).toFixed(1)} fill="none"
              stroke-dasharray="2 12" stroke-linecap="round" opacity=${e.live ? "0.85" : "0.4"}>
          <animate attributeName="stroke-dashoffset" from="14" to="0" dur=${e.dur + "s"} repeatCount="indefinite" />
        </path>
      </g>`)}
    </svg>
    <div class="pv-stages">
      ${stages.map((s) => {
        const live = s.count > 0 && ["implement", "subagent", "review"].includes(s.id);
        const kind = stageKind(s.id);
        return html`<div class="pv-stage ${kind || ""} ${live ? "live" : ""} ${s.count ? "" : "dim"} ${scope === s.id ? "sel" : ""}"
             key=${s.id} tabindex="0" role="button" aria-pressed=${String(scope === s.id)}
             onClick=${() => onPick(s.id)} onKeyDown=${(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPick(s.id); } }}>
          <span class="pv-ring"></span>
          <span class="pv-dot"></span>
          <div class="pv-count">${s.count}</div>
          <div class="pv-bar"><i style=${`width:${Math.max(4, Math.round((s.count / maxc) * 100))}%`}></i></div>
          <div class="pv-lbl">${s.label}</div>
        </div>`;
      })}
    </div>
  </div>`;
}

// ── feed: activity / worktrees / prs, with stage scoping + inline expand ──
function Feed({ mode, setMode, scope, scopeInfo, clearScope, activity, git, stageItems, runByItem, expanded, setExpanded, logs }) {
  const wt = git.worktrees || [];
  const prs = git.prs || { items: [], loading: false };
  const vault = git.vault || [];
  const extras = wt.filter((w) => !w.primary).length;

  return html`<div class="pv-feed">
    <div class="pv-fhead">
      <div class="pv-tabs">
        ${[
          { m: "activity", lbl: "Activity" },
          { m: "worktrees", lbl: "Worktrees", n: extras, title: `${extras} active drain${extras === 1 ? "" : "s"} of ${wt.length} worktrees` },
          { m: "prs", lbl: "PRs", n: prs.items.length, title: "open pull requests" },
          { m: "vault", lbl: "Vault", n: vault.filter((v) => !v.auto).length, title: "recent secondbrain writes (excludes auto-sync)" },
        ].map(({ m, lbl, n, title }) =>
          html`<button class="pv-tab ${mode === m ? "on" : ""}" key=${m} title=${title || ""} onClick=${() => setMode(m)}>
            ${lbl}${n ? html`<span class="pv-tn">${n}</span>` : null}</button>`)}
      </div>
      ${scopeInfo && mode === "activity"
        ? html`<div class="pv-scope">viewing <b>${scopeInfo.label}</b> · ${scopeInfo.count} items <span class="pv-clr" onClick=${clearScope}>clear ✕</span></div>`
        : null}
    </div>
    <div class="pv-fbody">
      ${mode === "activity"
        ? (scope
            ? StageItems({ items: stageItems, runByItem, expanded, setExpanded, logs })
            : ActivityRows({ runs: activity.runs, now: activity.now, expanded, setExpanded, logs }))
        : mode === "worktrees" ? WorktreeRows({ wt })
        : mode === "prs" ? PrRows({ prs })
        : VaultRows({ vault, now: activity.now })}
    </div>
  </div>`;
}

function LogBlock({ log }) {
  return html`<div class="pv-exp">
    <pre class="pv-log">${log == null ? "loading…" : log.error ? "log unavailable" : !log.available || !(log.lines || []).length ? "no log output" : log.lines.join("\n")}</pre>
  </div>`;
}

function ActivityRows({ runs, now, expanded, setExpanded, logs }) {
  const sorted = [...runs].sort((a, b) => lastTs(b) - lastTs(a)).slice(0, 80);
  if (!sorted.length) return html`<div class="pv-empty">No activity recorded yet.</div>`;
  return sorted.map((r) => {
    const cls = runClass(r), open = expanded === r.id;
    return html`<div key=${r.id}>
      <div class="pv-row ${cls} ${open ? "open" : ""}" tabindex="0" onClick=${() => setExpanded(open ? null : r.id)}>
        <span class="pv-badge">${SURFACE[r.surface] || r.surface}</span>
        <div class="pv-rmain"><div class="pv-rt">${r.title || r.id}</div>
          <div class="pv-rm">${r.repo ? html`<span class="repo">${r.repo}</span>` : null}
            ${r.item_ids && r.item_ids.length ? html`<span>${r.item_ids.join(" ")}</span>` : null}
            ${r.cost_usd ? html`<span>${fmtUsd(r.cost_usd)}</span>` : null}</div></div>
        <div class="pv-rr"><span class="pv-st ${cls}">${r.live ? html`<span class="d"></span>` : null}${runStatusText(r)}</span>
          <span class="pv-tm">${ago(now - lastTs(r))}</span></div>
      </div>
      ${open ? html`${r.detail ? html`<div class="pv-exp"><p class="pv-detail">${r.detail}</p></div>` : null}${LogBlock({ log: logs[r.id] })}` : null}
    </div>`;
  });
}

function StageItems({ items, runByItem, expanded, setExpanded, logs }) {
  if (items == null) return html`<div class="pv-empty">Loading…</div>`;
  if (!items.length) return html`<div class="pv-empty">No items in this stage.</div>`;
  return items.map((it) => {
    const run = runByItem.get(it.id), open = run && expanded === it.id;
    return html`<div key=${it.id}>
      <div class="pv-row ${run ? "run link" : ""}" tabindex=${run ? "0" : null}
           onClick=${run ? () => setExpanded(open ? null : it.id) : undefined}>
        <span class="pv-badge">${it.id}</span>
        <div class="pv-rmain"><div class="pv-rt">${it.title}</div>
          <div class="pv-rm">${it.repo ? html`<span class="repo">${it.repo}</span>` : null}</div></div>
        <div class="pv-rr">${run ? html`<span class="pv-st run"><span class="d"></span>run</span>` : html`<span class="pv-tm">→</span>`}</div>
      </div>
      ${open ? LogBlock({ log: logs[run.id] }) : null}
    </div>`;
  });
}

function WorktreeRows({ wt }) {
  if (!wt.length) return html`<div class="pv-empty">No worktrees found.</div>`;
  return wt.map((w, i) => html`<div class="pv-row ${w.primary ? "ok" : "run"}" key=${w.repo + w.path + i}>
    <span class="pv-badge">${w.repo}</span>
    <div class="pv-rmain"><div class="pv-rt">${w.branch || "(no branch)"}${w.locked ? " · locked" : ""}</div>
      <div class="pv-rm"><span>${w.path}</span></div></div>
    <div class="pv-rr"><span class="pv-st ${w.primary ? "" : "run"}">${w.primary ? "main" : "drain"}</span><span class="pv-tm">${w.head}</span></div>
  </div>`);
}

function PrRows({ prs }) {
  if (prs.loading && !prs.items.length) return html`<div class="pv-empty">Loading PRs…</div>`;
  if (!prs.items.length) return html`<div class="pv-empty">No open PRs.</div>`;
  return prs.items.map((p) => html`<a class="pv-row ${p.draft ? "warn" : "ok"}" key=${p.repo + "#" + p.number}
      href=${p.url} target="_blank" rel="noopener noreferrer">
    <span class="pv-badge">${(p.repo || "").slice(0, 4)}</span>
    <div class="pv-rmain"><div class="pv-rt">#${p.number} ${p.title}</div>
      <div class="pv-rm"><span class="repo">${p.branch}</span></div></div>
    <div class="pv-rr"><span class="pv-st ${p.draft ? "warn" : "ok"}">${p.draft ? "draft" : (p.state || "open").toLowerCase()}</span><span class="pv-tm">↗</span></div>
  </a>`);
}

function VaultRows({ vault, now }) {
  if (!vault.length) return html`<div class="pv-empty">No recent vault writes.</div>`;
  return vault.map((v) => html`<div class="pv-row ${v.auto ? "ok" : "run"}" key=${v.hash}>
    <span class="pv-badge">${(v.hash || "").slice(0, 7)}</span>
    <div class="pv-rmain"><div class="pv-rt">${v.subject}</div>
      <div class="pv-rm"><span>${v.auto ? "auto-sync" : "authored"}</span></div></div>
    <div class="pv-rr"><span class="pv-tm">${v.ts ? ago((now || Date.now() / 1000) - v.ts) : ""}</span></div>
  </div>`);
}

// ── the view ──────────────────────────────────────────────────
export function PipelineView() {
  const [model, setModel] = useState(null);
  const [activity, setActivity] = useState({ runs: [], now: 0 });
  const [git, setGit] = useState({ worktrees: [], prs: { items: [], loading: true }, vault: [] });
  const [mode, setMode] = useState("activity");
  const [scope, setScope] = useState(null);         // stage id
  const [stageItems, setStageItems] = useState(null);
  const [expanded, setExpanded] = useState(null);   // run id / item id with log open
  const [logs, setLogs] = useState({});
  const [lastSync, setLastSync] = useState(0);      // epoch-s of the last successful poll
  const [clientNow, setClientNow] = useState(Date.now() / 1000);

  const loadModel = () => api.get("/api/pipeline").then(setModel).catch(() => {});
  const loadActivity = () => api.get("/api/activity")
    .then((d) => { setActivity({ runs: d.runs || [], now: d._now || 0 }); setLastSync(Date.now() / 1000); }).catch(() => {});
  const loadGit = () => api.get("/api/git")
    .then((d) => setGit({ worktrees: d.worktrees || [], prs: d.prs || { items: [], loading: false }, vault: d.vault || [] })).catch(() => {});

  useEffect(() => { loadModel(); loadActivity(); loadGit(); }, []);
  useEffect(() => { const t = setInterval(() => { loadModel(); loadActivity(); }, 4000); return () => clearInterval(t); }, []);
  useEffect(() => { const t = setInterval(loadGit, 10000); return () => clearInterval(t); }, []);
  useEffect(() => { const t = setInterval(() => setClientNow(Date.now() / 1000), 1000); return () => clearInterval(t); }, []);

  // stage drill-in: fetch the scoped stage's full item list
  useEffect(() => {
    if (!scope) { setStageItems(null); return; }
    let alive = true; setStageItems(null);
    const load = () => api.get(`/api/pipeline/stage/${encodeURIComponent(scope)}`).then((d) => { if (alive) setStageItems(d.items || []); }).catch(() => {});
    load(); const t = setInterval(load, 4000);
    return () => { alive = false; clearInterval(t); };
  }, [scope]);

  // resolve the expanded row to a concrete run id (a scoped item maps to its factory run), fresh
  // each render so a superseding run is followed rather than polling the dead one.
  const runByItem = buildRunByItem(activity.runs);
  const expandedRunId = expanded
    ? (activity.runs.some((r) => r.id === expanded) ? expanded : (runByItem.get(expanded) || {}).id)
    : null;

  // fetch (and keep polling) the log for the expanded row
  useEffect(() => {
    if (!expandedRunId) return;
    let alive = true;
    const load = () => api.get(`/api/activity/${encodeURIComponent(expandedRunId)}/log?tail=200`)
      .then((d) => { if (alive) setLogs((m) => ({ ...m, [expandedRunId]: d })); })
      .catch(() => { if (alive) setLogs((m) => ({ ...m, [expandedRunId]: m[expandedRunId] || { error: true } })); });
    load(); const t = setInterval(load, 4000);
    return () => { alive = false; clearInterval(t); };
  }, [expandedRunId]);

  // SSE: refresh model + activity the instant the factory changes anything (polls are the fallback).
  useLive(["activity"], () => { loadActivity(); loadModel(); });

  const pickStage = (id) => { setMode("activity"); setExpanded(null); setScope((cur) => (cur === id ? null : id)); };
  const clearScope = () => { setScope(null); setExpanded(null); };
  const switchMode = (m) => { setMode(m); setExpanded(null); if (m !== "activity") setScope(null); };

  const stages = (model?.stages) || STAGES.map(([id, label]) => ({ id, label, count: 0 }));
  const gate = stages.find((s) => s.id === "gate")?.count ?? 0;
  const scoped = scope ? stages.find((s) => s.id === scope) : null;
  const scopeInfo = scope ? { label: (scoped && scoped.label) || scope, count: (scoped && scoped.count) ?? 0 } : null;

  return html`<div class="pv-cockpit">
    <${Metrics} runs=${activity.runs} gate=${gate} fresh=${lastSync ? ago(clientNow - lastSync) : null} />
    <${FlowGraph} stages=${stages} flows=${model?.flows} scope=${scope} onPick=${pickStage} />
    <${Feed} mode=${mode} setMode=${switchMode} scope=${scope} scopeInfo=${scopeInfo} clearScope=${clearScope}
             activity=${activity} git=${git} stageItems=${stageItems} runByItem=${runByItem}
             expanded=${expanded} setExpanded=${setExpanded} logs=${logs} />
  </div>`;
}

// item_id -> most-recent run (ascending sort => last write wins)
function buildRunByItem(runs) {
  const m = new Map();
  for (const r of [...runs].sort((a, b) => (a.started || 0) - (b.started || 0)))
    for (const iid of r.item_ids || []) m.set(iid, r);
  return m;
}
