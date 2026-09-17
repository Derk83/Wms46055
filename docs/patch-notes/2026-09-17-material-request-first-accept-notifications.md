# Material Request First-Accept Notifications

**Released:** September 17, 2026
**Application:** RPL Warehouse / Material Requests

## Summary

New material requests now alert eligible logistics specialists without requiring a page refresh. The first specialist to accept an available request is atomically assigned both the material request and its linked pick ticket.

## Changes

- Added an actionable in-app notification within approximately three seconds of request creation.
- Added **View** and **Accept request** actions to the warehouse alert.
- Added a signed, user-specific **Accept request** action to supported Web Push notifications.
- Made acceptance first-come, first-served with an atomic conditional claim.
- Assigns the material request and linked pick ticket together.
- Opens the request and existing pick-ticket acknowledgement workflow after a successful acceptance.
- Records acceptance in the material-request audit history.
- Updates other specialists' notifications with the winning assignee and removes stale Accept actions.
- Shows clear conflict, authorization, session, and network feedback for unsuccessful acceptance attempts.
- Added strict warehouse-host and permission enforcement to event and claim endpoints.
- Scoped polling cursors and cross-tab notification state by host and user.
- Added single-flight polling and monotonic cursor updates.
- Added light- and dark-theme responsive popup styling.

## Safety and validation

- Inventory quantities are not changed by accepting a request; existing reservation and fulfillment services remain authoritative.
- Claiming requires warehouse-wide request visibility and pick-ticket fulfillment permission.
- Signed push claims are bound to the user, request, and creation event and expire after 24 hours.
- Full release gate: **590 passed, 2 skipped, 55 subtests passed**.
- Django system check, migration dry-run, Python compilation, JavaScript syntax, and diff checks passed.
- Independent security/concurrency review found no unresolved blocker, high, or medium findings.
- Rendered workflow verified in light and dark themes with zero browser-console errors.

## Release

- Feature commit: `9c3a5ab`
- No database migration was required.
