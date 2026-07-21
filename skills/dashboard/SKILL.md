---
name: dashboard
description: Launch the AIOS operating dashboard — a local Linear-style web UI over the brief, gate queue, factory standup, state mirror, and cost ledgers. Use when the user says "open the dashboard", "dashboard", or wants a visual cockpit instead of the chat brief.
---

# aios:dashboard — launch the operating UI

1. Resolve the env root the standard way (walk up from cwd to the first dir
   containing both `state/` and `profile/`). Run from anywhere inside the env.
2. Launch (background it so the session stays free):

   `python <plugin_root>/engine/dashboard/dashboard_server.py --open`

   where `<plugin_root>` is this plugin's install directory. The server prints
   `aios-dashboard: http://127.0.0.1:8642/` and opens the browser. If the port
   is busy, a server is already running — it just opens the browser and exits.
3. Tell the user the URL and stop. Do NOT re-render dashboard data in chat —
   the dashboard is the render surface (deterministic-render rule).

Notes: 127.0.0.1-only; POSTs carry a per-start token injected into the page.
Writes go exclusively through allowlisted gated engine CLIs (ship/reject/walk
decision/veto revert) — Paper-Governs holds hold, every ship keeps its revert
pointer. Mirror browser is read-only until A64. Phase-2 phone access:
`tailscale serve 8642` (env-side, H61).
