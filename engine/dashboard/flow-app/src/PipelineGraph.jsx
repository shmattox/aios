import React, { useMemo } from "react";
import ReactFlow, { Background, Controls, Handle, Position } from "reactflow";
import "reactflow/dist/style.css";
import "./style.css";
import { usePipeline } from "./usePipeline";

const STAGE_X = 190, STAGE_Y = 90;

function StageNode({ data }) {
  return (
    <div className={"pnode" + (data.hot ? " hot" : "")}>
      <Handle type="target" position={Position.Left} />
      <div className="pnode-lbl">{data.label}</div>
      <div className={"pnode-count" + (data.count ? "" : " zero")}>{data.count}</div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
const nodeTypes = { stage: StageNode };

export default function PipelineGraph() {
  const model = usePipeline(4000);
  const { nodes, edges } = useMemo(() => {
    const stages = (model && model.stages) || [];
    const active = new Set(((model && model.flows) || []).map((f) => f.from + ">" + f.to));
    const hot = new Set(((model && model.flows) || []).flatMap((f) => [f.from, f.to]));
    const nodes = stages.map((s, i) => ({
      id: s.id, type: "stage", draggable: false, position: { x: i * STAGE_X, y: STAGE_Y },
      data: { label: s.label, count: s.count, hot: hot.has(s.id) },
    }));
    const edges = ((model && model.edges) || []).map((e) => ({
      id: e.from + ">" + e.to, source: e.from, target: e.to, animated: active.has(e.from + ">" + e.to),
    }));
    return { nodes, edges };
  }, [model]);

  return (
    <div style={{ width: "100vw", height: "100vh" }}>
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView
                 nodesConnectable={false} elementsSelectable={false} proOptions={{ hideAttribution: true }}>
        <Background color="#26282c" gap={22} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
