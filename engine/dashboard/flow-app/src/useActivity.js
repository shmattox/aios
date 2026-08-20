import { useEffect, useState } from "react";

// Poll the dashboard's activity records (factory drains, pipeline stage runs, sessions).
// Same-origin (served under /pipeline/), so no CORS. Returns { runs, now }.
export function useActivity(interval = 4000) {
  const [data, setData] = useState({ runs: [], now: 0 });
  useEffect(() => {
    let alive = true;
    const load = () =>
      fetch("/api/activity")
        .then((r) => r.json())
        .then((d) => { if (alive) setData({ runs: d.runs || [], now: d._now || 0 }); })
        .catch(() => {});
    load();
    const t = setInterval(load, interval);
    return () => { alive = false; clearInterval(t); };
  }, [interval]);
  return data;
}
