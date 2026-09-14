# Request Portal Theme Alignment and Inventory Reset

**Release date:** September 14, 2026

**Application:** RPL Warehouse WMS / Material Requests portal

## Summary

The public and authenticated `requests.rplwms.com` interface now uses the established RPL Warehouse visual system. The production inventory catalog was also cleared, as authorized, for the upcoming replacement spreadsheet import.

## Portal interface

- Rebuilt the public access screen with the shared RPL Warehouse dark/light color tokens, typography, controls, cards, and Black Box branding.
- Added a responsive two-column desktop form and single-column mobile layout.
- Corrected checkbox alignment, field spacing, hierarchy, and action placement.
- Added clear access, verification, and manager-approval guidance.
- Applied the portal-specific header treatment to authenticated request and inventory screens.
- Preserved the existing onboarding, sign-in, material-request, permission, host-isolation, CSRF, honeypot, and generic-response behavior.
- Added persisted theme selection and operating-system light-theme preference support.
- Corrected light-theme portal-header contrast following independent review.

## Authorized inventory reset

A recoverable backup was created before the reset:

- File: `backups/wms-backup-20260914-103259.tar.gz`
- SHA-256: `efa7067666bab8165ee7bd328c94993c6283b4df4e1d8bfcb260fee2e5f48cfc`

Deleted production inventory-linked records:

- 306 inventory items
- 300 immutable inventory transactions, including the linked receipt/reversal pair
- 615 cycle-count item records
- 1 receiving document
- No pick-ticket or receiving-ticket lines were present

Post-reset verification confirmed zero inventory items, inventory transactions, cycle-count item rows, receiving documents, item images, item documents, pick-ticket lines, and receiving lines. User accounts, groups, permissions, portal configuration, and cycle-count headers were retained.

## Validation

- Targeted portal/onboarding/navigation/UI suite: **57 passed, 21 subtests passed**
- Full release suite: **475 passed, 42 subtests passed**
- Django system check: passed
- Migration drift check: passed
- Python compilation: passed
- JavaScript syntax checks: passed
- Independent UI/security review: passed after resolving all high/medium findings
- Desktop and 390 px mobile renders verified in light and dark themes
- Live `requests.rplwms.com`: HTTP 200, current versioned CSS/JavaScript, zero browser-console errors
- Live authenticated request board and inventory paths: HTTP 200 using `force_login`
- Live `bbx.rplwms.com`: expected authentication redirect
- `ppe-inventory.service`: active, zero unexpected restarts

## Source

- Portal implementation commit: `437a90dfc0aa0baf8a02a664910af3972585f943`
