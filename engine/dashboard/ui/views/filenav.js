// Cross-page "open this file in Files" bus. Any object that maps to a real source file can call
// openInFiles(envRelPath) to switch to the Files page and open that file. FilesView drains a
// pending request on mount and subscribes for live requests while it's already the active page.
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
