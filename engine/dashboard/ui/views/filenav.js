// Cross-page "open this file in Files" bus + helpers to turn file paths inside text into links.
// Any object that maps to a real source file can call openInFiles(envRelPath) to switch to the
// Files page and open that file. FilesView drains a pending request on mount and subscribes for
// live requests while it's already active.
let pending = null;
const subs = new Set();

export function openInFiles(path) {
  if (!path) return;
  pending = path;
  subs.forEach((fn) => { try { fn(path); } catch (e) { /* ignore */ } });
  if (location.hash !== "#/files") location.hash = "#/files";
}
export function takePendingFile() { const p = pending; pending = null; return p; }
export function subscribeOpenFile(fn) { subs.add(fn); return () => subs.delete(fn); }

// vault-relative KB paths (02_FamilyOffice/…) resolve under the vault dir; state/repo paths are env-rel
export function toEnvFile(vaultRel, p) {
  if (!p) return p;
  p = String(p).replace(/\\/g, "/");
  if (vaultRel && !p.startsWith(vaultRel + "/") && /^\d\d_/.test(p)) return vaultRel + "/" + p;
  return p;
}

const escH = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
// one token: a URL (→ external link) OR a file-path (env top-dir / KB folder + real extension → Files).
// URL alternative comes first so a path inside a URL isn't split out on its own.
const TOKEN_RE = /(https?:\/\/[^\s<>"'`)\]]+)|((?:\d\d_[A-Za-z][\w-]*|state|Projects|SecondBrain|Memory|profile|docs|Scripts|_tools|Artifacts|Scheduled|engine)\/[\w./-]+\.[A-Za-z0-9]{1,8})/g;

// Escape text; link every URL (opens externally) and every file path (opens in the Files editor).
export function linkifyPaths(text, vaultRel) {
  if (text == null) return "";
  const s = String(text);
  let out = "", last = 0, m;
  TOKEN_RE.lastIndex = 0;
  while ((m = TOKEN_RE.exec(s)) !== null) {
    out += escH(s.slice(last, m.index));
    if (m[1]) {                                   // external URL
      let url = m[1], trail = "";
      const t = url.match(/[.,;:!?]+$/);          // don't swallow trailing sentence punctuation
      if (t) { trail = url.slice(-t[0].length); url = url.slice(0, -t[0].length); }
      out += `<a class="urllink" href="${escH(url)}" target="_blank" rel="noopener noreferrer">${escH(url)}</a>` + escH(trail);
    } else {                                      // in-repo file path
      out += `<span class="pathlink" data-path="${escH(toEnvFile(vaultRel, m[2]))}">${escH(m[2])}</span>`;
    }
    last = m.index + m[0].length;
  }
  out += escH(s.slice(last));
  return out;
}

// Attach to a container's onClick: opens the Files editor when a .pathlink inside it is clicked.
export function onPathClick(e) {
  const el = e.target.closest && e.target.closest(".pathlink");
  if (el && el.dataset.path) { e.preventDefault(); e.stopPropagation(); openInFiles(el.dataset.path); }
}
