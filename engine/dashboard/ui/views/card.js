// A109 v2a — THE one card component (spec: one anatomy, three homes).
// Used by the Inbox detail pane/accordion (Task 7) and the Board modal/accordion (Task 8).
// Presentation + local respond-box state only; the parent supplies onAction(act, params)
// and owns every API call, so the card holds zero write logic.
import { html, api, useState, useEffect, useRef, toast } from "/lib.js";

const KB_SILO = { familyoffice: "fo", personal: "per", gm: "gm", dev: "dev" };
export const siloClass = (kb) => KB_SILO[String(kb || "").toLowerCase()] || "dev";

const STATION_BLURB = {
  incoming: "Captured, not yet triaged — the pipeline sorts nightly.",
  needs_you: "Blocked on your decision — nothing moves until you act.",
  in_motion: "An agent is executing this right now.",
  review: "A fresh-context review-gate is checking the work before ship.",
  shipped: "Landed — revertible via its receipt pointer.",
};

// Station -> verb strip. Only these acts POST: approve, reject, dismiss, respond,
// append, comment. edit toasts until Task 9; receipt/draft/revert are link/reuse.
const VERB_SETS = {
  incoming: [{ act: "dismiss", label: "Dismiss", cls: "reject" }],
  needs_you: [
    { act: "approve", label: "Approve", kbd: "e", cls: "approve" },
    { act: "reject", label: "Reject", kbd: "x", cls: "reject" },
    { act: "edit", label: "Edit", kbd: "i", cls: "edit" },
    { act: "respond", label: "Respond", kbd: "r", cls: "respond" },
  ],
  in_motion: [{ act: "append", label: "Append instruction", cls: "respond" }],
  review: [
    { act: "draft", label: "Open draft", cls: "edit" },
    { act: "comment", label: "Comment to reviewer", cls: "respond" },
  ],
  shipped: [
    { act: "receipt", label: "View receipt", cls: "edit" },
    { act: "revert", label: "Revert", cls: "reject" },
  ],
};
const NOTE = {
  incoming: "Not yet triaged — the pipeline sorts nightly. Dismiss routes it to the searchable reference lane, never drafted. (Triage-now arrives in v2b.)",
  needs_you: "Every ship is revertible. Approve runs the same gated CLI the chat gate uses — the dashboard holds zero write logic.",
  in_motion: "An agent is executing this now — no decision needed. Anything you append is consumed at its next checkpoint.",
  review: "Fresh-context review-gate is checking the work — CRITICAL findings loop back into a fix pass automatically.",
  shipped: "Landed. The one power that matters post-ship is the undo: revert runs rewind's git-revert, itself revertible.",
};
const REPLY_KIND = { respond: "respond", append: "append", comment: "comment" };
const RESPOND_PH = {
  respond: "Ask a question or leave instructions — lands as a durable decision op; the next run consumes it.",
  append: "Append an instruction — the running agent consumes it at its next checkpoint.",
  comment: "Comment to the reviewer — attached to the review run, shown in the gate report.",
};

const shortPath = (p) => {
  p = String(p || "").replace(/\\/g, "/");
  const parts = p.split("/").filter(Boolean);
  return parts.length > 4 ? "…/" + parts.slice(-3).join("/") : p;
};

// Build the drill-down links from a record's real fields. Never treat the board
// card's `source` (a TYPE: "backlog"/"held") as a document path — that was the
// "KB backlog" bug. Only explicit doc fields become links.
export function docLinks(fields) {
  const out = [];
  if (fields.state_path)
    out.push({ tag: "state", label: shortPath(fields.state_path), route: "#/mirror" });
  const paper = fields.papered_source || "";
  if (/^https?:\/\//.test(paper)) out.push({ tag: "drive", label: paper, open: paper });
  else if (paper) out.push({ tag: "drive", label: shortPath(paper), mock: `opens “${paper}” in Google Drive (dataroom)` });
  if (fields.draft_path)
    out.push({ tag: "kb", label: shortPath(fields.draft_path), mock: `opens ${shortPath(fields.draft_path)} in Obsidian` });
  if (fields.backlog_path)
    out.push({ tag: "kb", label: shortPath(fields.backlog_path), mock: `opens ${fields.backlog_path} — the repo backlog` });
  return out;
}

// Deep-linking to Obsidian/Drive needs per-vault / per-file config not present in
// v2a, so kb/drive links surface an informative toast (as the approved mockup did);
// the in-app state link routes to the Mirror browser, which is real.
function refOpen(link) {
  if (link.route) { location.hash = link.route; return; }
  if (link.open) { window.open(link.open, "_blank", "noopener"); return; }
  if (link.mock) toast(`→ ${link.mock}`);
}

function Ref({ link }) {
  return html`<a class="ref" tabindex="0" onClick=${() => refOpen(link)}
      onKeyDown=${(e) => { if (e.key === "Enter") refOpen(link); }}>
    <span class="badge ${link.tag}">${link.tag}</span>
    <span class="path">${link.label}</span><span class="go">→</span></a>`;
}

// inline, source-tagged doclink woven into the prose (mockup's "…signed.pdf ᴰᴿᴵⱽᴱ" style)
function InlineLink({ link }) {
  return html`<a class="doclink" tabindex="0" onClick=${() => refOpen(link)}
      onKeyDown=${(e) => { if (e.key === "Enter") refOpen(link); }}>${link.label}<sup class="srctag ${link.tag}">${link.tag}</sup></a>`;
}

// Weave the doc links INTO the why prose (the mockup's approach) — wrap the first phrase
// that references each source, so the subtext itself is hyperlinked, not a separate list.
const WHY_KEYWORDS = {
  drive: /\b(?:the )?(loan modification|signed (?:loan )?\w+|executed \w+|the document|the pdf|the agreement|the statement|papered source|source document)\b/i,
  kb: /\b(current entity page|entity page|wiki page|knowledge base|the wiki|the page)\b/i,
  state: /\b(state record|the mirror|the record|state mirror)\b/i,
};

function weaveWhy(text, links) {
  let segs = [text];
  for (const link of links) {
    const rx = WHY_KEYWORDS[link.tag];
    if (!rx) continue;
    for (let i = 0; i < segs.length; i++) {
      if (typeof segs[i] !== "string") continue;
      const m = segs[i].match(rx);
      if (!m) continue;
      const before = segs[i].slice(0, m.index);
      const after = segs[i].slice(m.index + m[0].length);
      segs.splice(i, 1, before, html`<${InlineLink} link=${{ ...link, label: m[0] }} key=${link.tag} />`, after);
      break;
    }
  }
  return segs.filter((s) => s !== "");
}

// act (needs-you task) verbs — distinct from the gate verbs; wired to walk_decision / reply
const ACT_VERBS = [
  { act: "done", label: "Mark done", cls: "approve" },
  { act: "respond", label: "Respond", cls: "respond" },
];
const voiceText = (v) => (v == null ? "" : (typeof v === "string" ? v : (v.text || "")));
const voiceCite = (v) => (v && typeof v === "object" ? (v.cite || "") : "");

// Linkify a citation string IN PLACE — the state/ paths, Notion collection ids, and urls become
// clickable source-tagged links, keeping the readable "task…/decision…" provenance text around
// them (the "content -> its own context" hyperlinking, without a redundant separate box). State
// routes to the in-app Mirror; Notion/drive open informatively (real deep-link needs config).
function linkifyCite(cite) {
  if (!cite) return null;
  const rx = /(state\/[^\s;,)]+|collection:\/\/[A-Za-z0-9-]{8,}|https?:\/\/[^\s;,)]+)/g;
  const out = []; let last = 0, m;
  while ((m = rx.exec(cite)) !== null) {
    if (m.index > last) out.push(cite.slice(last, m.index));
    const tok = m[0];
    const link = tok.startsWith("state/") ? { tag: "state", route: "#/mirror", mock: `opens ${tok} in the Mirror` }
      : tok.startsWith("collection://") ? { tag: "notion", mock: `opens ${tok} in Notion` }
        : { tag: "drive", open: tok };
    out.push(html`<${InlineLink} link=${{ ...link, label: tok }} key=${m.index} />`);
    last = m.index + tok.length;
  }
  if (last < cite.length) out.push(cite.slice(last));
  return out;
}

export function Card({ item, station, onAction }) {
  station = station || item.station || "needs_you";
  const isAct = item._kind === "act";
  const [draft, setDraft] = useState(null);
  const [respondKind, setRespondKind] = useState(null);
  const [text, setText] = useState("");
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  const taRef = useRef(null);

  useEffect(() => {
    let alive = true;
    setDraft(null);
    setEditing(false);   // never carry edit mode across a card switch
    if (item.draft_index != null) {
      api.get(`/api/draft?i=${item.draft_index}`)
        .then((d) => { if (alive) setDraft(d.markdown); })
        .catch(() => { if (alive) setDraft("(draft file missing on disk — cannot approve blind)"); });
    }
    return () => { alive = false; };
  }, [item.id, item.draft_index]);

  useEffect(() => { if (respondKind && taRef.current) taRef.current.focus(); }, [respondKind]);

  const clickVerb = (act) => {
    if (act === "edit") { setEditText(draft != null ? draft : ""); setEditing(true); return; }
    if (REPLY_KIND[act]) { setRespondKind(respondKind === act ? null : act); return; }
    onAction(act, {});
  };
  const saveAndShip = () => { onAction("gate_edit", { content: editText }); setEditing(false); };
  const submitReply = () => {
    const t = text.trim();
    if (!t) return;
    onAction(respondKind, { text: t });
    setText(""); setRespondKind(null);
  };
  const onKeyReply = (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submitReply(); }
  };

  const verbs = isAct ? ACT_VERBS : (VERB_SETS[station] || VERB_SETS.needs_you);
  const links = docLinks(item);
  const sysText = voiceText(item.system_voice), sysCite = voiceCite(item.system_voice);
  const claudeText = voiceText(item.claude_voice);
  const flags = Array.isArray(item.flags) ? item.flags : (item.flags ? [item.flags] : []);
  const grade = item.system_voice && typeof item.system_voice === "object" ? item.system_voice.grade : "";
  const nextAction = item.in_motion && item.in_motion.next_action;
  const court = item.in_motion && item.in_motion.court;
  const isPG = item.paper_governs || item.gate_human || siloClass(item.kb) === "fo"
    || flags.length || /paper-governs|economic|ownership/i.test(item.rec_reason || "");
  // backlog cards have no rec_reason — fall back to the station's meaning so the popup is never empty
  const why = item.rec_reason
    || (item.repo ? `In the ${item.repo} backlog. ${STATION_BLURB[station] || ""}` : STATION_BLURB[station] || "");

  return html`<article id="detail">
    <div class="head">
      <div class="chips">
        <span class="chip id">${item.id}</span>
        ${item.kb ? html`<span class="chip ${siloClass(item.kb) === "fo" ? "fo-c" : ""}">${item.kb}</span>` : null}
        ${item.domain && !item.kb ? html`<span class="chip ${siloClass(item.domain) === "fo" ? "fo-c" : ""}">${item.domain}</span>` : null}
        ${item.thread_id ? html`<span class="chip">↻ ${item.thread_id}</span>` : null}
        ${item.repo ? html`<span class="chip">${item.repo}</span>` : null}
        ${item.lane ? html`<span class="chip">${item.lane} hold</span>` : null}
        ${isPG ? html`<span class="chip pg">⚖ Paper-Governs</span>` : null}
        ${item.gate_human ? html`<span class="chip pg">⛔ [GATE: human]</span>` : null}
        ${item.recommended ? html`<span class="chip">rec: ${item.recommended}</span>` : null}
      </div>
      <h1>${item.title || item.id}</h1>
      ${!isAct && why ? html`<p class="why">${weaveWhy(why, links)}</p>` : null}
    </div>

    ${isAct ? html`
      ${item.urgency ? html`<div class="sect"><div class="label">Why now</div><p class="why">${item.urgency}</p></div>` : null}
      <div class="sect">
        <div class="label">Recommendation</div>
        ${sysText
          ? html`<div class="voice sys"><span class="vlabel">🔵 Your system says${grade ? ` · ${grade}` : ""}</span> ${sysText}${sysCite ? html`<div class="vcite">${linkifyCite(sysCite)}</div>` : null}</div>`
          : html`<div class="voice silent">— your system is silent —</div>`}
        ${claudeText ? html`<div class="voice claude"><span class="vlabel">🟠 Claude adds</span> ${claudeText}</div>` : null}
      </div>
      ${flags.length ? html`
        <div class="sect">
          <div class="label">⚖ Paper-Governs</div>
          ${flags.map((f, i) => html`<blockquote class="flag" key=${i}>${f}</blockquote>`)}
        </div>` : null}
      ${nextAction ? html`
        <div class="sect">
          <div class="label">In motion${court ? ` · ${court === "you" ? "your court" : "others’ court"}` : ""}</div>
          <p class="why">${nextAction}</p>
        </div>` : null}
    ` : null}

    ${item.draft_index != null ? html`
      <div class="sect">
        <div class="label">${editing ? "Editing draft" : "Staged draft"}${item.draft_path ? html` · ${shortPath(item.draft_path)}` : ""}</div>
        ${editing
          ? html`<textarea class="editarea" value=${editText} onInput=${(e) => setEditText(e.target.value)}></textarea>`
          : html`<pre class="body">${draft == null ? "loading draft…" : draft}</pre>`}
      </div>` : null}

    ${links.length ? html`
      <div class="sect">
        <div class="label">Drill down</div>
        <div class="refs">${links.map((l) => html`<${Ref} link=${l} key=${l.tag + l.label} />`)}</div>
      </div>` : null}

    <div class="verbs">
      ${editing
        ? html`
          <button class="verb approve" onClick=${saveAndShip}>Save & ship</button>
          <button class="verb" onClick=${() => setEditing(false)}>Cancel</button>
          <span class="note">Save & ship writes your edited draft, then runs the SAME gated ship path (content-refusal + revert pointer). A Paper-Governs edit still holds unless the body passes the check.</span>`
        : html`
          ${verbs.map((v) => html`
            <button class="verb ${v.cls}" onClick=${() => clickVerb(v.act)}>
              ${v.label}${v.kbd ? html` <kbd>${v.kbd}</kbd>` : null}
            </button>`)}
          <span class="note">${NOTE[station] || NOTE.needs_you}</span>`}
    </div>
    <div class="respond-box ${respondKind ? "open" : ""}">
      <textarea ref=${taRef} value=${text}
        placeholder=${RESPOND_PH[respondKind] || RESPOND_PH.respond}
        onInput=${(e) => setText(e.target.value)} onKeyDown=${onKeyReply}></textarea>
      <div class="hint"><kbd>⌘/Ctrl</kbd> + <kbd>Enter</kbd> to submit · routed through the session ledger, answer rides the next gather</div>
    </div>
  </article>`;
}
