// A109 v2a — THE one card component (spec: one anatomy, three homes).
// Used by the Inbox detail pane/accordion (Task 7) and the Board modal/accordion (Task 8).
// Presentation + local respond-box state only; the parent supplies onAction(act, params)
// and owns every API call, so the card holds zero write logic.
import { html, api, useState, useEffect, useRef, toast } from "/lib.js";
import { openInFiles } from "./filenav.js";

const KB_SILO = { familyoffice: "fo", personal: "per", gm: "gm", dev: "dev" };
export const siloClass = (kb) => KB_SILO[String(kb || "").toLowerCase()] || "dev";

// map a stored path to an env-relative Files path: vault-relative KB paths (02_FamilyOffice/…)
// get the vault prefix; state/ and repo paths are already env-relative.
function toEnvFile(vaultRel, p) {
  if (!p) return null;
  p = String(p).replace(/\\/g, "/");
  if (vaultRel && !p.startsWith(vaultRel + "/") && /^\d\d_/.test(p)) return vaultRel + "/" + p;
  return p;
}

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
export function docLinks(fields, vaultRel) {
  const out = [];
  if (fields.state_path)
    out.push({ tag: "state", label: shortPath(fields.state_path), file: toEnvFile(vaultRel, fields.state_path) });
  const paper = fields.papered_source || "";
  if (/^https?:\/\//.test(paper)) out.push({ tag: "drive", label: paper, open: paper });
  else if (paper) out.push({ tag: "drive", label: shortPath(paper), mock: `opens “${paper}” in Google Drive (dataroom)` });
  if (fields.draft_path)
    out.push({ tag: "kb", label: shortPath(fields.draft_path), file: toEnvFile(vaultRel, fields.draft_path) });
  if (fields.backlog_path)
    out.push({ tag: "kb", label: shortPath(fields.backlog_path), file: toEnvFile(vaultRel, fields.backlog_path) });
  return out;
}

// Deep-linking to Obsidian/Drive needs per-vault / per-file config not present in
// v2a, so kb/drive links surface an informative toast (as the approved mockup did);
// the in-app state link routes to the Mirror browser, which is real.
function refOpen(link) {
  if (link.file) { openInFiles(link.file); return; }   // source file → open in the Files editor
  if (link.route) { location.hash = link.route; return; }
  if (link.open) {
    if (/^https?:\/\//i.test(link.open)) {
      window.open(link.open, "_blank", "noopener");
    } else {                                   // custom scheme (obsidian://) — anchor-launch the
      const a = document.createElement("a");   // OS handler without navigating the dashboard away
      a.href = link.open; a.rel = "noopener"; a.click();
    }
    return;
  }
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

// THE one prose renderer. The model that authors the voice prose (held `why` + act
// system/claude/urgency/in-motion) emits inline links in the form  [phrase](tag:target)  —
// tag ∈ state|drive|kb|notion, the canonical-roles homes. The PHRASE is the visible link text
// (source-tagged sup); the target/id lives in the link and is NEVER shown — exactly the mockup's
// `<a class="doclink" data-path=…>current entity page<sup class="srctag kb">kb</sup></a>`.
// Non-link text passes through untouched. No keyword-guessing, no token-matching: the author marks
// the links because only it knows the referents (env principle — model populates, engine renders).
// target allows one level of balanced parens so real filenames survive — e.g.
// drive:Family Office/raw/Bayview Loan Modification (executed).pdf — the classic
// markdown-link-with-parens case. (Mutually-exclusive branches → no ReDoS.)
const PROSE_LINK = /\[([^\]]+)\]\((state|drive|kb|notion):((?:[^()]|\([^()]*\))+)\)/g;
function linkForTarget(tag, target) {
  target = String(target).trim();
  if (tag === "state") return { tag, file: target, mock: `opens ${target}` };
  if (tag === "kb") return { tag, mock: `opens ${target} in Obsidian` };
  if (tag === "notion") return { tag, mock: `opens ${target} in Notion` };
  return /^https?:\/\//i.test(target) ? { tag, open: target } : { tag, mock: `opens “${target}” in Google Drive` };
}
function weaveLinks(text) {
  if (text == null || text === "") return null;
  text = String(text);
  const out = []; let last = 0, m;
  PROSE_LINK.lastIndex = 0;
  while ((m = PROSE_LINK.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(html`<${InlineLink} link=${{ ...linkForTarget(m[2], m[3]), label: m[1] }} key=${m.index} />`);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out.length === 1 && typeof out[0] === "string" ? out[0] : out;
}

// act (needs-you task) verbs — distinct from the gate verbs; wired to walk_decision / reply
const ACT_VERBS = [
  { act: "done", label: "Mark done", cls: "approve" },
  { act: "respond", label: "Respond", cls: "respond" },
];
const voiceText = (v) => (v == null ? "" : (typeof v === "string" ? v : (v.text || "")));
const voiceCite = (v) => (v && typeof v === "object" ? (v.cite || "") : "");

// The mockup's "Drill down" box — the same boxed STATE/DRIVE/KB refs the gate card gets from
// docLinks, built here from an act item's real citations (thread, state/ paths, Notion, urls).
function citeRefs(item) {
  const out = [], seen = new Set();
  const add = (l) => { const k = l.tag + l.label; if (l.label && !seen.has(k)) { seen.add(k); out.push(l); } };
  if (item.thread_id) add({ tag: "state", label: `thread · ${item.thread_id}`, route: "#/mirror", mock: `opens the ${item.thread_id} action thread` });
  const cite = voiceCite(item.system_voice) || "";
  for (const m of cite.matchAll(/state\/[^\s;,)]+/g)) add({ tag: "state", label: m[0], file: m[0], mock: `opens ${m[0]}` });
  for (const m of cite.matchAll(/collection:\/\/[A-Za-z0-9-]{8,}/g)) add({ tag: "notion", label: m[0], mock: `opens ${m[0]} in Notion` });
  for (const m of cite.matchAll(/https?:\/\/[^\s;,)]+/g)) add({ tag: "drive", label: m[0], open: m[0] });
  return out;
}

export function Card({ item, station, onAction, vaultRel }) {
  station = station || item.station || "needs_you";
  const isAct = item._kind === "act";
  const isDev = item._kind === "dev";  // a backlog pointer — read-only, no write verbs
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
        .then((d) => { if (alive) setDraft(d); })
        .catch(() => { if (alive) setDraft({ markdown: "(draft file missing on disk — cannot approve blind)" }); });
    }
    return () => { alive = false; };
  }, [item.id, item.draft_index]);

  useEffect(() => { if (respondKind && taRef.current) taRef.current.focus(); }, [respondKind]);

  const clickVerb = (act) => {
    if (act === "edit") { setEditText(draft && draft.markdown != null ? draft.markdown : ""); setEditing(true); return; }
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

  const verbs = isDev ? [] : isAct ? ACT_VERBS : (VERB_SETS[station] || VERB_SETS.needs_you);
  const links = docLinks(item, vaultRel);
  const sysText = voiceText(item.system_voice);
  const claudeText = voiceText(item.claude_voice);
  const flags = Array.isArray(item.flags) ? item.flags : (item.flags ? [item.flags] : []);
  const grade = item.system_voice && typeof item.system_voice === "object" ? item.system_voice.grade : "";
  const nextAction = item.in_motion && item.in_motion.next_action;
  const court = item.in_motion && item.in_motion.court;
  // drill-down: prefer the engine-emitted structured refs (brief_refs, one uniform source);
  // fall back to client parsing only for a cache written before the annotator ran.
  const drillRefs = (Array.isArray(item.refs) && item.refs.length)
    ? item.refs : (isAct ? citeRefs(item) : links);
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
        ${item.state_badge ? html`<span class="chip badge-${item.state_badge}">${item.state_badge}</span>` : null}
        ${item.lane ? html`<span class="chip">${item.lane} hold</span>` : null}
        ${isPG ? html`<span class="chip pg">⚖ Paper-Governs</span>` : null}
        ${item.gate_human ? html`<span class="chip pg">⛔ [GATE: human]</span>` : null}
        ${item.recommended ? html`<span class="chip">rec: ${item.recommended}</span>` : null}
      </div>
      <h1>${item.title || item.id}</h1>
      ${!isAct && why ? html`<p class="why">${weaveLinks(why)}</p>` : null}
    </div>

    ${isAct ? html`
      ${item.urgency ? html`<div class="sect"><div class="label">Why now</div><p class="why">${weaveLinks(item.urgency)}</p></div>` : null}
      <div class="sect">
        <div class="label">Recommendation</div>
        ${sysText
          ? html`<div class="voice sys"><span class="vlabel">🔵 Your system says${grade ? ` · ${grade}` : ""}</span> ${weaveLinks(sysText)}</div>`
          : html`<div class="voice silent">— your system is silent —</div>`}
        ${claudeText ? html`<div class="voice claude"><span class="vlabel">🟠 Claude adds</span> ${weaveLinks(claudeText)}</div>` : null}
      </div>
      ${flags.length ? html`
        <div class="sect">
          <div class="label">⚖ Paper-Governs</div>
          ${flags.map((f, i) => html`<blockquote class="flag" key=${i}>${f}</blockquote>`)}
        </div>` : null}
      ${nextAction ? html`
        <div class="sect">
          <div class="label">In motion${court ? ` · ${court === "you" ? "your court" : "others’ court"}` : ""}</div>
          <p class="why">${weaveLinks(nextAction)}</p>
        </div>` : null}
    ` : null}

    ${item.draft_index != null ? html`
      <div class="sect">
        ${(draft && draft.diff && draft.diff.length && !editing) ? html`
          <div class="label">Proposed change${draft.target ? html` · ${shortPath(draft.target)}` : ""}</div>
          <div class="diff">${draft.diff.map((r, i) => html`<div class="${r.op}" key=${i}>${r.text}</div>`)}</div>
        ` : html`
          <div class="label">${editing ? "Editing draft" : "Staged draft"}${item.draft_path ? html` · ${shortPath(item.draft_path)}` : ""}</div>
          ${editing
            ? html`<textarea class="editarea" value=${editText} onInput=${(e) => setEditText(e.target.value)}></textarea>`
            : html`<pre class="body">${draft == null ? "loading draft…" : (draft.markdown != null ? draft.markdown : "")}</pre>`}
        `}
      </div>` : null}

    ${(draft && draft.paper_evidence) ? html`
      <div class="sect">
        <div class="label">Paper evidence · advisory</div>
        <div class="paper ${draft.paper_evidence.verdict}">
          <span class="verdict ${draft.paper_evidence.verdict}">${draft.paper_evidence.verdict === "matches" ? "✓ MATCHES" : draft.paper_evidence.verdict === "conflicts" ? "✗ CONFLICTS" : "— no paper found —"}</span>
          ${draft.paper_evidence.quote ? html`<blockquote>“${draft.paper_evidence.quote}”</blockquote>` : null}
          ${draft.paper_evidence.doc ? html`<span class="src">${draft.paper_evidence.doc}${draft.paper_evidence.section ? ` · §${draft.paper_evidence.section}` : ""}${draft.paper_evidence.checked_utc ? ` · checked ${String(draft.paper_evidence.checked_utc).slice(0, 10)}` : ""}</span>` : null}
        </div>
      </div>` : null}

    ${drillRefs.length ? html`
      <div class="sect">
        <div class="label">Drill down</div>
        <div class="refs">${drillRefs.map((l) => html`<${Ref} link=${l} key=${l.tag + l.label} />`)}</div>
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
          <span class="note">${isDev
            ? "Backlog pointer — the dashboard doesn't act on backlog items. Open the backlog above to see the full item, then work it in a native session (or let the factory drain it)."
            : (NOTE[station] || NOTE.needs_you)}</span>`}
    </div>
    <div class="respond-box ${respondKind ? "open" : ""}">
      <textarea ref=${taRef} value=${text}
        placeholder=${RESPOND_PH[respondKind] || RESPOND_PH.respond}
        onInput=${(e) => setText(e.target.value)} onKeyDown=${onKeyReply}></textarea>
      <div class="hint"><kbd>⌘/Ctrl</kbd> + <kbd>Enter</kbd> to submit · routed through the session ledger, answer rides the next gather</div>
    </div>
  </article>`;
}
