// v2 shell — the IA rebuild: Overview / Board / Files / Usage, then Pipelines (AIOS · Software ·
// Marketing · Ops). Rail + mobile drawer, hash router, live age badge. Pages build page-by-page;
// Overview is live, the rest fill in.
import { html, render, useEffect, useState, useRef, api, toast } from "/lib.js";
import { OverviewView } from "/views/overview.js";
import { BoardView } from "/views/board.js";
import { AiosView } from "/views/aios.js";
import { SoftwareView } from "/views/software.js";
import { UsageView } from "/views/usage.js";
import { FilesView } from "/views/files.js";

const Logo = () => html`
  <svg class="logo" viewBox="0 0 1000 1000" aria-label="AIOS logo">
    <g fill="none" stroke-width="62" stroke-linecap="round" stroke-linejoin="round" transform="rotate(-90 500 500)">
      <circle cx="500" cy="500" r="320" pathLength="100" stroke-dasharray="84 16" stroke-dashoffset="7"/>
      <circle cx="500" cy="500" r="205" pathLength="100" stroke-dasharray="78 22" stroke-dashoffset="26"/>
      <circle cx="500" cy="500" r="82" pathLength="100" stroke-dasharray="80 20" stroke-dashoffset="10"/>
    </g>
  </svg>`;

// rail glyphs
const ICONS = {
  overview: html`<svg class="ic" viewBox="0 0 16 16"><rect x="2" y="2" width="5" height="5" rx="1"/><rect x="9" y="2" width="5" height="3.2" rx="1"/><rect x="9" y="6.6" width="5" height="7.4" rx="1"/><rect x="2" y="8.6" width="5" height="5.4" rx="1"/></svg>`,
  board: html`<svg class="ic" viewBox="0 0 16 16"><rect x="1.8" y="2.5" width="3.4" height="11" rx="1"/><rect x="6.3" y="2.5" width="3.4" height="7.5" rx="1"/><rect x="10.8" y="2.5" width="3.4" height="9.5" rx="1"/></svg>`,
  files: html`<svg class="ic" viewBox="0 0 16 16"><path d="M2 4.5a1 1 0 0 1 1-1h2.5l1.3 1.4H13a1 1 0 0 1 1 1v6.6a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1z"/></svg>`,
  usage: html`<svg class="ic" viewBox="0 0 16 16"><path d="M2.5 13.5V7M6.5 13.5V3M10.5 13.5V8.5M14 13.5H1.5"/></svg>`,
  aios: html`<svg class="ic" viewBox="0 0 16 16"><path d="M1.5 8h2.5l1.3-4 2.6 8 1.3-4H14"/></svg>`,
  software: html`<svg class="ic" viewBox="0 0 16 16"><path d="M1.5 13.5h13M3 13.5V6l2.6 1.8V6l2.6 1.8V6l2.8 1.8v5.7M5 13.5v-2M8.3 13.5v-2M11.3 13.5v-2"/></svg>`,
  marketing: html`<svg class="ic" viewBox="0 0 16 16"><path d="M1.5 13.5h13M3 13.5V6l2.6 1.8V6l2.6 1.8V6l2.8 1.8v5.7"/></svg>`,
  ops: html`<svg class="ic" viewBox="0 0 16 16"><path d="M1.5 13.5h13M3 13.5V6l2.6 1.8V6l2.6 1.8V6l2.8 1.8v5.7"/></svg>`,
};

// pages still building in this rebuild — a clean placeholder until each lands (page-by-page)
const Building = ({ name, note }) => html`
  <section class="view">
    <div class="viewhead"><h1>${name}</h1><span class="sub">building</span></div>
    <p class="stub">${note || "This page is being rebuilt against the live data. Overview is live now; the rest land page by page."}</p>
  </section>`;
const stub = (name, note) => () => html`<${Building} name=${name} note=${note} />`;

const NAV = [
  { key: "overview", label: "Overview", view: OverviewView },
  { key: "board", label: "Board", view: BoardView },
  { key: "files", label: "Files", view: FilesView },
  { key: "usage", label: "Usage", view: UsageView },
  { section: "pipelines" },
  { key: "aios", label: "AIOS", view: AiosView },
  { key: "software", label: "Software", view: SoftwareView },
  { key: "marketing", label: "Marketing", soon: "soon" },
  { key: "ops", label: "Ops", soon: "soon" },
];
const ITEMS = NAV.filter((n) => n.key);
const VIEW = Object.fromEntries(ITEMS.filter((n) => n.view).map((n) => [n.key, n.view]));
const ROUTABLE = new Set(ITEMS.filter((n) => !n.soon).map((n) => n.key));

function routeName() {
  const n = (location.hash.replace(/^#\//, "") || "overview").split("/")[0];
  return ROUTABLE.has(n) ? n : "overview";
}

function Shell() {
  const [route, setRoute] = useState(routeName());
  const [collapsed, setCollapsed] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [narrow, setNarrow] = useState(window.matchMedia("(max-width: 860px)").matches);
  const [ageTxt, setAgeTxt] = useState("");
  const [inboxCount, setInboxCount] = useState(0);
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
        setInboxCount((h.held || []).length);   // the gate badge on the AIOS nav item
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
        if (e.key === "o") { location.hash = "#/overview"; return; }
        if (e.key === "b") { location.hash = "#/board"; return; }
        if (e.key === "f") { location.hash = "#/files"; return; }
        if (e.key === "a") { location.hash = "#/aios"; return; }
        if (e.key === "s") { location.hash = "#/software"; return; }
        if (e.key === "u") { location.hash = "#/usage"; return; }
      }
      if (e.key === "g") { chord.current = "g"; setTimeout(() => { chord.current = null; }, 900); return; }
      if (e.key === "[") setCollapsed((c) => !c);
      else if (e.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const go = (n) => { location.hash = `#/${n}`; };
  const View = VIEW[route] || OverviewView;

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
      ${NAV.map((n) => n.section
        ? html`<div class="navsec" key=${"sec-" + n.section}>${n.section}</div>`
        : html`
        <button class="nav-item ${n.soon ? "soon" : ""}" key=${n.key}
                aria-selected=${String(!n.soon && route === n.key)}
                title=${n.label}
                onClick=${() => { if (!n.soon) go(n.key); }}>
          ${ICONS[n.key]}
          <span class="lbl">${n.label}</span>
          ${n.key === "aios" && inboxCount ? html`<span class="count">${inboxCount}</span>` : null}
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
      <span class="k deskonly"><kbd>g</kbd><kbd>o</kbd> overview · <kbd>g</kbd><kbd>b</kbd> board</span>
      <span class="k deskonly"><kbd>[</kbd> collapse menu · <kbd>T</kbd> collapse lane</span>
    </div>

    <div id="toast" hidden></div>`;
}

render(html`<${Shell} />`, document.getElementById("root"));
