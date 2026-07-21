export default async function render(root, aios) {
  const b = await aios.get("/api/brief").catch(() => null);
  if (!b) { root.innerHTML = "<p class='meta'>no brief cache on disk</p>"; return; }
  const e = aios.esc;
  const age = b._age_s > 86400 ? ` <span class="stale">(stale ${Math.round(b._age_s / 3600)}h)</span>` : "";
  let html = `<h2>Act${age}</h2>`;
  for (const it of b.act || []) {
    html += `<div class="row" data-id="${e(it.id)}">
      <span class="badge ${it.urgency === "high" ? "hi" : ""}">${e(it.urgency || "-")}</span>
      <span class="title">${e(it.title)}</span>
      <span class="meta">${e(it.domain)}</span>
      <button data-act="${e(it.item_id || it.id)}">done</button></div>
      <div class="meta" style="padding:0 10px 8px">${e(it.system_voice || "")} — ${e(it.claude_voice || "")}</div>`;
  }
  html += `<h2>Going quiet</h2>`;
  for (const g of b.going_quiet || [])
    html += `<div class="row"><span class="title">${e(g.name)}</span><span class="meta">${e(g.days)}d — ${e(g.note || "")}</span></div>`;
  html += `<h2>Health</h2>`;
  for (const [k, v] of Object.entries(b.health_lines || {}))
    html += `<div class="row"><span class="meta">${e(k)}</span><span class="title">${e(v)}</span></div>`;
  root.innerHTML = html;
  root.querySelectorAll("button[data-act]").forEach((btn) =>
    btn.addEventListener("click", () =>
      aios.post("walk_decision", { item_id: btn.dataset.act, station: "act",
        choice: "done", action: "closed from dashboard" }).then(() => btn.closest(".row").remove())));
}
