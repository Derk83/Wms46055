# PWA and Web Push operations

The WMS and request portal share a root-scoped service worker and use host-specific manifests. Authenticated HTML and API responses are never cached. Non-GET requests are never intercepted or replayed; inventory mutations remain online-only.

## Required private configuration

Set these in `/etc/ppe-inventory.env` (never in source control):

```text
WEBPUSH_VAPID_PUBLIC_KEY=<URL-safe public key>
WEBPUSH_VAPID_PRIVATE_KEY=<private PEM value or private-key file accepted by pywebpush>
WEBPUSH_VAPID_SUBJECT=mailto:<operations-address>
```

The public key is intentionally sent to authorized browsers. The private key must remain readable only by root and the service process.

## Runtime

Install `deploy/requirements-push.txt`, apply migrations, and enable `ppe-push-notifications.timer`. New-request deliveries are attempted after the database transaction commits. Transient failures remain in the database outbox and are retried by the timer with bounded exponential delay. HTTP 404/410 endpoints are removed. Disabled endpoints are retained for 30 days; completed delivery records are retained for 90 days.

Users with `inventory.view_materialrequest` enable or disable notifications from the bell in the WMS header. Browser permission is requested only after that button is pressed. Subscriptions are bound to the authenticated Django session; expired or logged-out sessions cannot receive request details. Explicit logout also unregisters that browser endpoint when online.

## Verification

```bash
python manage.py check
python manage.py migrate --check
python manage.py process_push_notifications --limit 1
systemctl status ppe-push-notifications.timer
curl -I https://bbx.rplwms.com/service-worker.js
curl -I https://requests.rplwms.com/manifest.webmanifest
```
