import React, { useEffect } from "react";
import ReactFlow, {
  Background, Controls, Handle, Position,
  ReactFlowProvider, useNodesState, useEdgesState, useStoreApi,
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

function Graph() {
  const model = usePipeline(4000);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const store = useStoreApi();

  // Sync the polled model into React Flow state.
  useEffect(() => {
    if (!model) return;
    const stages = model.stages || [];
    const flows = model.flows || [];
    const active = new Set(flows.map((f) => f.from + ">" + f.to));
    const hot = new Set(flows.flatMap((f) => [f.from, f.to]));

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

  // Force React Flow to measure node/handle bounds AFTER the nodes commit to the DOM.
  // Some environments' ResizeObserver does not fire for async-mounted nodes, leaving
  // handleBounds null so every edge is silently dropped. Measuring directly from the
  // committed DOM elements is deterministic. Keyed on `nodes` so it runs post-commit.
  useEffect(() => {
    if (!nodes.length) return;
    const raf = requestAnimationFrame(() => {
      const els = document.querySelectorAll(".react-flow__node");
      if (!els.length) return;
      store.getState().updateNodeDimensions(
        Array.from(els).map((el) => ({
          id: el.getAttribute("data-id"), nodeElement: el, forceUpdate: true,
        }))
      );
    });
    return () => cancelAnimationFrame(raf);
  }, [nodes, store]);

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

export default function PipelineGraph() {
  return (
    <ReactFlowProvider>
      <Graph />
    </ReactFlowProvider>
  );
}
