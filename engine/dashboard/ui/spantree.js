// Span waterfall — the OTel trace behind a run, rendered as an indented tree (label · type ·
// duration · tokens · ok/fail), the LangGraph-Studio convention. Source: /api/otel/run/<traceId>
// (Jaeger via otel_runs.build_graph). The run→trace join is DETERMINISTIC: an OTel run's
// session.id equals the <uuid>.jsonl stem of an activity record's log_path — no heuristics.
import { html, api, useState, useEffect } from "/lib.js";

const fmtMs = (ms) => ms == null ? "—" : ms >= 1000 ? (ms / 1000).toFixed(1) + "s" : ms + "ms";
const fmtTok = (n) => { n = Number(n) || 0; return n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n); };

// activity record → its transcript session uuid (only session-style jsonl logs qualify)
export function sessionUuidOf(run) {
  const lp = String(run?.log_path || "").replace(/\\/g, "/");
  if (!lp.endsWith(".jsonl")) return null;
  return lp.split("/").pop().replace(/\.jsonl$/, "") || null;
}

// exact join: the OTel run whose session_id matches this activity record's transcript uuid
export function traceForRun(run, otelRuns) {
  const uuid = sessionUuidOf(run);
  if (!uuid) return null;
  const hit = (otelRuns || []).find((r) => r.session_id === uuid);
  return hit ? hit.trace_id : null;
}

// order spans as a DFS tree from the roots (parents before children, siblings by start order —
// node array order from build_graph is span order, so a stable DFS keeps it readable)
function orderTree(nodes, edges) {
  const kids = {};
  const hasParent = new Set(edges.map((e) => e.target));
  for (const e of edges) (kids[e.source] = kids[e.source] || []).push(e.target);
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const out = [];
  const seen = new Set();
  const walk = (id) => {
    if (seen.has(id)) return;
    seen.add(id);
    const n = byId[id]; if (!n) return;
    out.push(n); (kids[id] || []).forEach(walk);
  };
  nodes.filter((n) => !hasParent.has(n.id)).forEach((n) => walk(n.id));
  for (const n of nodes) if (!out.includes(n)) out.push(n);   // orphans still render (fail-open)
  return out;
}

export function SpanTreeModal({ traceId, title, onClose }) {
  const [g, setG] = useState(null);
  useEffect(() => {
    let alive = true;
    api.get(`/api/otel/run/${encodeURIComponent(traceId)}`)
      .then((d) => { if (alive) setG(d); }).catch(() => { if (alive) setG({ error: true }); });
    return () => { alive = false; };
  }, [traceId]);

  const s = g?.summary;
  const rows = g && !g.error ? orderTree(g.nodes || [], g.edges || []) : [];
  const maxDur = Math.max(1, ...rows.map((n) => n.dur_ms || 0));
  return html`<div class="th-modal" onClick=${onClose}>
    <div class="th-card sp-card" onClick=${(e) => e.stopPropagation()}>
      <div class="th-head"><span class="th-title">${title || "Span tree"}</span>
        <span class="th-repo">trace ${String(traceId).slice(0, 12)}…</span>
        <button class="th-x" onClick=${onClose} aria-label="close">×</button></div>
      <div class="th-body">
        ${g == null ? html`<p class="stub">Loading trace…</p>`
        : g.error || !rows.length ? html`<p class="stub">No spans — the telemetry store (Jaeger) is down or this trace has expired from its retention window.</p>`
        : html`
          ${s ? html`<div class="sp-sum num">${s.model} · ${fmtMs(s.duration_ms)} · ${fmtTok(s.total_tokens)} tok · $${(s.cost_usd || 0).toFixed(2)} est. · ${s.errors} error${s.errors === 1 ? "" : "s"}</div>` : null}
          <div class="sp-list">
            ${rows.map((n) => html`<div class="sp-row ${n.ok === false ? "sp-fail" : ""}" key=${n.id}
                style="padding-left:${8 + Math.min(n.depth, 12) * 14}px">
              <span class="sp-dot ${n.ok === false ? "bad" : ""}"></span>
              <span class="sp-lbl" title=${n.op}>${n.label || n.op}</span>
              <span class="sp-kind">${n.type || ""}${n.agent ? " · " + n.agent : ""}</span>
              <span class="uz-track sp-track"><span class="uz-fill" style="width:${Math.max(2, Math.round(((n.dur_ms || 0) / maxDur) * 100))}%"></span></span>
              <span class="sp-num num">${fmtMs(n.dur_ms)}${n.tokens ? " · " + fmtTok(n.tokens) : ""}</span>
            </div>`)}
          </div>`}
      </div>
      <div class="th-foot"><span class="note">OpenTelemetry span tree · /api/otel/run · read-only.</span></div>
    </div>
  </div>`;
}
