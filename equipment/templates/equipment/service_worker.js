{% load static inventory_extras %}
"use strict";
const CACHE_VERSION = "rpl-equipment-shell-v2";
const OFFLINE_URL = "/offline/";
const SHELL = [
  OFFLINE_URL,
  "{% versioned_static 'inventory/css/app.css' %}",
  "{% versioned_static 'equipment/css/equipment.css' %}",
  "{% versioned_static 'equipment/js/equipment.js' %}",
  "{% static 'equipment/icons/equipment-192.png' %}"
];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE_VERSION).then(cache => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key.startsWith("rpl-equipment-shell-") && key !== CACHE_VERSION).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request).catch(() => caches.match(OFFLINE_URL)));
    return;
  }
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request)));
  }
});
