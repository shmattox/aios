export default async function render(root, aios) {
  const h = await aios.get("/api/held").catch(() => ({ held: [] }));
  const e = aios.esc;
  let html = `<h2>Gate queue — ${h.held.length} held</h2>`;
  h.held.forEach((it, i) => {
    html += `<div class="row" data-i="${i}">
      <span class="badge ${it.lane === "review" ? "warn" : ""}">${e(it.lane)}</span>
      <span class="title">${e(it.title || it.id)}</span>
      <span class="meta">${e(it.kb)} → ${e(it.recommended || "?")}${it.rec_reason ? " · " + e(it.rec_reason) : ""}</span>
      <button data-view="${i}">view</button>
      <button data-ship="${e(it.id)}">ship</button>
      <button class="danger" data-reject="${e(it.id)}">reject</button></div>
      <div class="body" id="draft-${i}" hidden></div>`;
  });
  root.innerHTML = html || "<p class='meta'>queue clear</p>";
  root.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", async () => {
    const el = root.querySelector(`#draft-${b.dataset.view}`);
    if (el.hidden) { const d = await aios.get(`/api/draft?i=${b.dataset.view}`); el.textContent = d.markdown; }
    el.hidden = !el.hidden;
  }));
  root.querySelectorAll("[data-ship]").forEach((b) => b.addEventListener("click", () =>
    aios.post("gate_ship", { id: b.dataset.ship }).then(() => b.closest(".row").remove())));
  root.querySelectorAll("[data-reject]").forEach((b) => b.addEventListener("click", () => {
    const reason = prompt("Reject reason:");
    if (reason) aios.post("gate_reject", { id: b.dataset.reject, reason })
      .then(() => b.closest(".row").remove());
  }));
}
