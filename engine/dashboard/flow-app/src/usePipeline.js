import { useEffect, useState } from "react";

// Poll the dashboard's pipeline model. Same-origin (served under /pipeline/), so no CORS.
export function usePipeline(interval = 4000) {
  const [model, setModel] = useState(null);
  useEffect(() => {
    let alive = true;
    const load = () =>
      fetch("/api/pipeline").then((r) => r.json()).then((m) => { if (alive) setModel(m); }).catch(() => {});
    load();
    const t = setInterval(load, interval);
    return () => { alive = false; clearInterval(t); };
  }, [interval]);
  return model;
}
