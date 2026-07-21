// A63 shell: fetch + token, hash router, j/k keyboard nav, mtime polling.
const token = document.querySelector('meta[name="aios-token"]').content;

const aios = {
  token,
  esc: (s) => String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
  async get(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
  },
  async post(action, params) {
    const r = await fetch(`/api/action/${action}`, {
      method: "POST",
      headers: { "X-Aios-Token": token, "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok || body.ok === false) {
      toast(`✗ ${action}: ${body.stderr || body.error || r.status}`);
      throw new Error(action);
    }
    toast(`✓ ${action}`);
    return body;
  },
};
window.aios = aios;

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(t._h);
  t._h = setTimeout(() => { t.hidden = true; }, 6000);
}

const routes = {
  cockpit: () => import("/panels/cockpit.js"),
  gate: () => import("/panels/gate.js"),
  standup: () => import("/panels/standup.js"),
  mirror: () => import("/panels/mirror.js"),
  cost: () => import("/panels/cost.js"),
};

let current = null;
async function route() {
  const name = (location.hash.replace(/^#\//, "") || "cockpit").split("/")[0];
  current = routes[name] ? name : "cockpit";
  document.querySelectorAll("#nav a").forEach((a) =>
    a.classList.toggle("active", a.hash === `#/${current}`));
  const mod = await routes[current]();
  const main = document.getElementById("main");
  main.innerHTML = "";
  await mod.default(main, aios);
  select(0);
}
window.addEventListener("hashchange", route);

// keyboard: j/k move selection, Enter activates, panel jump keys
let selIdx = 0;
function rows() { return [...document.querySelectorAll("#main .row")]; }
function select(i) {
  const r = rows();
  if (!r.length) return;
  selIdx = Math.max(0, Math.min(i, r.length - 1));
  r.forEach((el, n) => el.classList.toggle("sel", n === selIdx));
  r[selIdx].scrollIntoView({ block: "nearest" });
}
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.metaKey || e.ctrlKey) return;
  if (e.key === "j") select(selIdx + 1);
  else if (e.key === "k") select(selIdx - 1);
  else if (e.key === "Enter") rows()[selIdx]?.querySelector("button, a")?.click();
  else {
    const nav = document.querySelector(`#nav a[data-key="${e.key}"]`);
    if (nav) location.hash = nav.hash;
  }
});

// mtime polling: re-render the current panel when its backing files change
let lastM = null;
setInterval(async () => {
  try {
    const m = await aios.get("/api/mtimes");
    const s = JSON.stringify(m);
    if (lastM !== null && s !== lastM) route();
    lastM = s;
    const newest = Math.max(...Object.values(m).filter(Boolean));
    document.getElementById("age").textContent =
      newest ? `data ${Math.round((Date.now() / 1000 - newest) / 60)}m old` : "no data";
  } catch { /* server gone; leave last render */ }
}, 4000);

route();
