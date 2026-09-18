# Equipment Requests and Recurring Vehicle Maintenance

**Released:** 2026-09-17  
**Production revision:** `efa1982`

## Summary

This release adds a standalone requester-facing Equipment Requests application at [eqreq.rplwms.com](https://eqreq.rplwms.com) and recurring vehicle-maintenance planning within Equipment Manager.

## Equipment Requests

- Added a dedicated requester application with its own hostname, navigation, theme, PWA manifest, service worker, help page, and account page.
- Requesters can request known equipment categories or describe unlisted equipment without seeing the internal asset register.
- Supports multiple request lines, quantities, need/return dates, destination, project, purpose, priority, substitutions, and requester notes.
- Requesters see only their own request records and may edit or cancel only during permitted workflow states.
- Added manager request queues, assignment, review, approval, asset allocation, readiness, fulfillment, decline, cancellation, and audited status history.
- Approved allocations create linked equipment reservations while preserving the existing custody and checkout audit path.
- Request and reservation state changes are synchronized transactionally to prevent lifecycle drift.

## Permission and data safety

- Requester access is isolated from Equipment Manager assets, custody, maintenance, cost, reporting, and administrative screens.
- Added separate requester-access and manager-processing permissions.
- Removed unintended request-management access from unrelated operational manager roles.
- Duplicate asset allocations and duplicate request-line selections are rejected cleanly.
- Requesters cannot access manager maintenance routes through the requester hostname.
- Status transitions and allocation changes are written to the request event history.

## Recurring vehicle maintenance

- Added calendar- and mileage-based recurring maintenance plans.
- Added odometer/hour meter readings and due-service projections.
- Added maintenance work orders with scheduling, start, completion, vendor, cost, meter-at-completion, and service notes.
- Added manager schedule, plan, work-order, and vehicle service-history views.
- Vehicles retain restrictive statuses such as Retired, Lost, or Out of Service when maintenance closes.
- Open work orders protect their originating plan from unsafe edits or deletion.
- A daily systemd timer generates due work orders at 4:45 AM Central.

## Mobile and accessibility

- Added responsive requester dashboards, request forms, status cards, pagination, and dynamic line controls.
- Added mobile-safe manager request queues and maintenance screens.
- Verified light and dark themes at desktop and 390-pixel mobile widths.
- Added visible labels, status text, accessible field errors, and touch-friendly controls.

## Deployment

- Added Cloudflare DNS for `eqreq.rplwms.com`.
- Added an Nginx Proxy Manager production route to the WMS application.
- Issued and attached a dedicated Let's Encrypt certificate using Cloudflare DNS validation.
- Enabled forced HTTPS, HTTP/2, and HSTS for the requester application.
- Applied Equipment migrations and collected static files.
- Enabled `ppe-vehicle-maintenance.timer`.

## Verification

- Full suite: **633 passed, 2 skipped, 55 subtests passed**.
- Django system checks passed.
- Migration drift check passed.
- Python compilation, JavaScript syntax checks, and `git diff --check` passed.
- Populated browser QA passed on desktop and mobile in light and dark themes.
- Public `https://eqreq.rplwms.com/login/` returns HTTP 200 with the versioned requester assets and no browser-console errors.
- Authenticated live verification passed for requester dashboard, new request, help, and account pages.
- Authenticated live verification passed for Equipment Manager request queue and maintenance schedule.
- Requester-host access to manager maintenance routes returns 404.
- `bbx.rplwms.com`, `requests.rplwms.com`, and `equipment.rplwms.com` remained healthy after deployment.
