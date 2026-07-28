// A109 v2a — Inbox view: cockpit strip (fold-in) + needs-you list + the one Card
// in a detail pane (>=860px) or inline accordion (<860px). The mockup's imperative
// place()/select() ownership logic becomes declarative conditional rendering here.
import { html, api, useState, useEffect, useRef, toast } from "/lib.js";
import { Card, siloClass } from "./card.js";

const REPLY = { respond: 1, append: 1, comment: 1 };

function useNarrow() {
  const [narrow, setNarrow] = useState(window.matchMedia("(max-width: 860px)").matches);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 860px)");
    const on = () => setNarrow(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return narrow;
}

function oldestAge(held) {
  const ages = held.map((h) => h._age_s || h.age_s).filter((n) => typeof n === "number");
  if (!ages.length) return "";
  const d = Math.max(...ages) / 86400;
  return d >= 1 ? `${Math.round(d)}d` : `${Math.round(Math.max(...ages) / 3600)}h`;
}

function CockpitStrip({ brief }) {
  const bubbles = (brief.headline_bubbles || []).map((b) =>
    typeof b === "string" ? b : (b && (b.label || b.text)) || "").filter(Boolean);
  const health = brief.health_lines && typeof brief.health_lines === "object"
    ? Object.values(brief.health_lines).filter((v) => typeof v === "string" && v.trim())
    : [];
  const age = brief._age_s != null ? `${Math.max(0, Math.round(brief._age_s))}s old` : "";
  if (!bubbles.length && !health.length) return null;
  return html`<div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:2px 2px 12px;border-bottom:1px solid var(--border);margin-bottom:12px">
    ${bubbles.map((b) => html`<span class="chip" key=${b}>${b}</span>`)}
    ${age ? html`<span class="chip" style="margin-left:auto">brief ${age}</span>` : null}
    ${health.map((hl) => html`<div style="flex-basis:100%;color:var(--text-2);font-size:var(--fs-xs)" key=${hl}>${hl}</div>`)}
  </div>`;
}

export function InboxView() {
  const [brief, setBrief] = useState(null);
  const [held, setHeld] = useState([]);
  const [sel, setSel] = useState(0);
  const [open, setOpen] = useState(true); // narrow accordion open state for the selected row
  const narrow = useNarrow();
  const selRef = useRef(0);
  selRef.current = sel;

  const load = () => api.get("/api/brief")
    .then((b) => {
      // held rows carry no draft_index over /api/brief — the array index IS the /api/draft?i= key
      const rows = (b.held || []).map((h, i) => ({ ...h, draft_index: h.draft_index != null ? h.draft_index : i }));
      setBrief(b); setHeld(rows);
      setSel((s) => Math.min(s, Math.max(0, rows.length - 1)));
    })
    .catch(() => { setBrief({}); setHeld([]); });

  useEffect(() => { load(); }, []);
  // live refresh: re-load when the brief cache or queue changes (SSE, poll fallback)
  useEffect(() => {
    let es, timer, last = {};
    const poll = async () => {
      try { const m = await api.get("/api/mtimes"); if (m.brief !== last.brief || m.queue !== last.queue) { last = m; load(); } } catch (e) {}
      timer = setTimeout(poll, 5000);
    };
    try {
      es = new EventSource("/api/events");
      es.addEventListener("change", (ev) => {
        const c = JSON.parse(ev.data).changed || [];
        if (c.includes("brief") || c.includes("queue")) load();
      });
      es.onerror = () => { es.close(); poll(); };
    } catch (e) { poll(); }
    return () => { es && es.close(); clearTimeout(timer); };
  }, []);

  async function act(item, a, params) {
    try {
      if (a === "approve") { await api.post("gate_ship", { id: item.id }); load(); }
      else if (a === "reject") {
        const reason = window.prompt("Reject reason:");
        if (!reason) return;
        await api.post("gate_reject", { id: item.id, reason }); load();
      } else if (a === "dismiss") {
        const reason = window.prompt("Dismiss reason:") || "below the worthiness bar";
        await api.post("dismiss", { id: item.id, reason }); load();
      } else if (a === "gate_edit") {
        await api.post("gate_edit", { id: item.id, content: params.content }); load();
      } else if (REPLY[a]) {
        await api.post("reply", { target_id: item.id, reply_kind: a, text: params.text });
      } else toast(`${a} — arrives in a later phase`);
    } catch (e) { /* api.post already toasted the failure */ }
  }

  // keyboard: j/k move, e/x/r act on the selected card (inert while typing)
  useEffect(() => {
    const onKey = (e) => {
      if (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT" || e.metaKey || e.ctrlKey) return;
      const list = held;
      if (!list.length) return;
      const i = selRef.current;
      if (e.key === "j") { setSel(Math.min(i + 1, list.length - 1)); setOpen(true); }
      else if (e.key === "k") { setSel(Math.max(i - 1, 0)); setOpen(true); }
      else if (e.key === "e") act(list[i], "approve", {});
      else if (e.key === "x") act(list[i], "reject", {});
      else if (e.key === "r") { /* respond handled inside the card; open it by focusing */ setOpen(true); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [held]);

  if (brief == null) return html`<section class="view"><p class="stub">Loading…</p></section>`;

  const selItem = held[sel];
  const rowClick = (i) => {
    if (narrow && i === sel) { setOpen((o) => !o); return; }
    setSel(i); setOpen(true);
  };

  return html`<section class="view">
    <div class="viewhead"><h1>Needs you</h1>
      <span class="sub">${held.length} open${held.length ? html` · oldest <span class="num">${oldestAge(held)}</span>` : ""}</span></div>
    <${CockpitStrip} brief=${brief} />
    ${held.length === 0
      ? html`<p class="stub">Review lane clear ✓ — nothing needs you right now.</p>`
      : html`<div id="inbox">
          <div id="cards">
            ${held.map((it, i) => html`
              <div class="card-row" key=${it.id} tabindex="0" data-idx=${i}
                   aria-current=${String(i === sel)}
                   onClick=${() => rowClick(i)}
                   onKeyDown=${(e) => { if (e.key === "Enter") rowClick(i); }}>
                <span class="silo ${siloClass(it.kb)}"></span>
                <span class="t"><span class="title">${it.title || it.id}</span>
                  <span class="sub">${it.kb || ""}${it.lane ? ` · ${it.lane} hold` : ""}${it.recommended ? ` · rec ${it.recommended}` : ""}</span></span>
                <span class="age num">${it._age_s ? Math.round(it._age_s / 3600) + "h" : ""}</span>
                <span class="disclose">▸</span>
              </div>
              ${narrow && i === sel && open
                ? html`<${Card} item=${it} station="needs_you" onAction=${(a, p) => act(it, a, p)} key=${"c" + it.id} />`
                : null}`)}
          </div>
          ${!narrow && selItem
            ? html`<div><${Card} item=${selItem} station="needs_you" onAction=${(a, p) => act(selItem, a, p)} /></div>`
            : null}
        </div>`}
  </section>`;
}
