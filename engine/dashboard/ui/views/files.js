// Files — browse and edit any text file under the environment root. The backend (files_state)
// keeps the whole thing inside the env root and refuses secrets, binaries, and oversized files;
// this is the face: a breadcrumb + directory listing on the left, a guarded editor on the right.
// Reads /api/files/tree + /api/files/read; saves via POST /api/files/write (token-gated).
import { html, api, useState, useEffect, useRef, toast } from "/lib.js";

const fmtSize = (n) => (n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : n + "B");
const base = (p) => (p || "").split("/").pop();

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

function Crumbs({ dir, go }) {
  const parts = (dir || "").split("/").filter(Boolean);
  return html`<div class="fm-crumbs">
    <button class="fm-crumb" onClick=${() => go("")}>env</button>
    ${parts.map((p, i) => html`<span key=${i}><span class="fm-sep">/</span>
      <button class="fm-crumb" onClick=${() => go(parts.slice(0, i + 1).join("/"))}>${p}</button></span>`)}
  </div>`;
}

function Editor({ sel }) {
  const [file, setFile] = useState(null);
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const gen = useRef(0);

  useEffect(() => {
    if (!sel) { setFile(null); setText(""); return; }
    const g = ++gen.current;
    setFile(null); setText("");
    api.get(`/api/files/read?path=${encodeURIComponent(sel)}`)
      .then((d) => { if (g === gen.current) { setFile(d); setText(d.content || ""); } })
      .catch(() => { if (g === gen.current) setFile({ error: "could not load file" }); });
  }, [sel]);

  if (!sel) return html`<p class="stub">Select a file to view or edit it.</p>`;
  if (!file) return html`<p class="stub">Loading ${base(sel)}…</p>`;
  if (file.error) return html`<article id="detail"><div class="head">
      <div class="chips"><span class="chip id">${sel}</span></div><h1>${base(sel)}</h1>
      <p class="why">${file.error}${file.too_large ? ` (${fmtSize(file.size)})` : ""}.</p></div></article>`;

  const dirty = text !== file.content;
  const save = async () => {
    setSaving(true);
    try { const b = await saveFile(sel, text); setFile({ ...file, content: text, size: b.bytes }); }
    catch (e) { /* toasted */ } finally { setSaving(false); }
  };
  return html`<div class="fm-editor">
    <div class="fm-ehead">
      <span class="fm-path" title=${file.path}>${file.path}</span>
      <span class="fm-meta">${fmtSize(file.size)}${dirty ? html` · <span class="fm-dirty">unsaved</span>` : ""}</span>
      <button class="verb approve" disabled=${!dirty || saving} onClick=${save}>${saving ? "Saving…" : "Save"}</button>
      <button class="verb" disabled=${!dirty || saving} onClick=${() => setText(file.content)}>Revert</button>
    </div>
    <textarea class="fm-area" spellcheck="false" value=${text}
      onInput=${(e) => setText(e.target.value)}></textarea>
  </div>`;
}

export function FilesView() {
  const [dir, setDir] = useState("");
  const [listing, setListing] = useState(null);
  const [sel, setSel] = useState(null);
  const gen = useRef(0);

  useEffect(() => {
    const g = ++gen.current;
    api.get(`/api/files/tree?path=${encodeURIComponent(dir)}`)
      .then((d) => { if (g === gen.current) setListing(d); })
      .catch(() => { if (g === gen.current) setListing({ entries: [] }); });
  }, [dir]);

  const entries = listing?.entries || [];
  const open = (e) => { if (e.dir) { setDir(e.path); } else { setSel(e.path); } };

  return html`<section class="view">
    <div class="viewhead"><h1>Files</h1><span class="sub">browse &amp; edit any file in the environment</span></div>
    <${Crumbs} dir=${dir} go=${setDir} />
    <div class="fm-grid">
      <div class="fm-list">
        ${entries.length ? entries.map((e) => html`
          <div class="fm-row ${!e.dir && e.path === sel ? "sel" : ""}" key=${e.path} tabindex="0"
               aria-current=${String(!e.dir && e.path === sel)} onClick=${() => open(e)}>
            <span class="fm-ic ${e.dir ? "dir" : "file"}">${e.dir ? "▸" : "·"}</span>
            <span class="fm-nm">${e.name}</span>
            ${!e.dir ? html`<span class="fm-sz num">${fmtSize(e.size)}</span>` : null}
          </div>`)
          : html`<p class="stub">Empty directory.</p>`}
      </div>
      <div class="fm-detail"><${Editor} sel=${sel} /></div>
    </div>
  </section>`;
}
