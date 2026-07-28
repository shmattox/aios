// A109 v2a — THE one card component (spec: one anatomy, three homes).
// Used by the Inbox detail pane/accordion (Task 7) and the Board modal/accordion (Task 8).
// Presentation + local respond-box state only; the parent supplies onAction(act, params)
// and owns every API call, so the card holds zero write logic.
import { html, api, useState, useEffect, useRef, toast } from "/lib.js";

const KB_SILO = { familyoffice: "fo", personal: "per", gm: "gm", dev: "dev" };
export const siloClass = (kb) => KB_SILO[String(kb || "").toLowerCase()] || "dev";

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

export function docLinks(fields) {
  const out = [];
  if (fields.state_path) out.push({ tag: "state", label: fields.state_path, route: `#/mirror/${fields.state_path}` });
  const src = fields.papered_source || fields.source || "";
  if (/^https?:\/\//.test(src)) out.push({ tag: "drive", label: src, href: src });
  else if (src) out.push({ tag: "kb", label: src, href: `obsidian://open?path=${encodeURIComponent(src)}` });
  if (fields.draft_path) out.push({ tag: "kb", label: fields.draft_path, href: `obsidian://open?path=${encodeURIComponent(fields.draft_path)}` });
  return out;
}

function Ref({ link }) {
  const open = (e) => {
    if (link.route) { location.hash = link.route; return; }
    if (link.href) window.open(link.href, link.href.startsWith("http") ? "_blank" : "_self");
  };
  return html`<a class="ref" onClick=${open} tabindex="0">
    <span class="badge ${link.tag}">${link.tag}</span>
    <span class="path">${link.label}</span><span class="go">→</span></a>`;
}

export function Card({ item, station, onAction }) {
  station = station || item.station || "needs_you";
  const [draft, setDraft] = useState(null);
  const [respondKind, setRespondKind] = useState(null);
  const [text, setText] = useState("");
  const taRef = useRef(null);

  // gate cards carry a draft_index into brief.held — fetch the staged body to read before approving
  useEffect(() => {
    let alive = true;
    setDraft(null);
    if (item.draft_index != null) {
      api.get(`/api/draft?i=${item.draft_index}`)
        .then((d) => { if (alive) setDraft(d.markdown); })
        .catch(() => { if (alive) setDraft("(draft file missing on disk — cannot approve blind)"); });
    }
    return () => { alive = false; };
  }, [item.id, item.draft_index]);

  useEffect(() => { if (respondKind && taRef.current) taRef.current.focus(); }, [respondKind]);

  const clickVerb = (act) => {
    if (REPLY_KIND[act]) { setRespondKind(respondKind === act ? null : act); return; }
    onAction(act, {});
  };
  const submitReply = () => {
    const t = text.trim();
    if (!t) return;
    onAction(respondKind, { text: t });
    setText(""); setRespondKind(null);
  };
  const onKeyReply = (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submitReply(); }
  };

  const verbs = VERB_SETS[station] || VERB_SETS.needs_you;
  const links = docLinks(item);
  const isPG = item.paper_governs || siloClass(item.kb) === "fo"
    || /paper-governs|economic|ownership/i.test(item.rec_reason || "");

  return html`<article id="detail">
    <div class="head">
      <div class="chips">
        <span class="chip id">${item.id}</span>
        ${item.kb ? html`<span class="chip ${siloClass(item.kb) === "fo" ? "fo-c" : ""}">${item.kb}</span>` : null}
        ${item.lane ? html`<span class="chip">${item.lane} hold</span>` : null}
        ${isPG ? html`<span class="chip pg">⚖ Paper-Governs</span>` : null}
        ${item.recommended ? html`<span class="chip">rec: ${item.recommended}</span>` : null}
      </div>
      <h1>${item.title || item.id}</h1>
      ${item.rec_reason ? html`<p class="why">${item.rec_reason}</p>` : null}
    </div>

    ${item.draft_index != null ? html`
      <div class="sect">
        <div class="label">Staged draft${item.draft_path ? html` · ${item.draft_path}` : ""}</div>
        <pre class="body">${draft == null ? "loading draft…" : draft}</pre>
      </div>` : null}

    ${links.length ? html`
      <div class="sect">
        <div class="label">Drill down</div>
        <div class="refs">${links.map((l) => html`<${Ref} link=${l} key=${l.tag + l.label} />`)}</div>
      </div>` : null}

    <div class="verbs">
      ${verbs.map((v) => html`
        <button class="verb ${v.cls}" onClick=${() => clickVerb(v.act)}>
          ${v.label}${v.kbd ? html` <kbd>${v.kbd}</kbd>` : null}
        </button>`)}
      <span class="note">${NOTE[station] || NOTE.needs_you}</span>
    </div>
    <div class="respond-box ${respondKind ? "open" : ""}">
      <textarea ref=${taRef} value=${text}
        placeholder=${RESPOND_PH[respondKind] || RESPOND_PH.respond}
        onInput=${(e) => setText(e.target.value)} onKeyDown=${onKeyReply}></textarea>
      <div class="hint"><kbd>⌘/Ctrl</kbd> + <kbd>Enter</kbd> to submit · routed through the session ledger, answer rides the next gather</div>
    </div>
  </article>`;
}
