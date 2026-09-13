# WMS4605

A Django warehouse-management system (WMS) for Black Box, deployed at
`https://bbx.rplwms.com`. This is the codebase that backs the working
production deployment: item catalog, pick tickets, material requests,
cycle counts, receiving, browser push for delivery notifications, and a
customer-facing request portal at `https://requests.rplwms.com`.

This repository was published to GitHub on 2026-09-13. The git history
was rewritten on first publish to (a) set commit authorship to the
project owner and (b) remove thirteen SQLite database backups plus a
`backups/` archive directory that had accidentally been committed in
local development and contained user data. See
[Security notes](#security-notes) below for the maintenance burden that
fall-out from that leak imposes.

## What the app does

Two surfaces, one Django project:

- **`bbx.rplwms.com`** — warehouse-floor WMS. Inventory items, picker
  work queue, pick tickets, receiving, cycle counts, settings, group
  permissions, manager dashboard. 115 URL routes total.
- **`requests.rplwms.com`** — requester-facing portal. Customers
  create material requests, get a ready-for-delivery email with secure
  response buttons, opt in to browser push for delivery status. Shared
  authentication cookies via CSRF_TRUSTED_ORIGINS.

Both surfaces share the same SQLite database and the same Django app
(`inventory`). The split is enforced at the URL conf level:

- `config/urls.py` mounts the WMS at `/`
- `inventory/request_urls.py` is mounted as a separate Django URL
  namespace for the portal

### Domain model

Eighteen models in `inventory/models.py`. The headline ones:

| Model | Purpose |
|-------|---------|
| `InventoryItem` | A warehouse item (part number, name, description, barcode, stock count, storage location). Generates Code 128 and QR code images on demand. |
| `InventoryTransaction` | Immutable ledger row for every stock change. Posted transactions cannot be bulk-updated — see the `Update` / `bulk_update` overrides on related querysets. |
| `PickTicket` / `PickTicketLine` | A picker's work order. Deletion reverses inventory through the audited instance paths. |
| `ReceivingTicket` / `ReceivingLine` / `ReceivingDocument` | Inbound shipments with document attachments (delivery notes, packing slips, photos). |
| `MaterialRequest` / `MaterialRequestLine` / `MaterialRequestEvent` | A customer request fulfilled through its automatically linked pick ticket. Event log captures every state transition. |
| `CycleCount` / `CycleCountItem` | Periodic stock-take. Lifecycle (complete / cancel / reopen) is gated on the `manage_cycle_counts` permission. |
| `ApprovalRequest` | Pending workflow item a manager approves or rejects. |
| `PushSubscription` / `PushDelivery` | Browser-push outbox. Transactional; failed deliveries retry with bounded exponential delay. |
| `ManagedGroupRole` | Stable identity for app-managed auth groups whose display names are editable. |
| `ItemImage` / `ItemDocument` | Per-item media gallery and attachments. |

### Permissions / roles

Authenticated through Django's auth system with a permission per
operation (`perform_cycle_count`, `manage_cycle_counts`,
`assign_materialrequest`, `archive_cycle_counts`, etc.). Five managed
groups are provisioned via data migrations:

- Procurement Manager
- Procurement Specialist
- Logistics Manager
- Logistics Specialist
- Requester (the customer-portal role)

Settings → Groups → Permissions page exposes a "Check all" toggle that
flips all 95 permission checkboxes at once; the toggle targets the
whole grid, not just inventory permissions.

## Tech stack

| Layer | Choice |
|-------|--------|
| Framework | Django 6.1.1 (Python 3.13) |
| DB | SQLite (single file at `db.sqlite3`, online-backup-safe) |
| HTTP server | Gunicorn 26.2.0, 3 workers, bound to `127.0.0.1:8089`, fronted by an Nginx Proxy Manager HTTPS reverse proxy |
| Push | `pywebpush` 2.5 with VAPID; outbox pattern via `PushDelivery` rows |
| PDFs | `weasyprint` 68 for printable pick tickets |
| Lockout | `django-axes` 8.3 — 5-fail / 15-min cooloff; admins can clear via Settings → Users → Unlock |
| CSP | `django-csp` 4.0 with per-request nonce; `html5-qrcode` is vendored under `inventory/static/inventory/js/vendor/` |
| Scanner | html5-qrcode 2.3.8 served from local static, no third-party runtime fetches |
| Tests | `pytest` 9 + `pytest-django`, 55 + tests covering role permissions, lifecycle guards, scanner self-hosting, CSP nonces, MR assignment |
| Task scheduling | Three systemd timers: `ppe-inventory-backup.timer` (nightly SQLite + source tar.gz → NAS), `ppe-push-notifications.timer` (push outbox drain), `ppe-material-request-archive.timer` (board archive roll-up) |

## Project layout

```
config/                          Django project (settings, wsgi, asgi, urls)
inventory/                       The single application
  migrations/0040..0044/         Schema + permission provisioning migrations
  static/inventory/js/vendor/    Vendored html5-qrcode (no CDN at runtime)
  static/inventory/css/app.css   Project stylesheet
  templates/inventory/           ~30 templates for WMS + portal views
  templatetags/                  Custom template tags
  management/commands/           `process_push_notifications` and friends
  models.py, views.py, forms.py, urls.py, request_urls.py,
  delivery_email.py, push.py, services.py, signals.py,
  middleware.py, test_*.py
deploy/
  ppe-push-notifications.service | .timer
  ppe-material-request-archive.service | .timer
  PWA_PUSH.md                    Push operations doc (config keys, runtime)
  requirements-push.txt          pywebpush-only overlay
scripts/
  backup_wms.py                  Online-backup of db.sqlite3 + source snapshot
  backup_db.py | .sh             Smaller per-DB dump helpers
  backup_to_nas.sh | restore_from_nas.sh
requirements.txt                 Pinned production install
pytest.ini                       Test config (DJANGO_SETTINGS_MODULE)
manage.py
```

## Running locally

Requires Python 3.13. SQLite is bundled with the standard library. The
shared venv lives next to the project — if you cloned fresh, create it
where the systemd service expects it:

```bash
cd /home/hermes/projects
python3.13 -m venv ppe-pick-ticket-venv
source ppe-pick-ticket-venv/bin/activate
pip install -r ppe_inventory/requirements.txt
pip install -r ppe_inventory/deploy/requirements-push.txt   # for push support
cd ppe_inventory
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 0.0.0.0:8000
```

Then visit `http://127.0.0.1:8000/`. To exercise the request portal
routes too, run with a different settings module that points
`ROOT_URLCONF` at `inventory.request_urls`, or front the dev server
with a local proxy that matches the two-host split.

## Running the test suite

```bash
cd /home/hermes/projects/ppe_inventory
source ../ppe-pick-ticket-venv/bin/activate
pytest                         # full suite
pytest inventory/test_user_unlock.py    # one test module
```

The suite produces 7 known warnings about `EMAIL_BACKEND` /
`EMAIL_HOST` / etc. becoming a unified `MAILERS` setting in Django 7.
Safe to ignore until the Django 7 upgrade lands.

## Configuration

All runtime configuration is read from environment variables in
`config/settings.py`. Defaults are safe-for-local-dev; production sets
these in `/etc/ppe-inventory.env`:

| Env var | Purpose |
|---------|---------|
| `DJANGO_SECRET_KEY` | Cryptographic key. **Change in production.** |
| `DJANGO_DEBUG` | `True` / `False`. Defaults to `False`. |
| `EMAIL_BACKEND` | `django.core.mail.backends.smtp.EmailBackend` for Postfix on localhost |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_TIMEOUT` | Standard SMTP wiring |
| `DEFAULT_FROM_EMAIL` | Override the default `From:` header (default: `Black Box Warehouse <noreply@rplwms.com>`) |
| `WEBPUSH_VAPID_PUBLIC_KEY`, `WEBPUSH_VAPID_PRIVATE_KEY`, `WEBPUSH_VAPID_SUBJECT` | VAPID keys for browser push. Private key must be root-readable only. See `deploy/PWA_PUSH.md`. |
| `REQUEST_PORTAL_BASE_URL` | Defaults to `https://requests.rplwms.com` |

## Deployment

Production deploy is on a Proxmox LXC at `192.168.0.177`. Three systemd
units:

- `ppe-inventory.service` — gunicorn binding `127.0.0.1:8089`,
  reloaded via `systemctl reload ppe-inventory` after migrations
- `ppe-inventory-backup.timer` — nightly systemd timer, calls
  `scripts/backup_wms.py`, writes to NAS via `backup_to_nas.sh`
- `ppe-push-notifications.timer` — drains push outbox every few minutes
- `ppe-material-request-archive.timer` — rolls up old material-request
  board state

HTTPS termination happens at an Nginx Proxy Manager reverse proxy on
`bonksystems.com`, which forwards to the LXC gunicorn upstream.

### Releasing a code change

1. Merge / commit on `master`.
2. `ssh pve01 sudo -u hermes bash -c 'cd /home/hermes/projects/ppe_inventory && \
    /home/hermes/projects/ppe-pick-ticket-venv/bin/python manage.py migrate --noinput'`
3. `ssh pve01 sudo systemctl reload ppe-inventory`
4. Smoke-test critical paths with `.verify_live_delivery.py`,
   `.smoke_material_requests.py`, or the Playwright harness under
   `deploy/`.

## Security notes

Two things worth flagging, both about history rather than current code.

### 1. Database backups were committed before 2026-09-13

Prior to the initial GitHub push, this repo's working tree contained
thirteen SQLite backup files at the top level
(`db.sqlite3.pre-*.bak`) plus a `backups/` directory of dated
`tar.gz` archives of the project tree. Several of those `.bak` files
contained live WMS data including user password hashes and active
session keys. All of those files have been **stripped from every
commit's tree** via `git-filter-repo` and the push to GitHub carries
clean history.

Practical consequences:

- Anyone who reads the GitHub repo's git history (e.g. via
  `git clone --mirror` and walking objects) cannot recover the leaked
  data.
- The **hashes themselves are out**, though — they were in the files
  briefly. Treat them as compromised. **All seven `auth_user` rows
  from the dev environment should have their passwords rotated** and
  any active sessions invalidated. This README does not log into your
  live system to do that for you; that is an operational follow-up
  (Settings → Users → Unlock each row, or
  `python manage.py changepassword <username>`).
- The `.gitignore` now ignores `*.bak`, `backups/`, `*.sqlite3`, and
  several other patterns. See `.gitignore` for the full list.

### 2. CSRF_TRUSTED_ORIGINS includes internal hostnames

`config/settings.py` lists trusted origins including `192.168.0.177`,
`.ts.net`, and Tailscale-style hostnames for local-network access in
addition to the production domains. If you're hardening for a
public-internet deployment, prune the list.

## License

No license file has been added. Default copyright applies. If you fork
this for internal use, add a `LICENSE` file before publishing or
explicitly mark the repo private on your fork.

## Related repositories

This is the working codebase. Adjacent systems live separately:

- `immich` on CT111 — photo management (LXC at `192.168.0.111`)
- `Jellyfin` / `*arr` stack on CT113 (QSV) — media
- Nextcloud on `bonkvault` (NAS) — file storage, including
  `WMS updates/patch notes/`
- Home Assistant + Frigate — security camera alerts (HA Companion App
  notifications only)

## Author

Maintained by Derek Hefner (`Blargy83@gmail.com`, GitHub
`@Derk83`). Commit history is authored under the no-reply GitHub
address `150742612+Derk83@users.noreply.github.com` for privacy;
display name shows `Derk83`.
