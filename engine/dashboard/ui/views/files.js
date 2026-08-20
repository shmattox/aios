// Files — a regular file-manager UI: a locked, expandable file TREE on the left; a tabbed editor
// on the right (open several files as tabs, syntax-highlighted, Save/Revert per tab). The backend
// (files_state) keeps everything inside the env root and refuses secrets/binaries/oversize.
// Reads /api/files/tree + /api/files/read; saves via POST /api/files/write (token-gated).
import { html, api, useState, useEffect, useRef, toast } from "/lib.js";
import { takePendingFile, subscribeOpenFile } from "./filenav.js";

const base = (p) => (p || "").split("/").pop();
const ext = (p) => { const b = base(p); const i = b.lastIndexOf("."); return i > 0 ? b.slice(i + 1).toLowerCase() : ""; };

async function saveFile(path, content) {
  const r = await fetch("/api/files/write", {
    method: "POST",
    headers: { "X-Aios-Token": api.token, "Content-Type": "application/json" },
    body: JSON.stringify({ path, content }),
  });
  const b = await r.json().catch(() => ({}));
  if (!r.ok || !b.ok) { toast(`✗ save: ${b.error || r.status}`); throw new Error("save"); }
  toast(`✓ saved ${base(b.path)}`);
  return b;
}

// ---- lightweight, safe syntax highlight (escape first, then wrap) --------------------
const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const MD = new Set(["md", "markdown", "txt", "mdx", ""]);
function highlight(text, e) {
  const md = MD.has(e);
  return esc(text).split("\n").map((line) => {
    if (md) {
      if (/^#{1,6}\s/.test(line)) return `<span class="h">${line}</span>`;
      return line.replace(/(`[^`]+`)/g, '<span class="s">$1</span>')
                 .replace(/(\*\*[^*]+\*\*)/g, '<span class="h">$1</span>');
    }
    const cm = line.match(/^(\s*)((?:#|\/\/).*)$/);
    if (cm) return `${cm[1]}<span class="c">${cm[2]}</span>`;
    return line.replace(/("[^"]*"|'[^']*')/g, '<span class="s">$1</span>')
               .replace(/((?:#|\/\/).*)$/, '<span class="c">$1</span>');
  }).join("\n");
}

// ---- the left tree (lazy-expand, persistent) -----------------------------------------
function TreeNode({ node, depth, expanded, childrenOf, onToggle, onOpen, activePath }) {
  const isDir = node.dir;
  const open = expanded.has(node.path);
  const kids = childrenOf[node.path];
  return html`<div>
    <div class="fm-node ${isDir ? "dir" : ""} ${!isDir && node.path === activePath ? "sel" : ""}"
         style="padding-left:${8 + depth * 13}px" tabindex="0"
         onClick=${() => (isDir ? onToggle(node) : onOpen(node))}>
      <span class="tw">${isDir ? (open ? "▾" : "▸") : ""}</span>
      <span class="fnm">${node.name}</span>
    </div>
    ${isDir && open ? html`<div>${kids
        ? kids.map((c) => html`<${TreeNode} node=${c} depth=${depth + 1} expanded=${expanded}
            childrenOf=${childrenOf} onToggle=${onToggle} onOpen=${onOpen} activePath=${activePath} key=${c.path} />`)
        : html`<div class="fm-node" style="padding-left:${8 + (depth + 1) * 13}px"><span class="tw"></span><span class="fnm dim">…</span></div>`}</div>` : null}
  </div>`;
}

// ---- the tabbed editor pane ----------------------------------------------------------
function Editor({ tab, onChange, onSave, onRevert }) {
  const taRef = useRef(null), hlRef = useRef(null);
  const syncScroll = () => { if (hlRef.current && taRef.current) { hlRef.current.scrollTop = taRef.current.scrollTop; hlRef.current.scrollLeft = taRef.current.scrollLeft; } };
  if (!tab) return html`<div class="fm-empty"><p class="stub">Open a file from the tree to view or edit it.</p></div>`;
  if (tab.error) return html`<div class="fm-empty"><p class="stub">${tab.error}${tab.size ? ` (${tab.size} bytes)` : ""}.</p></div>`;
  const dirty = tab.text !== tab.content;
  return html`<div class="fm-main">
    <div class="fm-bar">
      <div class="fm-path" title=${tab.path}>${tab.path}${dirty ? html` <span class="fm-dot">●</span>` : ""}</div>
      <div class="fm-acts">
        <button class="verb" disabled=${!dirty} onClick=${onRevert}>Revert</button>
        <button class="verb ok" disabled=${!dirty} onClick=${onSave}>Save</button>
      </div>
    </div>
    <div class="fm-code">
      <pre class="fm-hl" ref=${hlRef} aria-hidden="true"
        dangerouslySetInnerHTML=${{ __html: highlight(tab.text, ext(tab.path)) + "\n" }}></pre>
      <textarea class="fm-ta" ref=${taRef} spellcheck="false" value=${tab.text}
        onInput=${(e) => onChange(e.target.value)} onScroll=${syncScroll}></textarea>
    </div>
  </div>`;
}

export function FilesView() {
  const [roots, setRoots] = useState([]);
  const [expanded, setExpanded] = useState(new Set());
  const [childrenOf, setChildrenOf] = useState({});   // dirPath -> entries
  const [tabs, setTabs] = useState([]);               // [{path, content, text, error?, size?}]
  const [active, setActive] = useState(null);
  const openRef = useRef(new Set());                   // open tab paths (stable across closures)

  const loadDir = (path) => api.get(`/api/files/tree?path=${encodeURIComponent(path)}`)
    .then((d) => setChildrenOf((m) => ({ ...m, [path]: d.entries || [] })))
    .catch(() => setChildrenOf((m) => ({ ...m, [path]: [] })));

  useEffect(() => { api.get("/api/files/tree?path=").then((d) => setRoots(d.entries || [])).catch(() => setRoots([])); }, []);

  const toggle = (node) => {
    setExpanded((s) => { const n = new Set(s); n.has(node.path) ? n.delete(node.path) : n.add(node.path); return n; });
    if (!childrenOf[node.path]) loadDir(node.path);
  };

  useEffect(() => { openRef.current = new Set(tabs.map((t) => t.path)); }, [tabs]);

  const openPath = (path) => {
    if (!path) return;
    setActive(path);
    if (openRef.current.has(path)) return;             // already open — just focus it
    openRef.current.add(path);
    setTabs((ts) => [...ts, { path, content: null, text: "", loading: true }]);
    api.get(`/api/files/read?path=${encodeURIComponent(path)}`)
      .then((d) => setTabs((ts) => ts.map((t) => t.path === path
        ? (d.error ? { path, error: d.error, size: d.size } : { path, content: d.content, text: d.content })
        : t)))
      .catch(() => setTabs((ts) => ts.map((t) => t.path === path ? { path, error: "could not load file" } : t)));
  };
  const open = (node) => openPath(node.path);

  // open a file requested from another page (a source-file link), now and on later requests
  useEffect(() => {
    const p = takePendingFile();
    if (p) openPath(p);
    return subscribeOpenFile((path) => openPath(path));
  }, []);

  const closeTab = (path, e) => {
    e && e.stopPropagation();
    setTabs((ts) => { const i = ts.findIndex((t) => t.path === path); const n = ts.filter((t) => t.path !== path);
      if (active === path) setActive(n.length ? (n[Math.min(i, n.length - 1)].path) : null);
      return n; });
  };
  const setText = (path, text) => setTabs((ts) => ts.map((t) => t.path === path ? { ...t, text } : t));
  const cur = tabs.find((t) => t.path === active) || null;

  const save = async () => {
    if (!cur) return;
    try { const b = await saveFile(cur.path, cur.text); setTabs((ts) => ts.map((t) => t.path === cur.path ? { ...t, content: cur.text, size: b.bytes } : t)); }
    catch (e) { /* toasted */ }
  };
  const revert = () => cur && setText(cur.path, cur.content);

  return html`<section class="view fmv">
    <div class="viewhead"><h1>Files</h1><span class="sub">browse and edit any file in the environment</span></div>
    <div class="fm">
      <div class="fm-tree">
        ${roots.length ? roots.map((n) => html`<${TreeNode} node=${n} depth=${0} expanded=${expanded}
            childrenOf=${childrenOf} onToggle=${toggle} onOpen=${open} activePath=${active} key=${n.path} />`)
          : html`<p class="stub" style="padding:10px">Loading…</p>`}
      </div>
      <div class="fm-pane">
        ${tabs.length ? html`<div class="fm-tabs">
          ${tabs.map((t) => html`<div class="fm-tab ${t.path === active ? "on" : ""}" key=${t.path}
              onClick=${() => setActive(t.path)} title=${t.path}>
            <span class="fm-tnm">${base(t.path)}${t.text !== t.content && !t.error && !t.loading ? " ●" : ""}</span>
            <button class="fm-x" onClick=${(e) => closeTab(t.path, e)} aria-label="close">×</button>
          </div>`)}
        </div>` : null}
        <${Editor} tab=${cur} onChange=${(v) => setText(active, v)} onSave=${save} onRevert=${revert} />
      </div>
    </div>
  </section>`;
}
