// Sessions — recent + live runs across every surface (factory drains, interactive sessions,
// pipeline stages, goals, workflows). Source: /api/activity (this env's own run records;
// terminal records are pruned after ~24h, so this is "recent", not all-time). Click a row to
// replay its transcript. Cost is a derived estimate.
import { html, api, useState, useEffect } from "/lib.js";
import { ThreadModal } from "/thread.js";

const fmtUsd = (n) => "$" + (Number(n) || 0).toFixed(2);
const fmtTok = (n) => { n = Number(n) || 0; return n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n); };
const fmtDur = (r) => { const e = (r.ended || r.heartbeat || 0) - (r.started || 0); return e > 0 ? (e >= 60 ? Math.round(e / 60) + "m" : Math.round(e) + "s") : "—"; };
const fmtAge = (s) => s == null ? "—" : s < 90 ? `${Math.round(s)}s` : s < 5400 ? `${Math.round(s / 60)}m` : s < 172800 ? `${Math.round(s / 3600)}h` : `${Math.round(s / 86400)}d`;

export function SessionsView() {
  const [runs, setRuns] = useState(null);
  const [q, setQ] = useState("");
  const [replay, setReplay] = useState(null);

  const load = () => api.get("/api/activity")
    .then((d) => setRuns(d.runs || [])).catch(() => setRuns([]));
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, []);

  if (runs == null) return html`<section class="view"><div class="viewhead"><h1>Sessions</h1></div><p class="stub">…</p></section>`;

  const needle = q.trim().toLowerCase();
  const match = (r) => !needle || [r.id, r.repo, r.surface, r.title, (r.item_ids || []).join(" ")]
    .some((v) => String(v || "").toLowerCase().includes(needle));
  const rows = runs.filter(match)
    .sort((a, b) => (b.ended || b.heartbeat || b.started || 0) - (a.ended || a.heartbeat || a.started || 0));
  const live = rows.filter((r) => r.live).length;

  return html`<section class="view">
    <div class="viewhead"><h1>Sessions</h1><span class="sub">recent + live runs · /api/activity · replayable</span></div>

    <div class="ov-strip">
      <div class="ov-cell"><div class="ov-k">runs shown</div><div class="ov-v">${rows.length}</div><div class="ov-s">of ${runs.length} in window</div></div>
      <div class="ov-cell"><div class="ov-k">live now</div><div class="ov-v">${live}</div><div class="ov-s">still running</div></div>
    </div>

    <div class="se-search"><input class="se-input" type="search" placeholder="filter by id · repo · surface · item"
      value=${q} onInput=${(e) => setQ(e.target.value)} aria-label="filter sessions" /></div>

    ${rows.length ? html`<div class="hl-list se-table">
      ${rows.map((r) => html`<div class="hl-row se-row ${r.live ? "se-live" : ""}" key=${r.id}
          tabindex="0" title="Replay this run's transcript" onClick=${() => setReplay(r)}
          onKeyDown=${(e) => { if (e.key === "Enter") setReplay(r); }}>
        <span class="badge">${(r.surface || "").slice(0, 4).toUpperCase()}</span>
        <span class="se-title">${r.title || r.id}</span>
        <span class="se-meta">${r.repo || ""}${(r.item_ids || []).length ? " · " + r.item_ids.join(",") : ""}</span>
        <span class="se-stat ${r.status === "failed" || r.status === "parked" ? "warn" : ""}">${r.live ? "running" : r.status}</span>
        <span class="se-nums num">${fmtDur(r)} · ${fmtTok(r.tokens)} · ${fmtUsd(r.cost_usd)} est. · ${fmtAge(r.age_s)}</span>
      </div>`)}
    </div>` : html`<p class="stub">No runs match. (Terminal records are pruned after ~24h — this window is recent + live only.)</p>`}

    ${replay ? html`<${ThreadModal} run=${replay} onClose=${() => setReplay(null)} />` : null}
  </section>`;
}
