// A109 v2a — Board view: auto-discovered swimlanes from /api/board.
// Wide (>=860px): station columns x lane rows; card click opens a modal hosting the
// one Card. Narrow (<860px): vertical lane accordions with per-cell station labels,
// empty cells hidden (CSS :has); card click opens an inline accordion under the card.
import { html, api, useState, useEffect, toast } from "/lib.js";
import { Card, siloClass } from "./card.js";

const REPLY = { respond: 1, append: 1, comment: 1 };
const STATION_LABEL = { incoming: "Incoming", needs_you: "Needs you", in_motion: "In motion", review: "Review", shipped: "Shipped 24h" };
const SILO_BLURB = {
  incoming: "captured, not yet triaged — the pipeline sorts nightly",
  needs_you: "blocked on your decision — nothing moves until you act",
  in_motion: "an agent is executing this right now",
  review: "fresh-context review-gate is checking the work before ship",
  shipped: "landed — revertible via its receipt pointer",
};

function useNarrow() {
  const [n, setN] = useState(window.matchMedia("(max-width: 860px)").matches);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 860px)");
    const on = () => setN(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return n;
}

function laneClass(lane) {
  // parse the badge string "active" / "active·needs-you,stuck" — flags render warn
  const parts = String(lane.badge || "active").split("·");
  const flags = parts[1] ? parts[1].split(",") : [];
  const warn = flags.some((f) => /stuck|needs-you|paused|guard/.test(f));
  return { text: lane.badge || "active", warn };
}

export function BoardView() {
  const [board, setBoard] = useState(null);
  const [openLane, setOpenLane] = useState(null);    // exactly ONE lane open at a time
  const [modal, setModal] = useState(null);          // {item, lane} for desktop
  const [acc, setAcc] = useState(null);              // {laneKey, cardId} for narrow
  const narrow = useNarrow();

  const load = () => api.get("/api/board")
    .then((bd) => { setBoard(bd); setOpenLane((o) => o || (bd.lanes[0] && bd.lanes[0].key)); })
    .catch(() => setBoard({ stations: [], lanes: [] }));

  // repo-lane cards gain repo + backlog drill-down; silo (held) cards keep their draft/state fields
  const augment = (item, lane) => lane.kind === "repo"
    ? { ...item, repo: lane.name, backlog_path: lane.key === "env-ops" ? "BACKLOG.md" : `Projects/${lane.key}/BACKLOG.md` }
    : item;
  useEffect(() => { load(); }, []);
  useEffect(() => {
    let es, timer, last = {};
    const poll = async () => {
      try { const m = await api.get("/api/mtimes"); if (m.board !== last.board || m.brief !== last.brief || m.standup !== last.standup) { last = m; load(); } } catch (e) {}
      timer = setTimeout(poll, 5000);
    };
    try {
      es = new EventSource("/api/events");
      es.addEventListener("change", (ev) => {
        const c = JSON.parse(ev.data).changed || [];
        if (c.includes("board") || c.includes("brief") || c.includes("standup")) load();
      });
      es.onerror = () => { es.close(); poll(); };
    } catch (e) { poll(); }
    return () => { es && es.close(); clearTimeout(timer); };
  }, []);

  // Escape closes the modal; T collapses the first lane (desktop convenience)
  useEffect(() => {
    const onKey = (e) => {
      if (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT") return;
      if (e.key === "Escape") setModal(null);
      else if ((e.key === "T" || e.key === "t") && board && board.lanes[0]) {
        setOpenLane((o) => (o ? null : board.lanes[0].key));
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [board]);

  async function act(item, a, params) {
    try {
      if (a === "approve") { await api.post("gate_ship", { id: item.id }); load(); setModal(null); }
      else if (a === "reject") { const r = window.prompt("Reject reason:"); if (!r) return; await api.post("gate_reject", { id: item.id, reason: r }); load(); setModal(null); }
      else if (a === "dismiss") { const r = window.prompt("Dismiss reason:") || "below the worthiness bar"; await api.post("dismiss", { id: item.id, reason: r }); load(); setModal(null); }
      else if (a === "done") { await api.post("walk_decision", { item_id: item.item_id || item.id, station: "act", choice: "done", action: "closed from dashboard" }); load(); setModal(null); }
      else if (a === "gate_edit") { await api.post("gate_edit", { id: item.id, content: params.content }); load(); setModal(null); }
      else if (REPLY[a]) await api.post("reply", { target_id: item.item_id || item.id, reply_kind: a, text: params.text });
      else toast(`${a} — arrives in a later phase`);
    } catch (e) { /* api.post toasted */ }
  }

  const openCard = (item, lane) => {
    if (narrow) setAcc((cur) => (cur && cur.cardId === item.id ? null : { laneKey: lane.key, cardId: item.id }));
    else setModal({ item, lane });
  };

  if (board == null) return html`<section class="view"><p class="stub">Loading board…</p></section>`;

  const stations = board.stations || [];
  const silos = board.lanes.filter((l) => l.kind === "silo");
  const repos = board.lanes.filter((l) => l.kind === "repo");
  const counts = {};
  for (const s of stations) counts[s] = board.lanes.reduce((n, l) => n + ((l.cells[s] || []).length), 0);

  const BCard = ({ item, lane }) => {
    const cls = item.source === "held" || item.gate_human ? "grab" : "inert";
    const open = narrow && acc && acc.cardId === item.id;
    return html`
      <div class="bcard ${cls} ${open ? "open" : ""}" tabindex="0"
           onClick=${() => openCard(item, lane)}
           onKeyDown=${(e) => { if (e.key === "Enter") openCard(item, lane); }}>
        <span class="bid">${item.id}${item.gate_human ? html` <span class="warn">⚖</span>` : ""}${item.flags && item.flags.length ? html` <span class="warn">⚖</span>` : ""}</span>
        ${item.title}
        ${item._kind === "act" && item.urgency ? html`<span class="bsub">${item.urgency}</span>` : null}
      </div>
      ${open ? html`<${Card} item=${augment(item, lane)} station=${item.station} onAction=${(a, p) => act(item, a, p)} />` : null}`;
  };

  const Lane = ({ lane, sub }) => {
    const b = laneClass(lane);
    const isCol = openLane !== lane.key;   // exclusive: only the open lane expands
    const total = stations.reduce((n, s) => n + (lane.cells[s] || []).length, 0);
    const needs = (lane.cells.needs_you || []).length;
    return html`<div class="lane ${sub ? "sub" : ""} ${isCol ? "collapsed" : ""}" data-lane=${lane.key}>
      <button class="lane-head" aria-expanded=${String(!isCol)}
              onClick=${() => setOpenLane(openLane === lane.key ? null : lane.key)}>
        <span class="caret">▾</span><span class="silo ${sub ? "dev" : siloClass(lane.key)}"></span>
        <span class="name">${lane.name}</span>
        <span class="st-badge ${b.warn ? "warn-b" : ""}">${b.text}</span>
        <span class="summary">${total} item${total === 1 ? "" : "s"}${needs ? ` · ${needs} need you` : ""}</span>
        <kbd>T</kbd>
      </button>
      <div class="lane-grid">
        <span class="gutter"></span>
        ${stations.map((s) => html`
          <div class="cell" data-label=${STATION_LABEL[s] || s} key=${s}>
            ${(lane.cells[s] || []).map((it) => html`<${BCard} item=${it} lane=${lane} key=${it.id} />`)}
          </div>`)}
      </div>
    </div>`;
  };

  return html`<section class="view">
    <div class="viewhead"><h1>Board</h1>
      <span class="sub">columns = station · rows = lane · <kbd>T</kbd> collapses a lane · lanes auto-discovered from every BACKLOG.md</span></div>

    <div class="cols">
      <span class="colhead">Lane</span>
      ${stations.map((s) => html`<span class="colhead" key=${s}>${STATION_LABEL[s] || s} <span class="n">${counts[s]}</span></span>`)}
    </div>

    ${silos.map((l) => html`<${Lane} lane=${l} key=${l.key} />`)}

    ${repos.length ? html`
      <div class="group-head">
        <span class="gname">Dev — one lane per repo backlog</span>
        <span class="gsub">auto-discovered: every Projects/* repo with a BACKLOG.md, plus env-ops</span>
      </div>` : null}
    ${repos.map((l) => html`<${Lane} lane=${l} sub=${true} key=${l.key} />`)}

    ${!narrow && modal ? html`
      <div id="modal">
        <div class="mscrim" onClick=${() => setModal(null)}></div>
        <article id="modal-card" role="dialog" aria-modal="true">
          <div id="modal-head">
            <div id="modal-context">
              <span class="silo ${siloClass(modal.lane.key === "env-ops" || modal.lane.kind === "repo" ? "dev" : modal.lane.key)}"></span>
              <span class="sname">${modal.lane.name}</span><span>·</span>
              <span class="st">${STATION_LABEL[modal.item.station] || modal.item.station}</span>
              <span class="blurb">${SILO_BLURB[modal.item.station] || ""}</span>
            </div>
            <button id="modal-close" aria-label="Close" onClick=${() => setModal(null)}>✕</button>
          </div>
          <${Card} item=${augment(modal.item, modal.lane)} station=${modal.item.station} onAction=${(a, p) => act(modal.item, a, p)} />
        </article>
      </div>` : null}
  </section>`;
}
