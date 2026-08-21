// Health — the env's invariants, rendered. Standing-check reds (A94 runner), the scheduled-task
// fleet's last-run ages, and per-source staleness. Read-only; an unreadable input arrives as a
// synthetic red row from the server (fail-open), never as a hidden panel.
import { html, api, useState, useEffect } from "/lib.js";

const fmtAge = (s) => s == null ? "—" : s < 90 ? `${s}s` : s < 5400 ? `${Math.round(s / 60)}m`
  : s < 172800 ? `${Math.round(s / 3600)}h` : `${Math.round(s / 86400)}d`;

function Metric({ k, v, sub, warn }) {
  return html`<div class="ov-cell"><div class="ov-k">${k}</div>
    <div class="ov-v ${warn ? "warn" : ""}">${v}</div><div class="ov-s">${sub || ""}</div></div>`;
}

const Dot = ({ status }) => html`<span class="hl-dot hl-${status}" title=${status}></span>`;

export function HealthView() {
  const [h, setH] = useState(null);

  useEffect(() => { api.get("/api/health").then(setH).catch(() => setH({ error: true })); }, []);

  if (h == null) return html`<section class="view"><div class="viewhead"><h1>Health</h1></div><p class="stub">…</p></section>`;
  if (h.error) return html`<section class="view"><div class="viewhead"><h1>Health</h1></div>
    <p class="stub">The dashboard server did not answer /api/health — it is itself the red.</p></section>`;

  const st = h.standing || { checks: [] };
  const order = { red: 0, expired: 1, observed: 2, watching: 3, green: 4 };
  const checks = [...st.checks].sort((a, b) => (order[a.status] ?? 5) - (order[b.status] ?? 5) || String(a.id).localeCompare(String(b.id)));
  const fleet = [...(h.fleet || [])].sort((a, b) => (b.age_s ?? Infinity) - (a.age_s ?? Infinity));
  const stale = (h.sources || []).filter((s) => s.age_s == null || s.age_s > 172800).length;

  return html`<section class="view">
    <div class="viewhead"><h1>Health</h1><span class="sub">standing checks · fleet · staleness</span></div>

    <div class="ov-strip">
      <${Metric} k="standing reds" v=${st.reds} warn=${st.reds > 0} sub=${`${st.greens} green`} />
      <${Metric} k="watch expired" v=${st.watch_expired} warn=${st.watch_expired > 0} sub="failed past check_by" />
      <${Metric} k="fleet" v=${fleet.length} sub="scheduled tasks seen" />
      <${Metric} k="stale sources" v=${stale} warn=${stale > 0} sub="> 48h or missing" />
    </div>

    <h3 class="ov-sect">Standing checks <span class="uz-src">· state/standing-checks/results.json · ${h.generated_utc || "?"}</span></h3>
    <div class="hl-list">
      ${checks.map((c) => html`<div class="hl-row hl-row-${c.status}" key=${c.id}>
        <${Dot} status=${c.status} />
        <span class="hl-id">${c.id}</span>
        <span class="hl-meta">${c.kind}${c.cadence ? " · " + c.cadence : ""}${c.first_red ? " · red since " + c.first_red : ""}</span>
        ${c.status !== "green" ? html`<div class="hl-why">${c.reason ? c.reason + " — " : ""}${c.on_violation || ""}</div>` : null}
      </div>`)}
    </div>

    <h3 class="ov-sect">Task fleet <span class="uz-src">· state/task-logs · last-run age (health judgment lives in the fleet standing check)</span></h3>
    <div class="hl-list">
      ${fleet.map((f) => html`<div class="hl-row" key=${f.task}>
        <span class="hl-id">${f.task}</span><span class="hl-meta num">${fmtAge(f.age_s)}</span>
      </div>`)}
    </div>

    <h3 class="ov-sect">Sources <span class="uz-src">· mtime age per state file</span></h3>
    <div class="hl-list">
      ${(h.sources || []).map((s) => html`<div class="hl-row" key=${s.name}>
        <span class="hl-id">${s.name}</span>
        <span class="hl-meta">${s.path}</span>
        <span class="hl-meta num ${s.age_s == null || s.age_s > 172800 ? "warn" : ""}">${fmtAge(s.age_s)}</span>
      </div>`)}
    </div>
  </section>`;
}
