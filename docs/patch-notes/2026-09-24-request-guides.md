# Material and equipment request guides

**Released:** 2026-09-24

## Summary

Added step-by-step requester guides to the Material Requests and Equipment Requests apps. Each guide uses the existing requester interface and remains inside its app's established authentication and permission boundary.

## Material request guide

Available at `https://requests.rplwms.com/guide/` and from **Request Guide** in the material requester navigation.

The guide explains how to:

1. Enter requestor, delivery, timing, urgency, and notes.
2. Search live inventory and add material lines.
3. Choose a shortage action for unavailable quantities:
   - use available stock and cancel the remainder;
   - backorder the remainder; or
   - route the remainder to Procurement.
4. Submit the request and retain its request number.
5. Track fulfillment through the Request Board.

The page is limited to authenticated users who have the material portal and request creation/view permissions.

## Equipment request guide

Available at `https://eqreq.rplwms.com/help/` and from **Request guide** in the equipment requester navigation.

The guide explains how to:

1. Enter dates, destination, purpose, project, priority, substitute preference, and notes.
2. Choose a category, optionally select an available preferred asset, or describe unlisted equipment.
3. Submit and retain the request number.
4. Track the normal approval path and closed outcomes.
5. Edit while Submitted or cancel while Submitted or Under review.

Each guide links to the other requester app and warns that a separate sign-in may be required.

## Security and isolation

- Material guide route is available only on the Material Requests host.
- Equipment guide route remains isolated to the Equipment Requests host.
- Unauthorized users are denied and anonymous users are redirected to the appropriate login.
- Both guide endpoints reject unsafe HTTP methods.
- No warehouse, inventory, equipment, request, or audit records are changed by viewing either guide.

## Verification

- Full test suite: **667 passed**, **2 skipped**, **55 subtests passed**.
- Requester portal regression suite: **82 passed**, **2 skipped**.
- Django system check passed.
- No pending migrations.
- Python compilation and JavaScript syntax checks passed.
- Independent permission/UI review completed; all medium findings resolved.
- Production authenticated rendering verified on both host-specific routes.
- Production domain-table counts were unchanged after verification.
- `ppe-inventory.service` remained active with zero unexpected restarts.
