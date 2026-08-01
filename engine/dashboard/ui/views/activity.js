// A121 — Activity view: what is running right now, across every agent surface
// (factory drains, the nightly pipeline, interactive sessions, /goal, Workflows).
// List + detail pane, mirroring the Inbox home (#inbox/#cards/#detail); the detail
// pane adds a live, auto-scrolling log tail. Reads GET /api/activity +
// GET /api/activity/<id>/log?tail=N; refreshes on the SSE `activity` signal.
import { html, api, useState, useEffect, useRef } from "/lib.js";

const SURFACE_LABEL = { factory: "factory", pipeline: "pipeline", session: "session", goal: "goal", workflow: "workflow" };
const STATUS_LABEL = { running: "running", stale: "stale", shipped: "shipped", parked: "parked", "no-op": "no-op", failed: "failed", ended: "ended" };

function useNarrow() {
  const [n, setN] = useState(window.matchMedia("(max-width: 860px)").matches);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 860px)");
    const on = () => setN(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return n;
}

function fmtElapsed(startedS, nowS) {
  const s = Math.max(0, Math.round((nowS || 0) - (startedS || 0)));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m${String(s % 60).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  return `${h}h${String(m % 60).padStart(2, "0")}m`;
}

function fmtWhen(tsS) {
  return typeof tsS === "number" ? new Date(tsS * 1000).toLocaleString() : "";
}

// stale = the record still says "running" but neither its pid nor its heartbeat is fresh —
// rendered loud, never dropped (the contract's liveness invariant).
function runStatus(run) {
  if (run.status === "running") return run.live ? "running" : "stale";
  return run.status || "ended";
}

function drillLinks(run) {
  const out = [];
  for (const id of Array.isArray(run.item_ids) ? run.item_ids : []) {
    if (!id) continue;
    const path = run.repo && run.repo !== "env-ops" ? `Projects/${run.repo}/BACKLOG.md` : "BACKLOG.md";
    out.push({ tag: "kb", label: `${id} · ${path}`, key: `item-${id}` });
  }
  if (run.worktree) out.push({ tag: "state", label: run.worktree, key: "worktree" });
  return out;
}

function CountStrip({ runs }) {
  const live = runs.filter((r) => r.live);
  if (!live.length) return html`<div class="countstrip"><span class="chip">0 running</span></div>`;
  const bySurface = {};
  for (const r of live) bySurface[r.surface] = (bySurface[r.surface] || 0) + 1;
  const parts = Object.keys(bySurface).sort().map((s) => `${bySurface[s]} ${SURFACE_LABEL[s] || s}`);
  return html`<div class="countstrip"><span class="chip rs-running">${live.length} running: ${parts.join(" · ")}</span></div>`;
}

function RunDetail({ run, log, nowS, onDismiss }) {
  const status = runStatus(run);
  const links = drillLinks(run);
  const preRef = useRef(null);
  useEffect(() => { if (preRef.current) preRef.current.scrollTop = preRef.current.scrollHeight; }, [log]);

  return html`<article id="detail">
    <div class="head">
      <div class="chips">
        <span class="chip id">${run.id}</span>
        <span class="chip">${SURFACE_LABEL[run.surface] || run.surface}</span>
        <span class="chip rs-${status}">${STATUS_LABEL[status] || status}</span>
        ${run.repo ? html`<span class="chip">${run.repo}</span>` : null}
      </div>
      <h1>${run.title || run.id}</h1>
      <p class="why">
        ${status === "running"
          ? html`elapsed <span class="num">${fmtElapsed(run.started, nowS)}</span>`
          : html`last seen <span class="num">${fmtWhen(run.heartbeat)}</span>`}
        ${run.tokens ? html` · ${run.tokens} tokens` : null}
        ${run.cost_usd ? html` · $${Number(run.cost_usd).toFixed(2)}` : null}
      </p>
    </div>

    ${run.detail ? html`<div class="sect"><div class="label">Detail</div><p class="why">${run.detail}</p></div>` : null}

    <div class="sect">
      <div class="label">Log tail</div>
      ${log == null
        ? html`<pre class="body" ref=${preRef}>loading…</pre>`
        : !log.available
          ? html`<pre class="body" ref=${preRef}>log unavailable</pre>`
          : html`<pre class="body" ref=${preRef}>${log.lines.join("\n")}</pre>`}
    </div>

    ${links.length ? html`
      <div class="sect">
        <div class="label">Drill down</div>
        <div class="refs">${links.map((l) => html`
          <div class="ref" key=${l.key}><span class="badge ${l.tag}">${l.tag}</span><span class="path">${l.label}</span></div>`)}</div>
      </div>` : null}

    ${status === "stale" ? html`
      <div class="verbs">
        <button class="verb" onClick=${onDismiss}>Dismiss</button>
        <span class="note">Hides locally only — the record itself clears via the server's retention prune.</span>
      </div>` : null}
  </article>`;
}

export function ActivityView() {
  const [runs, setRuns] = useState(null);
  const [clientNow, setClientNow] = useState(Date.now() / 1000);
  const [selId, setSelId] = useState(null);
  const [log, setLog] = useState(null);
  const [dismissed, setDismissed] = useState(() => new Set());
  const [open, setOpen] = useState(true);
  const narrow = useNarrow();
  const selIdRef = useRef(null);
  selIdRef.current = selId;

  const loadRuns = () => api.get("/api/activity")
    .then((b) => setRuns(b.runs || []))
    .catch(() => setRuns([]));

  const loadLog = (id) => {
    if (!id) { setLog(null); return; }
    api.get(`/api/activity/${encodeURIComponent(id)}/log?tail=400`)
      .then(setLog).catch(() => setLog({ available: false, lines: [] }));
  };

  useEffect(() => { loadRuns(); }, []);
  useEffect(() => { loadLog(selId); }, [selId]);

  // default-select the first run once the list arrives
  useEffect(() => { if (runs && runs.length && selId == null) setSelId(runs[0].id); }, [runs]);

  // client clock — ticks the elapsed display between refetches
  useEffect(() => {
    const t = setInterval(() => setClientNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    let es, timer, errs = 0;
    const poll = () => { loadRuns(); loadLog(selIdRef.current); timer = setTimeout(poll, 5000); };
    try {
      es = new EventSource("/api/events");
      es.addEventListener("change", (ev) => {
        const c = JSON.parse(ev.data).changed || [];
        if (c.includes("activity")) { loadRuns(); loadLog(selIdRef.current); }
      });
      es.onerror = () => { if (++errs >= 2) { es.close(); poll(); } };  // tolerate a single blip (matches useLive)
    } catch (e) { poll(); }
    return () => { es && es.close(); clearTimeout(timer); };
  }, []);

  if (runs == null) return html`<section class="view"><p class="stub">Loading…</p></section>`;

  const sorted = [...runs].sort((a, b) => (a.live !== b.live ? (a.live ? -1 : 1) : (b.started || 0) - (a.started || 0)));
  const visible = sorted.filter((r) => !dismissed.has(r.id));
  const selRun = visible.find((r) => r.id === selId) || null;
  const dismiss = (id) => setDismissed((d) => new Set(d).add(id));
  const rowClick = (id) => {
    if (narrow && id === selId) { setOpen((o) => !o); return; }
    setSelId(id); setOpen(true);
  };

  return html`<section class="view">
    <div class="viewhead"><h1>Activity</h1>
      <span class="sub">${visible.length} run${visible.length === 1 ? "" : "s"} · live across factory drains, the nightly pipeline, sessions, goals, and Workflows</span></div>
    <${CountStrip} runs=${runs} />
    ${visible.length === 0
      ? html`<p class="stub">Nothing running right now.</p>`
      : html`<div id="inbox">
          <div id="cards">
            ${visible.map((r) => {
              const status = runStatus(r);
              return html`
                <div class="card-row" key=${r.id} tabindex="0" aria-current=${String(r.id === selId)}
                     onClick=${() => rowClick(r.id)} onKeyDown=${(e) => { if (e.key === "Enter") rowClick(r.id); }}>
                  <span class="healthdot ${r.live ? "live" : "dead"}"></span>
                  <span class="t">
                    <span class="title">${r.title || r.id}</span>
                    <span class="sub">${SURFACE_LABEL[r.surface] || r.surface}${r.item_ids && r.item_ids.length ? ` · ${r.item_ids.join(", ")}` : ""}</span>
                  </span>
                  <span class="age num rs-${status}">${status === "running" ? fmtElapsed(r.started, clientNow) : (STATUS_LABEL[status] || status)}</span>
                  <span class="disclose">▸</span>
                </div>
                ${narrow && r.id === selId && open
                  ? html`<${RunDetail} run=${r} log=${log} nowS=${clientNow} onDismiss=${() => dismiss(r.id)} />`
                  : null}`;
            })}
          </div>
          ${!narrow && selRun
            ? html`<div><${RunDetail} run=${selRun} log=${log} nowS=${clientNow} onDismiss=${() => dismiss(selRun.id)} /></div>`
            : null}
        </div>`}
  </section>`;
}
