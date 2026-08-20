import React from "react";
import { useActivity } from "./useActivity";

const SURFACE = { factory: "FAC", pipeline: "PIPE", session: "SESS", goal: "GOAL", workflow: "FLOW" };

// running/live -> accent pulse; shipped/ended -> ok; failed -> bad; parked/held -> warn.
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

function lastTs(r) {
  return Math.max(r.ended || 0, r.heartbeat || 0, r.started || 0);
}

export function ActivityFeed() {
  const { runs, now } = useActivity(4000);
  const sorted = [...runs].sort((a, b) => lastTs(b) - lastTs(a)).slice(0, 80);
  const liveCount = runs.filter((r) => r.live).length;

  return (
    <section className="feed">
      <header className="feed-head">
        <span className="feed-title">Activity</span>
        <span className="feed-meta">
          {liveCount ? <span className="feed-live"><span className="dot" />{liveCount} live</span> : null}
          <span className="feed-count">{runs.length} runs</span>
        </span>
      </header>
      <div className="feed-body">
        {!sorted.length ? (
          <div className="feed-empty">No activity recorded yet.</div>
        ) : (
          sorted.map((r) => (
            <div className={"frow " + statusClass(r)} key={r.id}>
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
                <span className={"fstat " + statusClass(r)}>
                  {r.live ? <span className="dot" /> : null}{r.status || "—"}
                </span>
                <span className="ftime">{ago(now - lastTs(r))}</span>
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
