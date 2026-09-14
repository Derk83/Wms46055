{% load static %}
'use strict';

const CACHE_VERSION = 'bbx-shell-v4';
const OFFLINE_URL = '/offline/';
const SHELL_ASSETS = [
  OFFLINE_URL,
  '{% static "inventory/css/app.css" %}',
  '{% static "inventory/js/app.js" %}',
  '{% static "inventory/js/pwa.js" %}',
  '{% static "inventory/js/push.js" %}',
  '{% static "inventory/img/blackbox-logo.png" %}',
  '{% static "inventory/icons/icon-192.png" %}',
  '{% static "inventory/icons/icon-512.png" %}'
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE_VERSION).then((cache) => cache.addAll(SHELL_ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((key) => key !== CACHE_VERSION).map((key) => caches.delete(key))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  // Inventory mutations are online-only. Writes are never intercepted, persisted,
  // replayed, answered from a cache, or registered for deferred synchronization.
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
    return;
  }

  if (url.origin === self.location.origin && url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then((cached) => cached || fetch(request).then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE_VERSION).then((cache) => cache.put(request, copy));
        }
        return response;
      }))
    );
  }
});

self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (error) {
    data = {title: 'RPL Warehouse', body: 'A warehouse update is available.', url: '/'};
  }
  let url = new URL(data.url || '/', self.location.origin);
  if (url.origin !== self.location.origin) url = new URL('/', self.location.origin);
  let confirmUrl = null;
  if (data.confirmUrl) {
    const candidate = new URL(data.confirmUrl, self.location.origin);
    if (candidate.origin === self.location.origin) confirmUrl = candidate.pathname + candidate.search;
  }
  const options = {
    body: data.body || 'A warehouse update is available.',
    icon: '{% static "inventory/icons/icon-192.png" %}',
    badge: '{% static "inventory/icons/badge-96.png" %}',
    tag: data.tag || 'black-box-warehouse',
    renotify: true,
    requireInteraction: Boolean(data.requireInteraction),
    actions: Array.isArray(data.actions) ? data.actions.slice(0, 2) : [],
    data: {
      url: url.pathname + url.search,
      eventId: data.eventId || null,
      confirmUrl,
      confirmationToken: data.confirmationToken || null
    }
  };
  event.waitUntil(
    self.clients.matchAll({type: 'window', includeUncontrolled: true}).then((windows) => {
      windows.forEach((client) => client.postMessage({type: 'push-notification', payload: data}));
      return self.registration.showNotification(data.title || 'RPL Warehouse', options);
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  if (event.action === 'confirm-delivery' && data.confirmUrl && data.confirmationToken) {
    event.waitUntil(
      fetch(data.confirmUrl, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({token: data.confirmationToken})
      }).then((response) => {
        if (!response.ok) throw new Error('Delivery confirmation failed');
        return self.registration.showNotification('Delivery confirmation sent', {
          body: 'The warehouse has been notified that you are ready.',
          icon: '{% static "inventory/icons/icon-192.png" %}',
          tag: 'delivery-confirmed',
          data: {url: data.url || '/'}
        });
      }).catch(() => self.clients.openWindow ? self.clients.openWindow(data.url || '/') : undefined)
    );
    return;
  }
  let url = new URL(data.url || '/', self.location.origin);
  if (url.origin !== self.location.origin) url = new URL('/', self.location.origin);
  event.waitUntil(
    self.clients.matchAll({type: 'window', includeUncontrolled: true}).then((windows) => {
      for (const client of windows) {
        if ('focus' in client) {
          client.navigate(url.href);
          return client.focus();
        }
      }
      return self.clients.openWindow ? self.clients.openWindow(url.href) : undefined;
    })
  );
});
