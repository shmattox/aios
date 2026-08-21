// Gate — the differentiator, rendered. The review-gate ledger: decisions, who decided,
// and how often the human overrode the machine's recommendation. Source:
// state/factory/gate-metrics.json (engine/tools/gate_metrics.py; freshness = A128).
import { html, api, useState, useEffect } from "/lib.js";

const pct = (a, b) => b ? Math.round((a / b) * 100) + "%" : "—";
const fmtAge = (s) => s == null ? "?" : s < 172800 ? `${Math.round(s / 3600)}h` : `${Math.round(s / 86400)}d`;

function Metric({ k, v, sub, warn }) {
  return html`<div class="ov-cell"><div class="ov-k">${k}</div>
    <div class="ov-v ${warn ? "warn" : ""}">${v}</div><div class="ov-s">${sub || ""}</div></div>`;
}

function Bar({ label, n, of }) {
  const p = of ? Math.round((n / of) * 100) : 0;
  return html`<div class="uz-row"><span class="uz-lbl">${label}</span>
    <span class="uz-track"><span class="uz-fill" style="width:${p}%"></span></span>
    <span class="uz-n num">${n}</span></div>`;
}

export function GateView() {
  const [g, setG] = useState(null);
  useEffect(() => { api.get("/api/gate-metrics").then(setG).catch(() => setG({ error: true })); }, []);

  if (g == null) return html`<section class="view"><div class="viewhead"><h1>Gate</h1></div><p class="stub">…</p></section>`;
  const w = g.windows?.all;
  if (g.error || !w) return html`<section class="view"><div class="viewhead"><h1>Gate</h1></div>
    <p class="stub">No gate ledger — state/factory/gate-metrics.json is missing or empty (regeneration is aios A128).</p></section>`;

  const ag = w.agreement || {}, de = w.deciders || {}, to = w.totals || {};
  const decided = (ag.agree || 0) + (ag.override || 0);
  const stale = (g._age_s || 0) > 172800;

  return html`<section class="view">
    <div class="viewhead"><h1>Gate</h1>
      <span class="sub">review ledger · generated ${g.generated}
        ${stale ? html` · <span class="warn">${fmtAge(g._age_s)} old</span>` : ""}</span></div>

    <div class="ov-strip">
      <${Metric} k="decisions" v=${w.n} sub="all time" />
      <${Metric} k="agreement" v=${pct(ag.agree, decided)} sub=${`${ag.agree || 0} agree · ${ag.override || 0} override`} />
      <${Metric} k="held for human" v=${ag.hold || 0} sub="review-lane holds" />
      <${Metric} k="reverts" v=${(to.reverted || 0) + (w.reverts_hist || 0)} warn=${(to.reverted || 0) > 0} sub="ledger + historical" />
    </div>

    <h3 class="ov-sect">Outcomes <span class="uz-src">· gate-metrics totals</span></h3>
    <div class="uz-bars-list">
      <${Bar} label="accepted" n=${to.accepted || 0} of=${w.n} />
      <${Bar} label="rejected" n=${to.rejected || 0} of=${w.n} />
      <${Bar} label="reverted" n=${to.reverted || 0} of=${w.n} />
    </div>

    <h3 class="ov-sect">Deciders <span class="uz-src">· who shipped it</span></h3>
    <div class="uz-bars-list">
      ${["human", "auto", "scheduled", "unknown"].map((k) => html`<${Bar} key=${k} label=${k} n=${de[k] || 0} of=${w.n} />`)}
    </div>

    <h3 class="ov-sect">Overrides <span class="uz-src">· human decided against the recommendation · ${(w.override_ids || []).length} items</span></h3>
    ${(w.override_ids || []).length
      ? html`<div class="hl-list">${w.override_ids.slice(0, 20).map((id) => html`<div class="hl-row" key=${id}><span class="hl-id">${id}</span></div>`)}
          ${w.override_ids.length > 20 ? html`<p class="uz-note">…and ${w.override_ids.length - 20} more in the ledger.</p>` : ""}</div>`
      : html`<p class="stub">No overrides recorded.</p>`}
  </section>`;
}
