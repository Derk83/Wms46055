# WMS UI Audit Remediation — 2026-09-14

## Status

Deployed and verified in production on `bbx.rplwms.com`.

Release commits:

- `338659e` — Improve WMS usability and responsive workflows
- `fdaeb53` — Fix CSP nonces for inline WMS scripts

## Summary

Completed the full WMS UI audit remediation while preserving the established Black Box/RPL visual direction. The work focused on permission accuracy, responsive reliability, workflow clarity, accessible interaction, removal of duplicated controls, and consolidation of shared design-system behavior.

## Dashboard and navigation

- Reduced Dashboard Quick Actions to the two focused actions that are not duplicated elsewhere:
  - Add Item
  - Resume Work
- Kept operational metrics and permission-aware links in their existing dashboard sections.
- Removed redundant shortcuts that repeated primary navigation or required prior selections.
- Improved phone navigation behavior without redesigning the established WMS appearance.
- Added a proper mobile drawer focus trap, background inert state, Escape-to-close behavior, and focus return to the menu toggle.

## Inventory and stock workflows

- Restored distinct, usable routes for:
  - Batch quantity adjustments
  - Selection-based bulk item editing
- Corrected Batch Adjust so pending new items are not created until the operator commits the batch.
- Routed every committed adjustment through the immutable inventory ledger API.
- Kept item creation, quantity changes, and adjustment ledger entries inside one atomic transaction.
- Improved inventory selection behavior and responsive item layouts.
- Consolidated item-detail primary actions, media actions, barcode/UPC tools, labels, QR tools, and scanner affordances.

## Material requests and tickets

- Corrected action visibility so controls appear only when the user has the exact endpoint permissions required to use them.
- Improved material-request queue and board behavior across desktop, tablet, and phone layouts.
- Stabilized dynamic request-line add/remove behavior:
  - Correct form counts
  - Hidden deletion state
  - No duplicate element IDs
- Improved request, ticket, receiving, transaction, and detail-page action hierarchy.
- Removed duplicated and misleading page actions while preserving operational workflows.

## Cycle counts, receiving, settings, and reports

- Standardized page headers and semantic action styling across normal operational screens.
- Improved responsive behavior for cycle-count lists and details.
- Consolidated settings and group-permission presentation.
- Normalized receiving, report, transaction-history, and confirmation screens.
- Preserved permission boundaries for lifecycle and administrative operations.

## Design-system and maintenance cleanup

- Consolidated shared form, permission, empty-state, button, focus, and responsive styles into the application stylesheet.
- Added compatibility tokens for existing screens while reducing one-off styling.
- Consolidated duplicate view implementations and removed templates and routes proven unreachable:
  - Legacy approval queue
  - Legacy groups page
  - Legacy user form
- Added five UI-audit regression modules covering routes, permissions, accessibility, responsive behavior, workflow semantics, and dead-code contracts.

## CSP follow-up found during live verification

The first production browser pass detected inline scripts blocked by the strict Content Security Policy. The pages rendered, but some interactions could have failed silently.

Corrective action:

- Added the request-specific CSP nonce to every inline template script.
- Added a regression contract that rejects future inline scripts without `request.csp_nonce`.
- Preserved the strict policy; `unsafe-inline` was not enabled.
- Re-ran the complete suite and repeated the production browser matrix after the graceful reload.

## Verification

### Automated release gate

- 470 tests passed
- 42 subtests passed
- Django system check passed
- No migration drift
- JavaScript syntax check passed
- Git diff/whitespace check passed
- Independent security and logic reviews approved both the main release and CSP follow-up

### Isolated responsive browser matrix

- 24 breakpoint/page checks passed
- Widths covered: 390, 600, 601, 760, 761, 1024, 1120, and 1121 pixels
- Zero horizontal-overflow failures
- Dynamic material-request formset behavior passed
- Mobile drawer focus/inert/Escape behavior passed

### Production verification

Authenticated HTTPS verification covered Dashboard, Inventory, Tickets, Material Requests, Batch Adjust, and Settings at desktop and phone widths.

- 12 of 12 live page checks returned HTTP 200
- All expected page markers were present
- Zero horizontal-overflow failures
- Zero browser console errors
- Zero CSP violations
- Zero JavaScript page errors
- Public WMS login and request-portal splash pages returned HTTP 200
- Gunicorn gracefully reloaded and stabilized with three replacement workers

Production screenshots were captured locally under:

`/home/hermes/wms-ui-audit/live-2026-09-14/`

## Deployment details

- GitHub `master` synchronized with production checkout
- Database migration command completed with no pending migrations
- Static assets collected successfully
- `ppe-inventory.service` remained active throughout graceful Gunicorn HUP reloads
- No production inventory quantities, tickets, requests, user passwords, or permission assignments were changed during verification

## Non-blocking maintenance note

The release gate continues to report one existing Django 7 deprecation warning for `fail_silently` in the weekly report email command. It is unrelated to this release and does not affect current production behavior.
