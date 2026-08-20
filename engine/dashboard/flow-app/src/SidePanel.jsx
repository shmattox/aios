import React, { useEffect, useState } from "react";

// One right-side panel shared by the graph (stage drill-in) and the feed (run log). `panel` is
// either {kind:"stage", id} or {kind:"run", run}. Stage items whose id is currently in a factory
// run cross-link to that run's log via onOpenRun.
export function SidePanel({ panel, run, runByItem, onOpenRun, onClose }) {
  return (
    <aside className="detail">
      {panel.kind === "stage"
        ? <StageBody id={panel.id} runByItem={runByItem} onOpenRun={onOpenRun} onClose={onClose} />
        : <RunBody run={run} onClose={onClose} />}
    </aside>
  );
}

function PanelHead({ label, sub, onClose }) {
  return (
    <header className="detail-head">
      <div>
        <div className="detail-lbl">{label}</div>
        <div className="detail-sub">{sub}</div>
      </div>
      <button className="detail-x" title="Close" onClick={onClose}>✕</button>
    </header>
  );
}

function StageBody({ id, runByItem, onOpenRun, onClose }) {
  const [detail, setDetail] = useState(null);
  useEffect(() => {
    let alive = true;
    setDetail(null);
    const load = () =>
      fetch("/api/pipeline/stage/" + encodeURIComponent(id))
        .then((r) => r.json()).then((d) => { if (alive) setDetail(d); }).catch(() => {});
    load();
    const t = setInterval(load, 4000);
    return () => { alive = false; clearInterval(t); };
  }, [id]);

  const items = detail && detail.items;
  return (
    <>
      <PanelHead label={detail ? detail.label : id}
                 sub={detail ? `${detail.count} item${detail.count === 1 ? "" : "s"}` : "…"} onClose={onClose} />
      <div className="detail-body">
        {!detail ? <div className="detail-empty">Loading…</div>
          : !items.length ? <div className="detail-empty">No items in this stage.</div>
          : items.map((it) => {
              const run = runByItem.get(it.id);
              return (
                <div className={"ditem" + (run ? " link" : "")} key={it.id}
                     onClick={run ? () => onOpenRun(run) : undefined}
                     title={run ? "Open the factory run for this item" : undefined}>
                  <div className="ditem-top">
                    <span className="ditem-id">{it.id}</span>
                    {it.repo ? <span className="ditem-repo">{it.repo}</span> : null}
                    {run ? <span className="ditem-run">▶ run</span> : null}
                  </div>
                  <div className="ditem-title">{it.title}</div>
                </div>
              );
            })}
      </div>
    </>
  );
}

function fmtDur(r) {
  const a = r.started || 0, b = r.ended || r.heartbeat || 0;
  if (!a || !b || b < a) return null;
  const s = b - a;
  if (s < 60) return Math.round(s) + "s";
  if (s < 3600) return Math.round(s / 60) + "m";
  return (s / 3600).toFixed(1) + "h";
}

function RunBody({ run, onClose }) {
  const [log, setLog] = useState(null);   // {lines, available} | {error}
  // Poll the log so an OPEN live run's tail keeps growing; keep the last good log on a transient
  // poll failure (only surface an error if we never got one). Run meta (live/cost/duration) stays
  // fresh via the `run` prop, which App re-derives from the latest poll each render.
  useEffect(() => {
    let alive = true;
    setLog(null);
    const load = () =>
      fetch(`/api/activity/${encodeURIComponent(run.id)}/log?tail=200`)
        .then((r) => r.json()).then((d) => { if (alive) setLog(d); })
        .catch(() => { if (alive) setLog((l) => l || { error: true }); });
    load();
    const t = setInterval(load, 4000);
    return () => { alive = false; clearInterval(t); };
  }, [run.id]);

  const meta = [
    ["repo", run.repo],
    ["items", (run.item_ids || []).join(" ")],
    ["cost", run.cost_usd ? "$" + run.cost_usd.toFixed(2) : null],
    ["tokens", run.tokens ? run.tokens.toLocaleString() : null],
    ["duration", fmtDur(run)],
  ].filter(([, v]) => v);

  return (
    <>
      <PanelHead label={run.title || run.id}
                 sub={[run.surface, run.live ? "live" : run.status].filter(Boolean).join(" · ") || "run"}
                 onClose={onClose} />
      <div className="detail-body">
        <div className="rmeta">
          {meta.map(([k, v]) => (
            <div className="rmeta-row" key={k}><span className="rmeta-k">{k}</span><span className="rmeta-v">{v}</span></div>
          ))}
        </div>
        {run.detail ? <div className="rdetail">{run.detail}</div> : null}
        <div className="rlog-h">log</div>
        <pre className="rlog">
          {!log ? "Loading…"
            : log.error ? "Log unavailable."
            : !log.available || !(log.lines || []).length ? "No log output."
            : log.lines.join("\n")}
        </pre>
      </div>
    </>
  );
}
