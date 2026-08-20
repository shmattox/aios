import React, { useState } from "react";
import PipelineGraph from "./PipelineGraph";
import { ActivityFeed } from "./ActivityFeed";
import { SidePanel } from "./SidePanel";
import { useActivity } from "./useActivity";
import "./style.css";

// Cockpit shell: owns the ONE right-side panel (a stage's items OR a run's log) shared by the
// graph and the feed, plus the activity poll (so a stage item can cross-link to its factory run).
export default function App() {
  const activity = useActivity(4000);
  const [panel, setPanel] = useState(null);   // null | {kind:"stage", id} | {kind:"run", run}

  // item_id -> its most-recent run, for stage-item cross-links.
  const runByItem = new Map();
  for (const r of [...activity.runs].sort((a, b) => (a.started || 0) - (b.started || 0))) {
    for (const iid of r.item_ids || []) runByItem.set(iid, r);
  }

  const openStage = (id) =>
    setPanel((p) => (id == null ? null : (p && p.kind === "stage" && p.id === id ? null : { kind: "stage", id })));
  const openRun = (run) =>
    setPanel((p) => (p && p.kind === "run" && p.run.id === run.id ? null : { kind: "run", run }));

  return (
    <div className="cockpit">
      <div className="graph-wrap">
        <PipelineGraph selectedStage={panel?.kind === "stage" ? panel.id : null} onStageClick={openStage} />
      </div>
      <ActivityFeed activity={activity} onRunClick={openRun}
                    selectedRunId={panel?.kind === "run" ? panel.run.id : null} />
      {panel ? (
        <SidePanel panel={panel} runByItem={runByItem} onOpenRun={openRun} onClose={() => setPanel(null)} />
      ) : null}
    </div>
  );
}
