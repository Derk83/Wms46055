# Manager material-request cascade deletion and BO search

**Released:** 2026-09-15 15:43 CDT
**Application commit:** `cd99bfb`

## Summary

Managers can now safely delete an entire material-request record graph from one protected confirmation dialog. Warehouse global search also includes permission-scoped backorder tickets.

## Changes

- Restricted whole-request deletion to Logistics Manager, Sr. Logistics Manager, Procurement Manager, and superusers with all required aggregate delete permissions.
- Added a required confirmation checkbox before any deletion occurs.
- The confirmation dialog lists the linked MR, PT, BO, PRQ, and supplemental fulfillment-ticket numbers with links that open in a new tab.
- Redirected deletion attempts from an original linked pick ticket into the same material-request confirmation workflow.
- Atomically deletes the material request, request lines, original pick ticket, backorders, procurement requisitions, fulfillment records, and supplemental fulfillment pick tickets.
- Restores inventory for every deleted original and supplemental pick line through the existing ledger-backed compensation path.
- Retains a durable deletion audit event with snapshots of all linked record numbers.
- Keeps generic BO, PRQ, and supplemental-ticket deletion blocked.
- Added backorder tickets to global warehouse search for users authorized to view backorders.
- Added reversible manager-role permission grants in migration `0049` while preserving permissions that predated this release.
- Standardized request-before-ticket locking for linked workflows to prevent lock-order inversion.

## Safety and verification

- Required checkbox and manager authorization tested against direct POST bypass attempts.
- Foreign requests remain hidden with HTTP 404; visible requests return HTTP 403 to non-managers.
- Injected mid-cascade failure verified complete transaction rollback with no stock or record changes.
- Verified unrelated requests, tickets, requisitions, backorders, fulfillments, and inventory remain unchanged.
- Verified production has zero negative inventory items after deployment.
- Full automated gate: **520 tests passed, 45 subtests passed**.
- Django system checks, migration drift check, Python compilation, JavaScript syntax checks, and `git diff --check` passed.
- Independent security/concurrency review reported no blocker, high, or medium findings.
- Light and dark desktop rendering verified; responsive rules constrain the dialog to the viewport and stack actions on narrow screens.
- Production HTTPS, authenticated confirmation on both hosts, BO search, manager permission grants, static-file hash, service health, and zero unexpected restarts verified.
