// A109 v2a shell: rail + mobile drawer, hash router, keyboard map, live age badge.
// Inbox is real (Task 7); Board arrives in Task 8. Mirror mounts the working v1
// panel so nothing regresses. Flow/Stats = v2b.
import { html, render, useEffect, useState, useRef, api, toast } from "/lib.js";
import { InboxView } from "/views/inbox.js";
import { BoardView } from "/views/board.js";
import { ActivityView } from "/views/activity.js";
import { FlowView } from "/views/flow.js";

// v1-panel compat: the existing panels/*.js take (mountEl, aios) and may read window.aios.
const aiosCompat = {
  ...api,
  esc: (s) => String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
};
window.aios = aiosCompat;

const Logo = () => html`
  <svg class="logo" viewBox="0 0 1000 1000" aria-label="AIOS logo">
    <g fill="none" stroke-width="62" stroke-linecap="round" stroke-linejoin="round" transform="rotate(-90 500 500)">
      <circle cx="500" cy="500" r="320" pathLength="100" stroke-dasharray="84 16" stroke-dashoffset="7"/>
      <circle cx="500" cy="500" r="205" pathLength="100" stroke-dasharray="78 22" stroke-dashoffset="26"/>
      <circle cx="500" cy="500" r="82" pathLength="100" stroke-dasharray="80 20" stroke-dashoffset="10"/>
    </g>
  </svg>`;

// rail glyphs, ported from the mockup
const ICONS = {
  inbox: html`<svg class="ic" viewBox="0 0 16 16"><path d="M2 9.5h3l1.2 2h3.6l1.2-2h3"/><path d="M3.2 3.5h9.6l1.2 6v3.5H2V9.5z"/></svg>`,
  board: html`<svg class="ic" viewBox="0 0 16 16"><rect x="1.8" y="2.5" width="3.4" height="11" rx="1"/><rect x="6.3" y="2.5" width="3.4" height="7.5" rx="1"/><rect x="10.8" y="2.5" width="3.4" height="9.5" rx="1"/></svg>`,
  activity: html`<svg class="ic" viewBox="0 0 16 16"><path d="M1.5 8.5h3l1.5-5 3 9 1.5-4h3.5"/></svg>`,
  flow: html`<svg class="ic" viewBox="0 0 16 16"><circle cx="3.5" cy="8" r="1.8"/><circle cx="12.5" cy="3.8" r="1.8"/><circle cx="12.5" cy="12.2" r="1.8"/><path d="M5.3 7.2l5.4-2.6M5.3 8.8l5.4 2.6"/></svg>`,
  mirror: html`<svg class="ic" viewBox="0 0 16 16"><rect x="2" y="2.5" width="12" height="11" rx="1"/><path d="M2 6h12M6.5 6v7.5"/></svg>`,
  stats: html`<svg class="ic" viewBox="0 0 16 16"><path d="M3 13V9M8 13V4M13 13V6.5"/></svg>`,
};

// ── views ─────────────────────────────────────────────────
// Inbox (T7) and Board (T8) are real. Flow/Stats = v2b; Mirror mounts the v1 panel.
const Soon = ({ name, note }) => html`
  <section class="view">
    <div class="viewhead"><h1>${name}</h1><span class="sub">v2b</span></div>
    <p class="stub">${note}</p>
  </section>`;

// Mirror: mount the working v1 panel into a plain container (no regression).
function MirrorView() {
  const ref = useRef(null);
  useEffect(() => {
    let alive = true;
    import("/panels/mirror.js")
      .then((mod) => { if (alive && ref.current) mod.default(ref.current, aiosCompat); })
      .catch(() => { if (ref.current) ref.current.innerHTML = "<p class='stub'>Mirror panel unavailable.</p>"; });
    return () => { alive = false; };
  }, []);
  return html`<div ref=${ref}></div>`;
}

const NAV = [
  { key: "inbox", label: "Inbox", view: InboxView },
  { key: "board", label: "Board", view: BoardView },
  { key: "activity", label: "Activity", view: ActivityView },
  { key: "flow", label: "Flow", view: FlowView },
  { key: "mirror", label: "Mirror", view: MirrorView },
  { key: "stats", label: "Stats", soon: "v2b", view: () => html`<${Soon} name="Stats" note="Spend, gate metrics, throughput — v2b." />` },
];
const VIEW = Object.fromEntries(NAV.map((n) => [n.key, n.view]));
const ROUTABLE = new Set(NAV.filter((n) => !n.soon).map((n) => n.key));

function routeName() {
  const n = (location.hash.replace(/^#\//, "") || "inbox").split("/")[0];
  return ROUTABLE.has(n) ? n : "inbox";
}

function Shell() {
  const [route, setRoute] = useState(routeName());
  const [collapsed, setCollapsed] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [narrow, setNarrow] = useState(window.matchMedia("(max-width: 860px)").matches);
  const [ageTxt, setAgeTxt] = useState("");
  const [inboxCount, setInboxCount] = useState(0);
  const [liveCount, setLiveCount] = useState(0);
  const chord = useRef(null);

  // hash routing
  useEffect(() => {
    const onHash = () => { setRoute(routeName()); setMenuOpen(false); };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // breakpoint watcher
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 860px)");
    const on = () => setNarrow(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  // reflect layout state onto <body> (CSS keys off these classes)
  useEffect(() => {
    document.body.classList.toggle("side-collapsed", collapsed);
    document.body.classList.toggle("menu-open", menuOpen);
    document.body.classList.toggle("narrow", narrow);
  }, [collapsed, menuOpen, narrow]);

  // live age badge from /api/mtimes
  useEffect(() => {
    let timer;
    const tick = async () => {
      try {
        const m = await api.get("/api/mtimes");
        const vals = Object.values(m).filter(Boolean);
        const newest = vals.length ? Math.max(...vals) : 0;
        setAgeTxt(newest ? `${Math.max(0, Math.round(Date.now() / 1000 - newest))}s` : "");
        const h = await api.get("/api/held");
        setInboxCount((h.held || []).length);
        const a = await api.get("/api/activity");
        setLiveCount((a.runs || []).filter((r) => r.live).length);
      } catch (e) { /* server gone */ }
      timer = setTimeout(tick, 5000);
    };
    tick();
    return () => clearTimeout(timer);
  }, []);

  // global keyboard map (view-specific keys j/k/e/x/r are added by the views)
  useEffect(() => {
    const onKey = (e) => {
      if (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT") return;
      if (chord.current === "g") {
        chord.current = null;
        if (e.key === "b") { location.hash = "#/board"; return; }
        if (e.key === "i") { location.hash = "#/inbox"; return; }
        if (e.key === "a") { location.hash = "#/activity"; return; }
      }
      if (e.key === "g") { chord.current = "g"; setTimeout(() => { chord.current = null; }, 900); return; }
      if (e.key === "[") setCollapsed((c) => !c);
      else if (e.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const go = (n) => { location.hash = `#/${n}`; };
  const View = VIEW[route] || InboxView;

  return html`
    <header id="topbar">
      <${Logo} />
      <span class="word">AIOS<span class="dot">·</span>v2</span>
      <span class="live"><span class="pulse"></span><span class="num">${ageTxt}</span></span>
      <button id="burger" aria-label="Menu" aria-expanded=${String(menuOpen)} onClick=${() => setMenuOpen((o) => !o)}>
        <svg class="ic" viewBox="0 0 16 16"><path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11"/></svg>
      </button>
    </header>
    <div id="scrim" onClick=${() => setMenuOpen(false)}></div>

    <aside id="side">
      <div class="brand"><${Logo} /><span class="word">AIOS<span class="dot">·</span>v2</span></div>
      ${NAV.map((n) => html`
        <button class="nav-item ${n.soon ? "soon" : ""}" key=${n.key}
                aria-selected=${String(!n.soon && route === n.key)}
                title=${n.label}
                onClick=${() => { if (!n.soon) go(n.key); }}>
          ${ICONS[n.key]}
          <span class="lbl">${n.label}</span>
          ${n.key === "inbox" && inboxCount ? html`<span class="count">${inboxCount}</span>` : null}
          ${n.key === "activity" && liveCount ? html`<span class="count">${liveCount}</span>` : null}
          ${n.soon ? html`<span class="phase">${n.soon}</span>` : null}
        </button>`)}
      <span class="spacer"></span>
      <button id="collapse" title="Collapse menu" onClick=${() => setCollapsed((c) => !c)}>
        <svg class="ic" viewBox="0 0 16 16"><path d="M9.5 4L5.5 8l4 4"/></svg>
        <span class="lbl">Collapse</span>
      </button>
      <div class="live"><span class="pulse"></span><span class="txt">live · SSE · <span class="num">${ageTxt}</span></span></div>
    </aside>

    <div id="content"><main><${View} /></main></div>

    <div id="keys">
      <span class="k"><kbd>j</kbd><kbd>k</kbd> select</span>
      <span class="k"><kbd>e</kbd> approve · <kbd>x</kbd> reject · <kbd>i</kbd> edit · <kbd>r</kbd> respond</span>
      <span class="k deskonly"><kbd>g</kbd><kbd>b</kbd> board · <kbd>g</kbd><kbd>i</kbd> inbox</span>
      <span class="k deskonly"><kbd>[</kbd> collapse menu · <kbd>T</kbd> collapse lane</span>
    </div>

    <div id="toast" hidden></div>`;
}

render(html`<${Shell} />`, document.getElementById("root"));
