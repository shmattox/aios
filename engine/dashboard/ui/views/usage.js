// Usage — where the spend goes. Three honest, separately-sourced lenses (the data isn't one
// unified ledger, so we don't pretend it is): the daily cost series (/api/spend — factory drains),
// run counts by surface (/api/activity — the run index), and agent-telemetry totals
// (/api/otel/runs — OpenTelemetry). Each panel names its source.
import { html, api, useState, useEffect } from "/lib.js";

const fmtUsd = (n) => "$" + (Number(n) || 0).toFixed(2);
function fmtTok(n) { n = Number(n) || 0; return n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n); }
const fmtMs = (ms) => ms == null ? "—" : ms >= 1000 ? (ms / 1000).toFixed(1) + "s" : ms + "ms";
const sum = (a, f) => a.reduce((s, r) => s + (Number(f(r)) || 0), 0);

function Metric({ k, v, sub }) {
  return html`<div class="ov-cell"><div class="ov-k">${k}</div><div class="ov-v">${v}</div><div class="ov-s">${sub || ""}</div></div>`;
}

// A compact daily cost bar chart. Bars scale to the max day; hover shows the day's detail.
function CostBars({ days }) {
  const show = days.slice(-42);           // last ~6 weeks
  const max = Math.max(1, ...show.map((d) => d.cost_usd || 0));
  const W = Math.max(show.length * 16, 60), H = 120, PAD = 4;
  const bw = 11;
  return html`<div class="uz-chart">
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="uz-bars" role="img" aria-label="daily cost">
      ${show.map((d, i) => {
        const h = Math.max(1, Math.round(((d.cost_usd || 0) / max) * (H - PAD * 2)));
        return html`<rect key=${d.date} x=${i * 16 + 2} y=${H - PAD - h} width=${bw} height=${h} rx="1.5"
          class="uz-bar"><title>${d.date} · ${fmtUsd(d.cost_usd)} est. · ${fmtTok(d.output_tokens)} tok · ${d.drains || 0} drains</title></rect>`;
      })}
    </svg>
  </div>`;
}

function Bar({ label, n, of, meta }) {
  const pct = of ? Math.round((n / of) * 100) : 0;
  return html`<div class="uz-row">
    <span class="uz-lbl">${label}</span>
    <span class="uz-track"><span class="uz-fill" style="width:${pct}%"></span></span>
    <span class="uz-n num">${n}${meta ? html` <span class="uz-meta">${meta}</span>` : null}</span>
  </div>`;
}

export function UsageView() {
  const [spend, setSpend] = useState({ days: [] });
  const [activity, setActivity] = useState({ runs: [] });
  const [otel, setOtel] = useState(null);

  useEffect(() => {
    api.get("/api/spend").then((d) => setSpend({ days: d.days || [] })).catch(() => {});
    api.get("/api/activity").then((d) => setActivity({ runs: d.runs || [] })).catch(() => {});
    api.get("/api/otel/runs").then(setOtel).catch(() => setOtel({ agg: { jaeger_up: false } }));
  }, []);

  const days = spend.days, runs = activity.runs;
  const totalCost = sum(days, (d) => d.cost_usd);
  const totalDrains = sum(days, (d) => d.drains);

  // real cost/tokens = OpenTelemetry (the claude_code.interaction traces). Today = runs started
  // since local midnight; the telemetry window = everything Jaeger still retains.
  const agg = otel?.agg || {}, otelUp = agg.jaeger_up !== false, otelRuns = otel?.runs || [];
  const sot = (() => { const d = new Date(); d.setHours(0, 0, 0, 0); return d.getTime() / 1000; })();
  const otelToday = otelRuns.filter((r) => (r.started || 0) >= sot);
  const spendToday = sum(otelToday, (r) => r.cost_usd);
  const tokToday = sum(otelToday, (r) => r.total_tokens);

  // by surface (run index) — counts, plus cost where the run carries it
  const surfaces = {};
  for (const r of runs) {
    const s = r.surface || "other";
    (surfaces[s] = surfaces[s] || { n: 0, cost: 0 }).n += 1;
    surfaces[s].cost += Number(r.cost_usd) || 0;
  }
  const surfRows = Object.entries(surfaces).sort((a, b) => b[1].n - a[1].n);

  return html`<section class="view">
    <div class="viewhead"><h1>Usage</h1><span class="sub">where the spend goes</span></div>

    <div class="ov-strip">
      <${Metric} k="spend · today" v=${otel == null ? "…" : otelUp ? fmtUsd(spendToday) : "—"}
        sub=${otel == null ? "" : otelUp ? `${fmtTok(tokToday)} tok · ${otelToday.length} runs · est.` : "telemetry store down"} />
      <${Metric} k="telemetry · window" v=${otelUp ? fmtUsd(agg.cost_usd) : "—"}
        sub=${otelUp ? `${agg.runs || 0} runs · ${fmtTok(agg.tokens)} tok · est.` : "store down"} />
      <${Metric} k="factory drains" v=${fmtUsd(totalCost)} sub=${`${totalDrains} drains · ${days.length} days · est.`} />
      <${Metric} k="errors" v=${otelUp ? (agg.errors || 0) : "—"} sub="telemetry window" cls=${agg.errors ? "warn" : ""} />
      <${Metric} k="latency · p50/p95" v=${otelUp && agg.p50_ms != null ? `${fmtMs(agg.p50_ms)} / ${fmtMs(agg.p95_ms)}` : "—"}
        sub="telemetry window" />
    </div>

    ${agg.unpriced_models?.length ? html`<p class="uz-note warn">Unpriced model(s) charged at the
      $5/$15 default — cost is a floor, not a quote: ${agg.unpriced_models.join(", ")}</p>` : null}

    <h3 class="ov-sect">Daily cost <span class="uz-src">· factory drains · /api/spend · est.</span></h3>
    ${days.length ? html`<${CostBars} days=${days} />` : html`<p class="stub">No spend recorded yet.</p>`}

    <h3 class="ov-sect">By surface <span class="uz-src">· run index · /api/activity · by volume, not dollars</span></h3>
    ${surfRows.length ? html`<div class="uz-bars-list">
      ${surfRows.map(([s, v]) => html`<${Bar} label=${s} n=${v.n} of=${runs.length}
        meta=${v.cost ? "· " + fmtUsd(v.cost) + " est." : ""} key=${s} />`)}
    </div>
    <p class="uz-note">Run counts are complete; per-run cost is only attributed where the run
      carries it (most sessions don't) — so the surface split is by volume, not dollars. The
      authoritative cost series is the daily chart above.</p>`
      : html`<p class="stub">No runs indexed yet.</p>`}

    <h3 class="ov-sect">Errors by tool <span class="uz-src">· telemetry window · /api/otel/runs</span></h3>
    ${Object.keys(agg.error_kinds || {}).length ? html`<div class="uz-bars-list">
      ${Object.entries(agg.error_kinds).sort((a, b) => b[1] - a[1]).map(([tool, n]) => html`<${Bar}
        label=${tool} n=${n} of=${agg.errors || 1} key=${tool} />`)}
    </div>` : html`<p class="stub">No tool errors in the telemetry window.</p>`}
  </section>`;
}
