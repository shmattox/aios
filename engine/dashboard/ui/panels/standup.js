const LABELS = { veto: "✅ Veto window", needs_you: "⚠ Needs you",
  handed_off: "↪ Handed off", stuck: "✖ Stuck" };

export default async function render(root, aios) {
  const s = await aios.get("/api/standup").catch(() => null);
  if (!s) { root.innerHTML = "<p class='meta'>no standup on disk</p>"; return; }
  const e = aios.esc;
  let html = "";
  for (const [g, label] of Object.entries(LABELS)) {
    const items = s.groups?.[g] || [];
    html += `<h2>${label} — ${items.length}</h2>`;
    for (const it of items) {
      const id = e(it.id || it.item || "?");
      const line = e(it.headline || it.reason || it.note || JSON.stringify(it).slice(0, 140));
      html += `<div class="row"><span class="badge">${e(it.repo || "")}</span>
        <span class="title"><b>${id}</b> ${line}</span>`;
      if (g === "veto" && it.sha && it.repo)
        html += `<button class="danger" data-repo="${e(it.repo)}" data-sha="${e(it.sha)}">revert</button>`;
      html += `</div>`;
    }
  }
  root.innerHTML = html;
  root.querySelectorAll("[data-sha]").forEach((b) => b.addEventListener("click", () => {
    if (confirm(`git revert ${b.dataset.sha} in ${b.dataset.repo}?\n(Reverts are themselves revertible.)`))
      aios.post("veto_revert", { repo: b.dataset.repo, sha: b.dataset.sha });
  }));
}
