// Overview — the landing page: both pipelines at a glance, dev servers, and what's running.
// Reads /api/pipeline (factory), /api/content (AIOS pipeline), /api/servers, /api/activity.
// (Spend is derived from the activity runs' cost_usd, not a separate endpoint.)
import { html, api, useState, useEffect, toast } from "/lib.js";

const fmtUsd = (n) => "$" + (Number(n) || 0).toFixed(2);
const lastTs = (r) => Math.max(r.ended || 0, r.heartbeat || 0, r.started || 0);
const startOfTodaySec = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d.getTime() / 1000; };
const sum = (a, f) => a.reduce((s, r) => s + (Number(f(r)) || 0), 0);

function Metric({ k, v, sub, cls }) {
  return html`<div class="ov-cell"><div class="ov-k">${k}</div>
    <div class="ov-v ${cls || ""}">${v}</div><div class="ov-s">${sub || ""}</div></div>`;
}

// parse a Claude Code transcript (JSONL) into readable user/assistant turns
function parseThread(lines) {
  const out = [];
  for (const ln of lines) {
    let d; try { d = JSON.parse(ln); } catch (e) { continue; }
    const m = d.message;
    if (!m || !m.role) continue;
    const content = Array.isArray(m.content) ? m.content : (m.content != null ? [{ type: "text", text: m.content }] : []);
    let text = "", tools = [];
    for (const c of content) {
      if (c.type === "text" && c.text) text += c.text;
      else if (c.type === "tool_use" && c.name) tools.push(c.name);
    }
    text = text.trim();
    if (text || tools.length) out.push({ role: m.role, text: text.slice(0, 1600), tools });
  }
  return out;
}

// read-only viewer of a running session's live transcript
function ThreadModal({ run, onClose }) {
  const [msgs, setMsgs] = useState(null);
  useEffect(() => {
    let alive = true;
    const pull = () => api.get(`/api/activity/${encodeURIComponent(run.id)}/log?tail=500`)
      .then((d) => { if (alive) setMsgs(parseThread(d.lines || [])); }).catch(() => { if (alive) setMsgs([]); });
    pull();
    const t = setInterval(pull, 5000);   // live-tail
    return () => { alive = false; clearInterval(t); };
  }, [run.id]);
  const shown = (msgs || []).slice(-40);
  return html`<div class="th-modal" onClick=${onClose}>
    <div class="th-card" onClick=${(e) => e.stopPropagation()}>
      <div class="th-head"><span class="th-title">${run.title || run.id}</span>
        ${run.repo ? html`<span class="th-repo">${run.repo}</span>` : null}
        <button class="th-x" onClick=${onClose} aria-label="close">×</button></div>
      <div class="th-body">
        ${msgs == null ? html`<p class="stub">Loading thread…</p>`
          : shown.length ? shown.map((m, i) => html`<div class="th-msg ${m.role}" key=${i}>
              <span class="th-role">${m.role === "user" ? "Seth" : "Claude"}</span>
              ${m.text ? html`<div class="th-text">${m.text}</div>` : null}
              ${m.tools.length ? html`<div class="th-tools">${m.tools.map((t, j) => html`<span class="th-tool" key=${j}>→ ${t}</span>`)}</div>` : null}
            </div>`)
          : html`<p class="stub">No readable messages in this transcript yet.</p>`}
      </div>
      <div class="th-foot"><span class="note">Live transcript (read-only, refreshing). Sending
        input into a running session isn't wired up.</span></div>
    </div>
  </div>`;
}

export function OverviewView() {
  const [factory, setFactory] = useState(null);
  const [content, setContent] = useState(null);
  const [servers, setServers] = useState([]);
  const [activity, setActivity] = useState({ runs: [], now: 0 });
  const [thread, setThread] = useState(null);   // a running session opened for viewing

  const load = () => {
    api.get("/api/pipeline").then(setFactory).catch(() => {});
    api.get("/api/content").then(setContent).catch(() => {});
    api.get("/api/activity").then((d) => setActivity({ runs: d.runs || [], now: d._now || 0 })).catch(() => {});
  };
  useEffect(() => { load(); api.get("/api/servers").then((d) => setServers(d.servers || [])).catch(() => {}); }, []);
  useEffect(() => { const t = setInterval(load, 4000); return () => clearInterval(t); }, []);
  useEffect(() => {
    const t = setInterval(() => api.get("/api/servers").then((d) => setServers(d.servers || [])).catch(() => {}), 10000);
    return () => clearInterval(t);
  }, []);

  const runs = activity.runs, now = activity.now;
  const today = runs.filter((r) => lastTs(r) >= startOfTodaySec());
  // one row per actual run: a session can carry two capture records for one transcript — dedupe
  // by log_path (keeping the most recently-touched), so each running session shows exactly once.
  const live = [];
  const seenLog = new Set();
  for (const r of runs.filter((r) => r.live).sort((a, b) => lastTs(b) - lastTs(a))) {
    const key = r.log_path || r.id;
    if (seenLog.has(key)) continue;
    seenLog.add(key); live.push(r);
  }
  const drains = live.filter((r) => r.surface === "factory").length;
  const gate = (factory?.stages || []).find((s) => s.id === "gate")?.count ?? 0;
  const cst = content?.nodes || [];

  return html`<section class="view ov">
    <div class="viewhead"><h1>Overview</h1><span class="sub">both pipelines · servers · what's running</span></div>

    <div class="ov-strip">
      <${Metric} k="AIOS · at gate" v=${content?.gate ?? "…"} cls="warn" sub="your decisions" />
      <${Metric} k="Software · at gate" v=${gate} cls=${gate ? "warn" : ""} sub="[GATE: human]" />
      <${Metric} k="drains live" v=${drains} sub="factory" />
      <${Metric} k="spend · today" v=${fmtUsd(sum(today, (r) => r.cost_usd))} sub=${fmtUsd(sum(runs, (r) => r.cost_usd)) + " all-time"} />
    </div>

    <div class="ov-two">
      <div class="ov-card">
        <h3>AIOS pipeline</h3>
        ${cst.length ? cst.map((s) => html`<div class="ov-row" key=${s.id}>
          <span class="ov-dot ${s.id === "gate" ? "gate" : ""}"></span><span class="t">${s.label}</span><span class="r">${s.count}</span></div>`) : html`<div class="ov-row"><span class="t dim">loading…</span></div>`}
        ${content ? html`<div class="ov-row"><span class="t dim">${content.shipped} shipped · ${content.rejected} rejected lifetime</span></div>` : null}
      </div>
      <div class="ov-card">
        <h3>Software factory</h3>
        ${(factory?.stages || []).filter((s) => s.count).map((s) => html`<div class="ov-row" key=${s.id}>
          <span class="ov-dot ${s.id === "gate" ? "gate" : s.id === "complete" ? "ok" : ""}"></span><span class="t">${s.label}</span><span class="r">${s.count}</span></div>`)}
        ${!factory ? html`<div class="ov-row"><span class="t dim">loading…</span></div>` : null}
      </div>
    </div>

    <h3 class="ov-sect">Dev servers</h3>
    ${servers.length ? servers.map((s) => html`<div class="ov-srv" key=${s.repo + s.name + s.port}>
        <span class="ov-st ${s.up ? "up" : "down"}"></span>
        <div class="ov-si"><div class="nm">${s.name} <span class="repo">· ${s.repo}</span>${s.self ? html`<span class="tag">this page</span>` : null}</div>
          <div class="url">${s.url || s.cmd}</div></div>
        <div class="ov-ctrls">
          ${s.up ? html`<a class="verb ok" href=${s.url} target="_blank" rel="noopener">Open</a>` : null}
          ${s.self
            ? null
            : html`<button class="verb" onClick=${() => toast("server control lands in the next slice")}>${s.up ? "Stop" : "Start"}</button>
                   <button class="verb" onClick=${() => toast("logs land in the next slice")}>Logs</button>`}
        </div>
      </div>`) : html`<p class="stub">No dev servers declared.</p>`}

    <h3 class="ov-sect">Running now</h3>
    ${live.length ? live.sort((a, b) => lastTs(b) - lastTs(a)).map((r) => html`<div class="ov-run" key=${r.id}
        tabindex="0" title="View the live thread" onClick=${() => setThread(r)}>
        <span class="ov-dot ${r.surface === "session" ? "i" : "a"}"></span>
        <span class="badge">${(r.surface || "").slice(0, 4).toUpperCase()}</span>
        <span class="t">${r.title || r.id}</span>
        <span class="r">${r.repo || ""}${r.cost_usd ? " · " + fmtUsd(r.cost_usd) : ""}<span class="go">→</span></span>
      </div>`) : html`<p class="stub">Nothing running right now.</p>`}

    ${thread ? html`<${ThreadModal} run=${thread} onClose=${() => setThread(null)} />` : null}
  </section>`;
}
