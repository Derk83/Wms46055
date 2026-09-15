# Shortage Guidance, Supply Navigation, and Delete Error Fix

**Released:** September 15, 2026
**Application commit:** `061de5c`

## Request shortage guidance

- The shortage block is now prominently highlighted with a strong amber border and background in light and dark themes.
- The block shows the requested, currently available, and remaining quantities before submission.
- Requester choices now use plain language:
  - **Use available stock and cancel the rest**
  - **Request the rest when available**
  - **Ask Procurement to purchase the rest**
- The selected action’s stored value and workflow behavior are unchanged.
- Zero-stock selections now reveal the shortage choice immediately.
- Editing a request containing an inactive inventory item now shows accurate availability.

## Navigation

- **Backorders** and **Procurement** are now permission-controlled links in the primary desktop header.
- Their duplicate **Supply** section was removed from the desktop **More** menu.
- Existing mobile navigation and permission boundaries are preserved.

## Delete error fix

- Deleting a pick ticket linked to a request that already entered Procurement no longer causes HTTP 500.
- The request and ticket remain protected and the user receives a clear message explaining why deletion is blocked.
- Material-request and pick-ticket deletion paths are covered on their exact production hostnames.
- No inventory, ledger, request, ticket, backorder, or Procurement record is partially changed when deletion is blocked.

## Verification

- Full suite: 515 tests passed plus 45 subtests.
- Focused deletion and compatibility suite: 65 tests passed plus 7 subtests.
- Independent review completed with all blocker/high/medium findings resolved.
- Django checks, migration consistency, Python compilation, JavaScript syntax, and diff checks passed.
- Migration `0048` is a SQL no-op that records updated display-label metadata only.
- Both public HTTPS hosts returned HTTP 200 with valid TLS.
- Authenticated request detail and both delete-confirmation routes returned HTTP 200.
- Browser console and deployed static-file freshness checks passed.
- Production inventory contained zero negative-quantity items at deployment.
