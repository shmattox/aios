import React, { useState } from "react";

const SURFACE = { factory: "FAC", pipeline: "PIPE", session: "SESS", goal: "GOAL", workflow: "FLOW" };

function statusClass(r) {
  if (r.live || r.status === "running") return "run";
  if (r.status === "shipped" || r.status === "ended") return "ok";
  if (r.status === "failed") return "bad";
  if (r.status === "parked" || r.pending_approval) return "warn";
  return "idle";
}

function ago(sec) {
  if (sec == null || sec < 0) return "";
  if (sec < 60) return Math.round(sec) + "s";
  if (sec < 3600) return Math.round(sec / 60) + "m";
  if (sec < 86400) return Math.round(sec / 3600) + "h";
  return Math.round(sec / 86400) + "d";
}

const lastTs = (r) => Math.max(r.ended || 0, r.heartbeat || 0, r.started || 0);

// The cockpit's bottom pane, three modes: live activity, git worktrees, open PRs.
export function Feed({ activity, git, onRunClick, selectedRunId }) {
  const [mode, setMode] = useState("activity");
  const worktrees = git.worktrees || [];
  const extraWts = worktrees.filter((w) => !w.primary).length;
  const prs = git.prs || { items: [], loading: false };

  return (
    <section className="feed">
      <header className="feed-head">
        <div className="feed-tabs">
          <button className={"tab" + (mode === "activity" ? " on" : "")} onClick={() => setMode("activity")}>Activity</button>
          <button className={"tab" + (mode === "worktrees" ? " on" : "")} onClick={() => setMode("worktrees")}>
            Worktrees{extraWts ? <span className="tab-n">{extraWts}</span> : null}
          </button>
          <button className={"tab" + (mode === "prs" ? " on" : "")} onClick={() => setMode("prs")}>
            PRs{prs.items.length ? <span className="tab-n">{prs.items.length}</span> : null}
          </button>
        </div>
      </header>
      {mode === "activity" ? <ActivityBody activity={activity} onRunClick={onRunClick} selectedRunId={selectedRunId} />
        : mode === "worktrees" ? <WorktreeBody worktrees={worktrees} />
        : <PrBody prs={prs} />}
    </section>
  );
}

function ActivityBody({ activity, onRunClick, selectedRunId }) {
  const { runs, now } = activity;
  const [surface, setSurface] = useState("all");
  const [liveOnly, setLiveOnly] = useState(false);
  const present = [...new Set(runs.map((r) => r.surface))];
  const filtered = runs
    .filter((r) => (surface === "all" || r.surface === surface) && (!liveOnly || r.live))
    .sort((a, b) => lastTs(b) - lastTs(a)).slice(0, 80);
  const liveCount = runs.filter((r) => r.live).length;

  return (
    <>
      <div className="feed-sub">
        <div className="feed-filters">
          <button className={"chip" + (surface === "all" ? " on" : "")} onClick={() => setSurface("all")}>all</button>
          {present.map((s) => (
            <button className={"chip" + (surface === s ? " on" : "")} key={s} onClick={() => setSurface(s)}>
              {(SURFACE[s] || s).toLowerCase()}
            </button>
          ))}
          <button className={"chip" + (liveOnly ? " on" : "")} onClick={() => setLiveOnly((v) => !v)}>live</button>
        </div>
        <span className="feed-meta">
          {liveCount ? <span className="feed-live"><span className="dot" />{liveCount} live</span> : null}
          <span className="feed-count">{filtered.length}/{runs.length}</span>
        </span>
      </div>
      <div className="feed-body">
        {!filtered.length ? <div className="feed-empty">No matching activity.</div>
          : filtered.map((r) => (
            <div className={"frow " + statusClass(r) + (r.id === selectedRunId ? " sel" : "")} key={r.id}
                 onClick={() => onRunClick(r)} title="Open run log">
              <span className="fsurface">{SURFACE[r.surface] || r.surface}</span>
              <div className="fmain">
                <div className="ftitle">{r.title || r.id}</div>
                <div className="fmeta">
                  {r.repo ? <span className="frepo">{r.repo}</span> : null}
                  {r.item_ids && r.item_ids.length ? <span className="fids">{r.item_ids.join(" ")}</span> : null}
                  {r.cost_usd ? <span className="fcost">${r.cost_usd.toFixed(2)}</span> : null}
                </div>
              </div>
              <div className="fright">
                <span className={"fstat " + statusClass(r)}>{r.live ? <span className="dot" /> : null}{r.status || "—"}</span>
                <span className="ftime">{ago(now - lastTs(r))}</span>
              </div>
            </div>
          ))}
      </div>
    </>
  );
}

function WorktreeBody({ worktrees }) {
  return (
    <div className="feed-body">
      {!worktrees.length ? <div className="feed-empty">No worktrees found.</div>
        : worktrees.map((w, i) => (
          <div className={"frow " + (w.primary ? "idle" : "run")} key={w.repo + w.path + i}>
            <span className="fsurface">{w.repo}</span>
            <div className="fmain">
              <div className="ftitle">{w.branch || "(no branch)"}{w.locked ? " · locked" : ""}</div>
              <div className="fmeta"><span className="fids">{w.path}</span></div>
            </div>
            <div className="fright">
              <span className={"fstat " + (w.primary ? "" : "run")}>{w.primary ? "main" : "drain"}</span>
              <span className="ftime">{w.head}</span>
            </div>
          </div>
        ))}
    </div>
  );
}

function PrBody({ prs }) {
  return (
    <div className="feed-body">
      {prs.loading && !prs.items.length ? <div className="feed-empty">Loading PRs…</div>
        : !prs.items.length ? <div className="feed-empty">No open PRs.</div>
        : prs.items.map((p) => (
          <a className={"frow " + (p.draft ? "warn" : "ok")} key={p.repo + "#" + p.number}
             href={p.url} target="_blank" rel="noopener noreferrer" title="Open PR on GitHub">
            <span className="fsurface">{p.repo}</span>
            <div className="fmain">
              <div className="ftitle">#{p.number} {p.title}</div>
              <div className="fmeta"><span className="fids">{p.branch}</span></div>
            </div>
            <div className="fright">
              <span className={"fstat " + (p.draft ? "warn" : "ok")}>{p.draft ? "draft" : (p.state || "open").toLowerCase()}</span>
              <span className="ftime">↗</span>
            </div>
          </a>
        ))}
    </div>
  );
}
