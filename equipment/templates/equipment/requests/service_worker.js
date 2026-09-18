const CACHE = "rpl-equipment-requests-v2";
const OFFLINE = "/offline/";
const CORE = [
  OFFLINE,
  "/static/equipment/eqreq/eqreq.css",
  "/static/equipment/eqreq/eqreq-init.js",
  "/static/equipment/eqreq/eqreq.js",
  "/static/equipment/eqreq/eqreq-mark.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(CORE)));
});
self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))
  )));
});
self.addEventListener("fetch", (event) => {
  if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request).catch(() => caches.match(OFFLINE)));
  }
});
