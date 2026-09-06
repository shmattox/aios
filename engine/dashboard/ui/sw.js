// Service worker for the AIOS cockpit. It exists to make the app installable and does nothing else.
//
// It deliberately CACHES NOTHING. The server sends `Cache-Control: no-store` on every response by
// design — a cached old app.js against a new index.html renders blank — and a cockpit that serves
// stale queue state from a worker cache is worse than no cockpit: it would show a decision as
// pending that has already been made. The fetch handler is here because Chrome's install criteria
// require one; it hands every request straight back to the network.
//
// If you are about to add a cache here, the thing you actually want is probably an offline
// fallback PAGE that says "no connection" — never cached queue data.

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => e.respondWith(fetch(e.request)));
