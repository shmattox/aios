import React from "react";
import PipelineGraph from "./PipelineGraph";
import { ActivityFeed } from "./ActivityFeed";
import "./style.css";

// Cockpit: the pipeline graph up top (using what was dead vertical space), the live activity
// feed filling the rest below.
export default function App() {
  return (
    <div className="cockpit">
      <div className="graph-wrap">
        <PipelineGraph />
      </div>
      <ActivityFeed />
    </div>
  );
}
