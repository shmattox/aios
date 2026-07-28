// A109 v2a shared lib: Preact/htm binding, token-carrying fetch, live hook, toast.
// Vendored, no build step (see vendor/LICENSES.md).
import { h, render } from "/vendor/preact.mjs";
import { useEffect, useState, useRef } from "/vendor/hooks.mjs";
import htm from "/vendor/htm.mjs";

export const html = htm.bind(h);
export { h, render, useEffect, useState, useRef };

const token = document.querySelector('meta[name="aios-token"]').content;

export function toast(msg) {
  let t = document.getElementById("toast");
  if (!t) { t = Object.assign(document.createElement("div"), { id: "toast" }); document.body.append(t); }
  t.textContent = msg; t.hidden = false;
  clearTimeout(t._h); t._h = setTimeout(() => { t.hidden = true; }, 5200);
}

export const api = {
  token,
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
    if (!r.ok || body.ok === false) { toast(`✗ ${action}: ${body.stderr || body.error || r.status}`); throw new Error(action); }
    toast(`✓ ${action}`);
    return body;
  },
};

// SSE with poll fallback: cb(changedNames[]) fires on any change to `surfaces`.
export function useLive(surfaces, cb) {
  const errs = useRef(0);
  useEffect(() => {
    let es, timer, last = {};
    const poll = async () => {
      try {
        const m = await api.get("/api/mtimes");
        const changed = surfaces.filter((s) => m[s] !== last[s]);
        last = m;
        if (changed.length) cb(changed);
      } catch (e) { /* server gone; keep trying */ }
      timer = setTimeout(poll, 5000);
    };
    try {
      es = new EventSource("/api/events");
      es.addEventListener("change", (ev) => {
        const changed = JSON.parse(ev.data).changed.filter((s) => surfaces.includes(s));
        if (changed.length) cb(changed);
      });
      es.onerror = () => { if (++errs.current >= 2) { es.close(); poll(); } };
    } catch (e) { poll(); }
    return () => { es && es.close(); clearTimeout(timer); };
  }, []);
}
