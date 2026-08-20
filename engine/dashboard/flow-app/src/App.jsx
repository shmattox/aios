import React, { useState, useMemo } from "react";
import PipelineGraph from "./PipelineGraph";
import { Feed } from "./Feed";
import { SidePanel } from "./SidePanel";
import { useActivity } from "./useActivity";
import { useGit } from "./useGit";
import "./style.css";

// Cockpit shell: owns the ONE right-side panel (a stage's items OR a run's log) shared by the
// graph and the feed, plus the activity poll (so a stage item can cross-link to its factory run).
export default function App() {
  const activity = useActivity(4000);
  const git = useGit(10000);
  const [panel, setPanel] = useState(null);   // null | {kind:"stage", id} | {kind:"run", runId}

  // item_id -> its most-recent run (ascending sort => last write wins), for stage-item cross-links.
  const runByItem = useMemo(() => {
    const m = new Map();
    for (const r of [...activity.runs].sort((a, b) => (a.started || 0) - (b.started || 0)))
      for (const iid of r.item_ids || []) m.set(iid, r);
    return m;
  }, [activity.runs]);

  const openStage = (id) =>
    setPanel((p) => (id == null ? null : (p && p.kind === "stage" && p.id === id ? null : { kind: "stage", id })));
  const openRun = (run) =>
    setPanel((p) => (p && p.kind === "run" && p.runId === run.id ? null : { kind: "run", runId: run.id }));

  // Re-derive the open run from the freshest poll each render so its live badge / cost / duration
  // update while the panel is open (the panel holds only an id, never a frozen snapshot).
  const openRunObj = panel?.kind === "run"
    ? (activity.runs.find((r) => r.id === panel.runId) || { id: panel.runId, gone: true })
    : null;

  return (
    <div className="cockpit">
      <div className="graph-wrap">
        <PipelineGraph selectedStage={panel?.kind === "stage" ? panel.id : null} onStageClick={openStage} />
      </div>
      <Feed activity={activity} git={git} onRunClick={openRun}
            selectedRunId={panel?.kind === "run" ? panel.runId : null} />
      {panel ? (
        <SidePanel panel={panel} run={openRunObj} runByItem={runByItem}
                   onOpenRun={openRun} onClose={() => setPanel(null)} />
      ) : null}
    </div>
  );
}
