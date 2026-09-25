# Centralized WMS Training Center

**Released:** September 24, 2026
**Application:** RPL Warehouse Management System
**Primary URL:** <https://demo.rplwms.com/>

## Summary

The public demo host is now the central landing page for WMS demonstrations, requester guides, and permission-protected module training. Training content uses fictional scenarios and directs authorized users into the corresponding live workspace only when they already hold the destination permission.

## Training catalog

### Guided demo

- Guided Inventory Demo

### Requester guides

- Request Material
- Request Equipment

### Warehouse module training

- Inventory, Locations & Scanning
- Receiving
- Pick Tickets & QA
- Material Request Processing
- Shortages, Backorders & Procurement
- Cycle Counts
- Transactions, Audit & Reports
- Users & Permissions

### Equipment operations training

- Equipment Asset Register
- Equipment Custody & Returns
- Equipment Reservations
- Equipment Maintenance & Rentals
- Equipment Request Queue
- Equipment Imports & Reporting

## Security and data-safety controls

- Warehouse and Equipment course pages require authentication.
- Each course checks its own module permissions.
- Warehouse and Equipment course routes remain isolated to their application hosts.
- Requester-only users cannot enter Equipment Manager training.
- Course pages accept safe HTTP methods only.
- Fictional course content does not mutate inventory, requests, tickets, equipment, or audit records.
- Warehouse training suppresses assigned-ticket prompts, live request-event polling, notifications, push registration, and production claim actions.
- Live-workspace links appear only when the signed-in user has the destination permission.

## Validation

- Full automated gate: **692 passed, 2 skipped, 61 subtests passed**.
- Django system checks: no issues.
- Migration drift check: no changes detected.
- Python and JavaScript syntax checks passed.
- Independent permission, host-isolation, live-data, and UI review: no blocker, high, or medium findings.
- Desktop light/dark and 390 px mobile layouts were rendered and checked.
- Production catalog returned HTTP 200 with 17 unique cards.
- All 16 authenticated training and requester-guide routes returned HTTP 200 using `Client.force_login`.
- Production inventory and equipment table counts were unchanged by authenticated verification.
- Deployed stylesheet SHA-256 matched the repository copy.
- Production browser console reported zero JavaScript errors.

## Deployment

- Feature commit: `9944864`
- Service: `ppe-inventory.service`
- Service state after deployment: active
- Unexpected service restarts: 0
