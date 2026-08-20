import { useEffect, useState } from "react";

// Poll the dashboard's git state (active worktrees + open PRs). PRs are server-side bg-cached,
// so a 10s poll is plenty; worktrees change only when a drain starts/stops.
export function useGit(interval = 10000) {
  const [data, setData] = useState({ worktrees: [], prs: { items: [], loading: true } });
  useEffect(() => {
    let alive = true;
    const load = () =>
      fetch("/api/git")
        .then((r) => r.json())
        .then((d) => { if (alive) setData({ worktrees: d.worktrees || [], prs: d.prs || { items: [], loading: false } }); })
        .catch(() => {});
    load();
    const t = setInterval(load, interval);
    return () => { alive = false; clearInterval(t); };
  }, [interval]);
  return data;
}
