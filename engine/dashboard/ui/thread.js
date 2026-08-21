// Shared read-only transcript viewer: parse a Claude Code JSONL transcript into turns and
// render a live-tailing modal. Used by Overview ("running now") and Sessions (replay).
import { html, api, useState, useEffect } from "/lib.js";

// parse a Claude Code transcript (JSONL) into readable user/assistant turns
export function parseThread(lines) {
  const out = [];
  for (const ln of lines) {
    let d; try { d = JSON.parse(ln); } catch (e) { continue; }
    const m = d.message;
    if (!m || !m.role) continue;
    const content = Array.isArray(m.content) ? m.content : (m.content != null ? [{ type: "text", text: m.content }] : []);
    let text = "", tools = [];
    for (const c of content) {
      if (c.type === "text" && c.text) text += c.text;
      else if (c.type === "tool_use" && c.name) tools.push(c.name);
    }
    text = text.trim();
    if (text || tools.length) out.push({ role: m.role, text: text.slice(0, 1600), tools });
  }
  return out;
}

// read-only viewer of a running session's live transcript
export function ThreadModal({ run, onClose }) {
  const [msgs, setMsgs] = useState(null);
  useEffect(() => {
    let alive = true;
    const pull = () => api.get(`/api/activity/${encodeURIComponent(run.id)}/log?tail=500`)
      .then((d) => { if (alive) setMsgs(parseThread(d.lines || [])); }).catch(() => { if (alive) setMsgs([]); });
    pull();
    const t = setInterval(pull, 5000);   // live-tail
    return () => { alive = false; clearInterval(t); };
  }, [run.id]);
  const shown = (msgs || []).slice(-40);
  return html`<div class="th-modal" onClick=${onClose}>
    <div class="th-card" onClick=${(e) => e.stopPropagation()}>
      <div class="th-head"><span class="th-title">${run.title || run.id}</span>
        ${run.repo ? html`<span class="th-repo">${run.repo}</span>` : null}
        <button class="th-x" onClick=${onClose} aria-label="close">×</button></div>
      <div class="th-body">
        ${msgs == null ? html`<p class="stub">Loading thread…</p>`
          : shown.length ? shown.map((m, i) => html`<div class="th-msg ${m.role}" key=${i}>
              <span class="th-role">${m.role === "user" ? "Seth" : "Claude"}</span>
              ${m.text ? html`<div class="th-text">${m.text}</div>` : null}
              ${m.tools.length ? html`<div class="th-tools">${m.tools.map((t, j) => html`<span class="th-tool" key=${j}>→ ${t}</span>`)}</div>` : null}
            </div>`)
          : html`<p class="stub">${run.live
              ? "No readable messages in this transcript yet."
              : "No readable messages — this log isn't a transcript (factory drains tee stdout; the drain's session record carries the full transcript)."}</p>`}
      </div>
      <div class="th-foot"><span class="note">${run.live
        ? "Live transcript (read-only, refreshing). Sending input into a running session isn't wired up."
        : "Transcript (read-only)."}</span></div>
    </div>
  </div>`;
}
