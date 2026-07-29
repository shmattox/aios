// A109 v2a — Inbox view: the "Needs you" front door. The list is the real needs-you set —
// the brief's `act` items (tasks / walk questions / factory flags) PLUS gate `held` drafts —
// rendered as rows into a detail pane (>=860px) or inline accordion (<860px). A compact count
// strip sits under the header; system-health lines live on other surfaces, not here.
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

function ageStr(s) {
  if (typeof s !== "number") return "";
  return s >= 86400 ? `${Math.round(s / 86400)}d` : `${Math.round(s / 3600)}h`;
}
function oldestAge(items) {
  const ages = items.map((h) => h._age_s).filter((n) => typeof n === "number");
  return ages.length ? ageStr(Math.max(...ages)) : "";
}

// Build the unified needs-you list from the brief: gate holds first (a decision is waiting),
// then the act items (tasks / walk questions / flags). Each row is tagged _kind for the Card.
function buildItems(brief) {
  const held = (brief.held || []).map((h, i) => ({
    ...h, _kind: "held", draft_index: h.draft_index != null ? h.draft_index : i,
    _silo: siloClass(h.kb), _sub: `${h.kb || ""}${h.lane ? ` · ${h.lane} hold` : ""}${h.recommended ? ` · rec ${h.recommended}` : ""}`,
  }));
  const act = (brief.act || []).map((a) => ({
    ...a, _kind: "act", _silo: siloClass(a.domain || a.kb),
    _sub: `${a.domain || ""}${a.urgency && a.urgency !== "normal" ? ` · ${a.urgency}` : ""}`,
  }));
  // dev/GM/env backlog items the factory flagged as needing Seth (server `dev` list) —
  // read-only pointers so the inbox is a true cross-silo needs-you front door.
  const dev = (brief.dev || []).map((d) => ({
    ...d, _kind: "dev", _silo: siloClass(d.repo),
    _sub: `${d.repo || ""}${d.state_badge ? ` · ${d.state_badge}` : ""}${d.gate_human ? " · ⛔ GATE" : ""}`,
  }));
  return [...held, ...act, ...dev];
}

// compact count chips (no health-line wall — the mockup's header was clean)
function CountStrip({ brief }) {
  const bubbles = (brief.headline_bubbles || [])
    .map((b) => (typeof b === "string" ? b : (b && (b.label || b.text)) || "")).filter(Boolean);
  if (!bubbles.length) return null;
  return html`<div class="countstrip">${bubbles.map((b) => html`<span class="chip" key=${b}>${b}</span>`)}
    ${brief._age_s != null ? html`<span class="chip" style="margin-left:auto">brief ${ageStr(brief._age_s) || "fresh"}</span>` : null}</div>`;
}

export function InboxView() {
  const [brief, setBrief] = useState(null);
  const [items, setItems] = useState([]);
  const [sel, setSel] = useState(0);
  const [open, setOpen] = useState(true);
  const narrow = useNarrow();
  const selRef = useRef(0);
  selRef.current = sel;

  const load = () => api.get("/api/brief")
    .then((b) => { setBrief(b); const it = buildItems(b); setItems(it); setSel((s) => Math.min(s, Math.max(0, it.length - 1))); })
    .catch(() => { setBrief({}); setItems([]); });

  useEffect(() => { load(); }, []);
  useEffect(() => {
    let es, timer, last = {};
    const poll = async () => {
      try { const m = await api.get("/api/mtimes"); if (m.brief !== last.brief || m.queue !== last.queue) { last = m; load(); } } catch (e) {}
      timer = setTimeout(poll, 5000);
    };
    try {
      es = new EventSource("/api/events");
      es.addEventListener("change", (ev) => { const c = JSON.parse(ev.data).changed || []; if (c.includes("brief") || c.includes("queue")) load(); });
      es.onerror = () => { es.close(); poll(); };
    } catch (e) { poll(); }
    return () => { es && es.close(); clearTimeout(timer); };
  }, []);

  async function act(item, a, params) {
    try {
      if (a === "approve") { await api.post("gate_ship", { id: item.id }); load(); }
      else if (a === "reject") { const r = window.prompt("Reject reason:"); if (!r) return; await api.post("gate_reject", { id: item.id, reason: r }); load(); }
      else if (a === "dismiss") { const r = window.prompt("Dismiss reason:") || "below the worthiness bar"; await api.post("dismiss", { id: item.id, reason: r }); load(); }
      else if (a === "done") { await api.post("walk_decision", { item_id: item.item_id || item.id, station: "act", choice: "done", action: "closed from dashboard" }); load(); }
      else if (a === "gate_edit") { await api.post("gate_edit", { id: item.id, content: params.content }); load(); }
      else if (REPLY[a]) { await api.post("reply", { target_id: item.item_id || item.id, reply_kind: a, text: params.text }); }
      else toast(`${a} — arrives in a later phase`);
    } catch (e) { /* api.post toasted */ }
  }

  useEffect(() => {
    const onKey = (e) => {
      if (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT" || e.metaKey || e.ctrlKey) return;
      if (!items.length) return;
      const i = selRef.current, it = items[i];
      if (e.key === "j") { setSel(Math.min(i + 1, items.length - 1)); setOpen(true); }
      else if (e.key === "k") { setSel(Math.max(i - 1, 0)); setOpen(true); }
      else if (e.key === "e" && it._kind === "held") act(it, "approve", {});
      else if (e.key === "x" && it._kind === "held") act(it, "reject", {});
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [items]);

  if (brief == null) return html`<section class="view"><p class="stub">Loading…</p></section>`;

  const selItem = items[sel];
  const rowClick = (i) => { if (narrow && i === sel) { setOpen((o) => !o); return; } setSel(i); setOpen(true); };

  return html`<section class="view">
    <div class="viewhead"><h1>Needs you</h1>
      <span class="sub">${items.length} open${items.length ? html` · oldest <span class="num">${oldestAge(items)}</span>` : ""}</span></div>
    <${CountStrip} brief=${brief} />
    ${items.length === 0
      ? html`<p class="stub">All clear ✓ — nothing needs you right now.</p>`
      : html`<div id="inbox">
          <div id="cards">
            ${items.map((it, i) => html`
              <div class="card-row" key=${it._kind + (it.id || i)} tabindex="0" data-idx=${i}
                   aria-current=${String(i === sel)}
                   onClick=${() => rowClick(i)} onKeyDown=${(e) => { if (e.key === "Enter") rowClick(i); }}>
                <span class="silo ${it._silo}"></span>
                <span class="t"><span class="title">${it.title || it.id}</span><span class="sub">${it._sub}</span></span>
                <span class="age num">${ageStr(it._age_s)}</span>
                <span class="disclose">▸</span>
              </div>
              ${narrow && i === sel && open
                ? html`<${Card} item=${it} station="needs_you" onAction=${(a, p) => act(it, a, p)} key=${"c" + it._kind + it.id} />`
                : null}`)}
          </div>
          ${!narrow && selItem
            ? html`<div><${Card} item=${selItem} station="needs_you" onAction=${(a, p) => act(selItem, a, p)} /></div>`
            : null}
        </div>`}
  </section>`;
}
