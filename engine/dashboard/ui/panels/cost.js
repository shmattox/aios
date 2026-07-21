export default async function render(root, aios) {
  const s = await aios.get("/api/spend").catch(() => ({ days: [], gate_metrics: null }));
  const e = aios.esc;
  const total = s.days.reduce((a, d) => a + (d.cost_usd || 0), 0);
  let html = `<h2>Factory spend — $${total.toFixed(2)} across ${s.days.length} day(s)</h2>`;
  for (const d of [...s.days].reverse())
    html += `<div class="row"><span class="title">${e(d.date)}</span>
      <span class="meta">${e(d.drains)} drains · ${e(d.output_tokens)} out-tokens</span>
      <span class="badge">$${(d.cost_usd || 0).toFixed(2)}</span></div>`;
  const w = s.gate_metrics?.windows?.all;
  if (w) html += `<h2>Gate (all-time)</h2>
    <div class="row"><span class="title">accepted ${e(w.totals.accepted)} · rejected ${e(w.totals.rejected)} · reverted ${e(w.totals.reverted)}</span>
    <span class="meta">deciders: ${aios.esc(JSON.stringify(w.deciders))}</span></div>`;
  html += `<p class="meta">$ figures are authoritative; token counts may under-count multi-turn runs (H62)</p>`;
  root.innerHTML = html;
}
