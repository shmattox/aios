import React, { useEffect, useState, useCallback } from "react";
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
    <div className={"pnode" + (data.hot ? " hot" : "") + (data.sel ? " sel" : "")}>
      <Handle type="target" position={Position.Left} />
      <div className="pnode-lbl">{data.label}</div>
      <div className={"pnode-count" + (data.count ? "" : " zero")}>{data.count}</div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
const nodeTypes = { stage: StageNode };

// Drill-in side panel: the full item list for the clicked stage (fetched on demand).
function DetailPanel({ stageId, label, count, detail, onClose }) {
  const items = detail && detail.items;
  return (
    <aside className="detail">
      <header className="detail-head">
        <div>
          <div className="detail-lbl">{label || stageId}</div>
          <div className="detail-sub">{count} item{count === 1 ? "" : "s"}</div>
        </div>
        <button className="detail-x" title="Close" onClick={onClose}>✕</button>
      </header>
      <div className="detail-body">
        {!detail ? (
          <div className="detail-empty">Loading…</div>
        ) : !items.length ? (
          <div className="detail-empty">No items in this stage.</div>
        ) : (
          items.map((it) => (
            <div className="ditem" key={it.id}>
              <div className="ditem-top">
                <span className="ditem-id">{it.id}</span>
                {it.repo ? <span className="ditem-repo">{it.repo}</span> : null}
              </div>
              <div className="ditem-title">{it.title}</div>
            </div>
          ))
        )}
      </div>
    </aside>
  );
}

function Graph() {
  const model = usePipeline(4000);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selected, setSelected] = useState(null);   // clicked stage id
  const [detail, setDetail] = useState(null);        // fetched {id,label,count,items}
  const store = useStoreApi();

  const stageById = new Map((model?.stages || []).map((s) => [s.id, s]));

  // Sync the polled model into React Flow state (nodes carry the selected flag for highlight).
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
        return { ...base, data: { label: s.label, count: s.count, hot: hot.has(s.id), sel: s.id === selected } };
      });
    });

    setEdges(
      (model.edges || []).map((e) => ({
        id: e.from + ">" + e.to, source: e.from, target: e.to,
        animated: active.has(e.from + ">" + e.to),
      }))
    );
  }, [model, selected, setNodes, setEdges]);

  // Drill-in: fetch the full item list for the selected stage, and keep it live via an internal
  // interval (NOT the `model` dep — that re-ran this effect every poll, and the cleanup's alive=false
  // aborted the in-flight setDetail so the list never committed). Clears stale detail on switch.
  useEffect(() => {
    if (!selected) { setDetail(null); return; }
    let alive = true;
    setDetail(null);   // show Loading while the newly-selected stage loads
    const load = () =>
      fetch("/api/pipeline/stage/" + encodeURIComponent(selected))
        .then((r) => r.json())
        .then((d) => { if (alive) setDetail(d); })
        .catch(() => {});
    load();
    const t = setInterval(load, 4000);
    return () => { alive = false; clearInterval(t); };
  }, [selected]);

  const onNodeClick = useCallback((_evt, node) => {
    setSelected((cur) => (cur === node.id ? null : node.id));   // toggle
  }, []);

  // Force React Flow to measure node/handle bounds. Its ResizeObserver auto-measure does not
  // fire reliably for async-mounted nodes here (also embedded in an iframe), leaving handleBounds
  // null so every edge is silently dropped. Retry until nodes are painted and edges render, then
  // stop; re-arms on each poll via the `nodes` dep.
  useEffect(() => {
    if (!nodes.length) return;
    let cancelled = false, tries = 0, timer;
    const tick = () => {
      if (cancelled) return;
      const els = document.querySelectorAll(".react-flow__node");
      if (els.length) {
        store.getState().updateNodeDimensions(
          Array.from(els).map((el) => ({
            id: el.getAttribute("data-id"), nodeElement: el, forceUpdate: true,
          }))
        );
      }
      tries += 1;
      const rendered = document.querySelector(".react-flow__edge-path");
      if (!rendered && tries < 30) timer = setTimeout(tick, 120);
    };
    timer = setTimeout(tick, 0);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [nodes, store]);

  const sel = selected ? stageById.get(selected) : null;

  return (
    <div style={{ width: "100vw", height: "100vh" }}>
      <ReactFlow
        nodes={nodes} edges={edges}
        onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
        onNodeClick={onNodeClick} onPaneClick={() => setSelected(null)}
        nodeTypes={nodeTypes} fitView nodesConnectable={false} elementsSelectable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#26282c" gap={22} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
      {selected ? (
        <DetailPanel
          stageId={selected} label={sel?.label} count={sel?.count ?? 0}
          detail={detail} onClose={() => setSelected(null)}
        />
      ) : null}
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
