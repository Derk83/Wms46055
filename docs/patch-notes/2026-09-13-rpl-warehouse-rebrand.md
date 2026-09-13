# Patch Notes — 2026-09-13 — Rebrand: Black Box RPL Warehouse → RPL Warehouse

## What changed
All user-visible brand strings changed from "Black Box RPL Warehouse" to
**"RPL Warehouse"**. Logo image (`blackbox-logo.png`) is preserved
unchanged — only text strings were updated.

## Header layout
The top header now shows a **single line** "RPL Warehouse" next to the
logo. The two-line stacked layout ("Black Box RPL" / "Warehouse") has
been collapsed. On the request portal (`requests.rplwms.com`) the
subtitle "Material Requests" still appears below the brand-primary.

## PWA + mobile
- Manifest `name`: "RPL Warehouse"
- Manifest `short_name`: "RPL WMS" / "RPL Requests" (portal)
- `apple-mobile-web-app-title`: "RPL WMS" / "RPL Requests"
- Service worker push default title: "RPL Warehouse"

## PDFs
All PDF templates updated:
- Page `<title>` and `<img alt>` (logo image stays the same)
- Document-name banners in daily/weekly reports ("BLACK BOX WAREHOUSE"
  → "RPL WAREHOUSE")
- Footer brand line (e.g. "Black Box RPL Warehouse · Pick Ticket · ..."
  → "RPL Warehouse · Pick Ticket · ...")

Affected templates:
- `ticket_print.html`, `cycle_count_pdf.html`,
  `cycle_count_results_pdf.html`, `reports_pdf.html`,
  `reports_weekly_pdf.html`, `receiving_ticket_print.html`,
  `item_barcode_print.html` (barcode label brand "BLACK BOX" →
  "RPL WAREHOUSE")

## Email
- `DEFAULT_FROM_EMAIL` default: `"RPL Warehouse <noreply@rplwms.com>"`
  (was `"Black Box RPL Warehouse <noreply@rplwms.com>"`)

## Files touched (27)
Templates: base, login, offline, settings, item_barcode_print, ticket_print,
cycle_count_pdf, cycle_count_results_pdf, reports_pdf, reports_weekly_pdf,
receiving_ticket_print, reports_pdf_viewer, material_request_* (6 files),
service_worker.

Code: views.py (PWA manifest), config/settings.py (DEFAULT_FROM_EMAIL).

Tests: tests.py, test_pwa_push.py, test_delivery_email.py.

Deploy + docs: ppe-push-notifications.{service,timer}, README.md.

## Test status
**372/372 pytest pass.**

## Deploy
- `manage.py check` — 0 issues
- `collectstatic` — synced
- `sudo systemctl restart ppe-inventory.service` applied
- Live verified: `<title>RPL Warehouse</title>`, brand-primary text
  "RPL Warehouse", logo alt "RPL Warehouse", PWA manifest JSON
  returns the new name

## Preserved on purpose
- Logo image filename `blackbox-logo.png` — Derek asked to leave the logo.
- Internal JS namespace `BBXNotifications` in `app.js` / `push.js` — not user-visible.
- Test email domains `@blackbox.com` in `test_weekly_auto.py` — internal test data.
- Real hostnames `bbx.rplwms.com` and `requests.rplwms.com` — DNS, not brand.

## Notes
- Derek's account password remains `ME!246aH` — never modified.
- Yesterday's hamburger revert is intact (`911de7d`) — no sidebar work touched.
