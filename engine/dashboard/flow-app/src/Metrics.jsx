import React from "react";

const startOfTodaySec = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d.getTime() / 1000; };
const runTs = (r) => Math.max(r.ended || 0, r.started || 0);
const sum = (arr, f) => arr.reduce((a, r) => a + (f(r) || 0), 0);

function fmtUsd(n) { return "$" + (n || 0).toFixed(2); }
function fmtTok(n) {
  if (!n) return "0";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}

// Cost + throughput strip across the top of the cockpit, computed from the already-polled
// activity records + the pipeline model (gate depth). Fully client-side.
export function Metrics({ activity, model }) {
  const runs = activity.runs || [];
  const today = startOfTodaySec();
  const todayRuns = runs.filter((r) => runTs(r) >= today);
  const gate = (model?.stages || []).find((s) => s.id === "gate")?.count ?? 0;

  const cells = [
    { k: "spend · today", v: fmtUsd(sum(todayRuns, (r) => r.cost_usd)), sub: fmtUsd(sum(runs, (r) => r.cost_usd)) + " all" },
    { k: "tokens · today", v: fmtTok(sum(todayRuns, (r) => r.tokens)), sub: fmtTok(sum(runs, (r) => r.tokens)) + " all" },
    { k: "drains live", v: String(runs.filter((r) => r.surface === "factory" && r.live).length), accent: "run" },
    { k: "ships · today", v: String(todayRuns.filter((r) => r.status === "shipped").length), accent: "ok" },
    { k: "gate depth", v: String(gate), accent: gate ? "warn" : null },
  ];

  return (
    <div className="metrics">
      {cells.map((c) => (
        <div className="mcell" key={c.k}>
          <div className="mk">{c.k}</div>
          <div className={"mv" + (c.accent ? " " + c.accent : "")}>{c.v}</div>
          {c.sub ? <div className="msub">{c.sub}</div> : null}
        </div>
      ))}
    </div>
  );
}
