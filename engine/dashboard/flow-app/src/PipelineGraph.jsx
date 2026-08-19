import React, { useEffect } from "react";
import ReactFlow, {
  Background, Controls, Handle, Position, useNodesState, useEdgesState,
} from "reactflow";
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
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  useEffect(() => {
    if (!model) return;
    const stages = model.stages || [];
    const flows = model.flows || [];
    const active = new Set(flows.map((f) => f.from + ">" + f.to));
    const hot = new Set(flows.flatMap((f) => [f.from, f.to]));

    // Merge onto existing node objects by id so React Flow's measured fields
    // (width/height/handleBounds) survive each poll — no re-measure flicker.
    setNodes((prev) => {
      const byId = new Map(prev.map((n) => [n.id, n]));
      return stages.map((s, i) => {
        const base = byId.get(s.id) || {
          id: s.id, type: "stage", draggable: false,
          position: { x: i * STAGE_X, y: STAGE_Y },
        };
        return { ...base, data: { label: s.label, count: s.count, hot: hot.has(s.id) } };
      });
    });

    setEdges(
      (model.edges || []).map((e) => ({
        id: e.from + ">" + e.to, source: e.from, target: e.to,
        animated: active.has(e.from + ">" + e.to),
      }))
    );
  }, [model, setNodes, setEdges]);

  return (
    <div style={{ width: "100vw", height: "100vh" }}>
      <ReactFlow
        nodes={nodes} edges={edges}
        onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes} fitView nodesConnectable={false} elementsSelectable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#26282c" gap={22} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
