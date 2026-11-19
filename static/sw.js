// Service worker della AHA: rende l'app installabile e la apre anche senza connessione
// mostrando gli ultimi dati ricevuti. La versione viene inserita dal server.
const V = "{{V}}";
const SHELL = "aha-shell-" + V;
const DATA = "aha-data";
const PRECACHE = ["/", `/static/aha.js?v=${V}`, `/static/aha.css?v=${V}`, "/static/vendor/plotly.min.js",
  "/manifest.webmanifest", "/static/icons/icon-192.png", "/static/icons/icon-512.png"];
const API_TIMEOUT = 10000;

self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(k => k.startsWith("aha-shell-") && k !== SHELL).map(k => caches.delete(k))
  )).then(() => self.clients.claim()));
});

// rete prima, cache come riserva: i dati mostrati sono sempre gli ultimi disponibili
async function networkFirst(req, cacheName, timeout) {
  const cache = await caches.open(cacheName);
  try {
    const ctrl = new AbortController();
    const t = timeout ? setTimeout(() => ctrl.abort(), timeout) : null;
    const resp = await fetch(req, { signal: ctrl.signal });
    if (t) clearTimeout(t);
    if (resp.ok) cache.put(req, resp.clone());
    return resp;
  } catch (err) {
    const hit = await cache.match(req, { ignoreVary: true });
    if (!hit) throw err;
    // segnala all'interfaccia che il dato viene dalla memoria del telefono
    const h = new Headers(hit.headers); h.set("X-AHA-Offline", "1");
    return new Response(await hit.blob(), { status: hit.status, statusText: hit.statusText, headers: h });
  }
}

async function cacheFirst(req) {
  const hit = await caches.match(req, { ignoreVary: true });
  if (hit) return hit;
  const resp = await fetch(req);
  if (resp.ok) (await caches.open(SHELL)).put(req, resp.clone());
  return resp;
}

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;               // font Google e link esterni: gestiti dal browser
  if (url.pathname.startsWith("/api/")) {
    if (url.pathname === "/api/status") return;             // stato del server: solo in tempo reale
    e.respondWith(networkFirst(req, DATA, API_TIMEOUT));
  } else if (req.mode === "navigate") {
    e.respondWith(networkFirst(new Request("/"), SHELL, 8000));
  } else if (url.pathname.startsWith("/static/")) {
    e.respondWith(url.search || url.pathname.startsWith("/static/vendor/") || url.pathname.startsWith("/static/icons/")
      ? cacheFirst(req) : networkFirst(req, SHELL));
  }
});
