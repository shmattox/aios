export default async function render(root, aios) {
  const e = aios.esc;
  const seg = location.hash.split("/").slice(2);  // #/mirror/<silo>/<table>/<slug>
  if (seg.length >= 3) {
    const r = await aios.get(`/api/domains/${seg[0]}/${seg[1]}/${seg[2]}`);
    root.innerHTML = `<h2><a href="#/mirror/${e(seg[0])}/${e(seg[1])}">← ${e(seg[1])}</a> / ${e(r.slug)}</h2>
      <pre>${e(JSON.stringify(r.fields, null, 1))}</pre><div class="body">${e(r.body)}</div>
      <p class="meta">read-only until A64</p>`;
    return;
  }
  if (seg.length === 2) {
    const t = await aios.get(`/api/domains/${seg[0]}/${seg[1]}`);
    const cols = [...new Set(t.records.flatMap((r) => Object.keys(r.fields)))].slice(0, 4);
    let html = `<h2><a href="#/mirror">← silos</a> / ${e(seg[0])} / ${e(seg[1])} — ${t.records.length}</h2>`;
    for (const r of t.records)
      html += `<div class="row"><a class="title" href="#/mirror/${e(seg[0])}/${e(seg[1])}/${e(r.slug)}">${e(r.slug)}</a>
        <span class="meta">${cols.map((c) => `${e(c)}: ${e(r.fields[c] ?? "")}`).join(" · ")}</span></div>`;
    root.innerHTML = html;
    return;
  }
  const d = await aios.get("/api/domains");
  let html = "<h2>state/domains</h2>";
  for (const s of d.silos)
    for (const t of s.tables)
      html += `<div class="row"><span class="badge">${e(s.silo)}</span>
        <a class="title" href="#/mirror/${e(s.silo)}/${e(t.name)}">${e(t.name)}</a>
        <span class="meta">${t.count} records</span></div>`;
  root.innerHTML = html;
}
