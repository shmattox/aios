// Files — a full-bleed editor: a locked file TREE on the left, a tabbed editor on the right.
// Toolbar (search / new file / refresh / collapse) up top. Tabs and Save/Revert share one row.
// Backend (files_state) keeps everything inside the env root and refuses secrets/binaries/oversize.
import { html, api, useState, useEffect, useRef, toast } from "/lib.js";
import { takePendingFile, subscribeOpenFile } from "./filenav.js";

const base = (p) => (p || "").split("/").pop();
const ext = (p) => { const b = base(p); const i = b.lastIndexOf("."); return i > 0 ? b.slice(i + 1).toLowerCase() : ""; };

const IC = {
  search: html`<svg viewBox="0 0 16 16" class="ic"><circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5 14 14"/></svg>`,
  add: html`<svg viewBox="0 0 16 16" class="ic"><path d="M8 3.5v9M3.5 8h9"/></svg>`,
  refresh: html`<svg viewBox="0 0 16 16" class="ic"><path d="M13 8a5 5 0 1 1-1.5-3.5M13 2.5V5h-2.5"/></svg>`,
  collapse: html`<svg viewBox="0 0 16 16" class="ic"><path d="M4.5 9.5 8 6l3.5 3.5"/></svg>`,
};

async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "X-Aios-Token": api.token, "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const b = await r.json().catch(() => ({}));
  return { ok: r.ok && b.ok, body: b };
}

// ---- safe syntax highlight (escape then wrap) --------------------------------------
const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const MD = new Set(["md", "markdown", "txt", "mdx", ""]);
function highlight(text, e) {
  const md = MD.has(e);
  return esc(text).split("\n").map((line) => {
    if (md) {
      if (/^#{1,6}\s/.test(line)) return `<span class="h">${line}</span>`;
      return line.replace(/(`[^`]+`)/g, '<span class="s">$1</span>').replace(/(\*\*[^*]+\*\*)/g, '<span class="h">$1</span>');
    }
    const cm = line.match(/^(\s*)((?:#|\/\/).*)$/);
    if (cm) return `${cm[1]}<span class="c">${cm[2]}</span>`;
    return line.replace(/("[^"]*"|'[^']*')/g, '<span class="s">$1</span>').replace(/((?:#|\/\/).*)$/, '<span class="c">$1</span>');
  }).join("\n");
}

function TreeNode({ node, depth, expanded, childrenOf, onToggle, onOpen, activePath }) {
  const isDir = node.dir, open = expanded.has(node.path), kids = childrenOf[node.path];
  return html`<div>
    <div class="fm-node ${isDir ? "dir" : ""} ${!isDir && node.path === activePath ? "sel" : ""}"
         style="padding-left:${8 + depth * 13}px" tabindex="0"
         onClick=${() => (isDir ? onToggle(node) : onOpen(node.path))}>
      <span class="tw">${isDir ? (open ? "▾" : "▸") : ""}</span><span class="fnm">${node.name}</span>
    </div>
    ${isDir && open ? html`<div>${kids
        ? kids.map((c) => html`<${TreeNode} node=${c} depth=${depth + 1} expanded=${expanded}
            childrenOf=${childrenOf} onToggle=${onToggle} onOpen=${onOpen} activePath=${activePath} key=${c.path} />`)
        : html`<div class="fm-node" style="padding-left:${8 + (depth + 1) * 13}px"><span class="tw"></span><span class="fnm dim">…</span></div>`}</div>` : null}
  </div>`;
}

function Editor({ tab, onChange }) {
  const taRef = useRef(null), hlRef = useRef(null);
  const sync = () => { if (hlRef.current && taRef.current) { hlRef.current.scrollTop = taRef.current.scrollTop; } };
  if (!tab) return html`<div class="fm-empty"><p class="stub">Open a file from the tree to view or edit it.</p></div>`;
  if (tab.error) return html`<div class="fm-empty"><p class="stub">${tab.error}${tab.size ? ` (${tab.size} bytes)` : ""}.</p></div>`;
  return html`<div class="fm-code">
    <pre class="fm-hl" ref=${hlRef} aria-hidden="true" dangerouslySetInnerHTML=${{ __html: highlight(tab.text, ext(tab.path)) + "\n" }}></pre>
    <textarea class="fm-ta" ref=${taRef} spellcheck="false" value=${tab.text}
      onInput=${(e) => onChange(e.target.value)} onScroll=${sync}></textarea>
  </div>`;
}

export function FilesView() {
  const [roots, setRoots] = useState([]);
  const [expanded, setExpanded] = useState(new Set());
  const [childrenOf, setChildrenOf] = useState({});
  const [tabs, setTabs] = useState([]);
  const [active, setActive] = useState(null);
  const [q, setQ] = useState("");
  const [results, setResults] = useState(null);
  const openRef = useRef(new Set());
  const searchTimer = useRef(null);

  const loadRoot = () => api.get("/api/files/tree?path=").then((d) => setRoots(d.entries || [])).catch(() => setRoots([]));
  const loadDir = (path) => api.get(`/api/files/tree?path=${encodeURIComponent(path)}`)
    .then((d) => setChildrenOf((m) => ({ ...m, [path]: d.entries || [] }))).catch(() => setChildrenOf((m) => ({ ...m, [path]: [] })));
  useEffect(() => { loadRoot(); }, []);
  useEffect(() => { openRef.current = new Set(tabs.map((t) => t.path)); }, [tabs]);

  const toggle = (node) => {
    setExpanded((s) => { const n = new Set(s); n.has(node.path) ? n.delete(node.path) : n.add(node.path); return n; });
    if (!childrenOf[node.path]) loadDir(node.path);
  };
  // expand the tree down to `path` (loading each ancestor dir) so the file is revealed + highlighted
  const reveal = (path) => {
    const parts = String(path).split("/").filter(Boolean);
    const dirs = [];
    for (let i = 0; i < parts.length - 1; i++) dirs.push(parts.slice(0, i + 1).join("/"));
    if (!dirs.length) return;
    setExpanded((s) => { const n = new Set(s); dirs.forEach((d) => n.add(d)); return n; });
    dirs.forEach((d) => { if (!childrenOf[d]) loadDir(d); });
    if (q) { setQ(""); setResults(null); }   // leave search mode so the tree (not results) shows
  };
  const openPath = (path) => {
    if (!path) return;
    setActive(path);
    reveal(path);
    if (openRef.current.has(path)) return;
    openRef.current.add(path);
    setTabs((ts) => [...ts, { path, content: null, text: "", loading: true }]);
    api.get(`/api/files/read?path=${encodeURIComponent(path)}`)
      .then((d) => setTabs((ts) => ts.map((t) => t.path === path
        ? (d.error ? { path, error: d.error, size: d.size } : { path, content: d.content, text: d.content }) : t)))
      .catch(() => setTabs((ts) => ts.map((t) => t.path === path ? { path, error: "could not load file" } : t)));
  };
  // once the revealed file's row is rendered in the tree, scroll it into view
  useEffect(() => {
    if (!active) return;
    const t = setTimeout(() => {
      const el = document.querySelector(".fm-tree .fm-node.sel");
      if (el) el.scrollIntoView({ block: "center" });
    }, 250);
    return () => clearTimeout(t);
  }, [active, childrenOf]);
  useEffect(() => { const p = takePendingFile(); if (p) openPath(p); return subscribeOpenFile(openPath); }, []);

  // debounced recursive search
  useEffect(() => {
    clearTimeout(searchTimer.current);
    if (q.trim().length < 2) { setResults(null); return; }
    searchTimer.current = setTimeout(() => {
      api.get(`/api/files/search?q=${encodeURIComponent(q.trim())}`).then((d) => setResults(d.results || [])).catch(() => setResults([]));
    }, 220);
    return () => clearTimeout(searchTimer.current);
  }, [q]);

  const closeTab = (path, e) => {
    e && e.stopPropagation();
    setTabs((ts) => { const i = ts.findIndex((t) => t.path === path); const n = ts.filter((t) => t.path !== path);
      if (active === path) setActive(n.length ? n[Math.min(i, n.length - 1)].path : null); return n; });
  };
  const setText = (path, text) => setTabs((ts) => ts.map((t) => t.path === path ? { ...t, text } : t));
  const cur = tabs.find((t) => t.path === active) || null;
  const dirty = cur && !cur.error && !cur.loading && cur.text !== cur.content;

  const save = async () => {
    if (!cur || !dirty) return;
    const { ok, body } = await post("/api/files/write", { path: cur.path, content: cur.text });
    if (!ok) { toast(`✗ save: ${body.error || "failed"}`); return; }
    toast(`✓ saved ${base(cur.path)}`);
    setTabs((ts) => ts.map((t) => t.path === cur.path ? { ...t, content: cur.text } : t));
  };
  const revert = () => cur && setText(cur.path, cur.content);
  const refresh = () => { setChildrenOf({}); loadRoot(); };
  const collapseAll = () => setExpanded(new Set());
  const newFile = async () => {
    const p = window.prompt("New file path (relative to the environment root):");
    if (!p) return;
    const { ok, body } = await post("/api/files/create", { path: p.trim() });
    if (!ok) { toast(`✗ create: ${body.error || "failed"}`); return; }
    toast(`✓ created ${base(body.path)}`);
    refresh(); openPath(body.path);
  };

  return html`<section class="view fmv">
    <div class="fm-top">
      <h1>Files</h1>
      <label class="fm-search">${IC.search}
        <input type="text" placeholder="Search files…" value=${q} onInput=${(e) => setQ(e.target.value)} />
      </label>
      <div class="fm-tools">
        <button title="New file" onClick=${newFile}>${IC.add}</button>
        <button title="Refresh" onClick=${refresh}>${IC.refresh}</button>
        <button title="Collapse all" onClick=${collapseAll}>${IC.collapse}</button>
      </div>
    </div>
    <div class="fm">
      <div class="fm-tree">
        ${results != null
          ? (results.length
              ? results.map((p) => html`<div class="fm-node ${p === active ? "sel" : ""}" tabindex="0" title=${p}
                    onClick=${() => openPath(p)} key=${p}><span class="tw"></span><span class="fnm">${base(p)}</span>
                    <span class="fdir">${p.split("/").slice(0, -1).join("/") || "."}</span></div>`)
              : html`<p class="stub" style="padding:10px">No files match "${q}".</p>`)
          : (roots.length
              ? roots.map((n) => html`<${TreeNode} node=${n} depth=${0} expanded=${expanded} childrenOf=${childrenOf}
                  onToggle=${toggle} onOpen=${openPath} activePath=${active} key=${n.path} />`)
              : html`<p class="stub" style="padding:10px">Loading…</p>`)}
      </div>
      <div class="fm-pane">
        <div class="fm-tabbar">
          <div class="fm-tabs">
            ${tabs.map((t) => html`<div class="fm-tab ${t.path === active ? "on" : ""}" key=${t.path}
                onClick=${() => setActive(t.path)} title=${t.path}>
              <span class="fm-tnm">${base(t.path)}${t.text !== t.content && !t.error && !t.loading ? " ●" : ""}</span>
              <button class="fm-x" onClick=${(e) => closeTab(t.path, e)} aria-label="close">×</button>
            </div>`)}
          </div>
          <div class="fm-acts">
            <button class="verb" disabled=${!dirty} onClick=${revert}>Revert</button>
            <button class="verb ok" disabled=${!dirty} onClick=${save}>Save</button>
          </div>
        </div>
        <${Editor} tab=${cur} onChange=${(v) => setText(active, v)} />
      </div>
    </div>
  </section>`;
}
