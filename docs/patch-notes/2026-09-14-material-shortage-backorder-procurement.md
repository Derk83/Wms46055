# Material Shortage, Backorder, and Procurement Workflow

**Released:** September 14, 2026
**Application commit:** `82a5f93`

## Summary

- Material requests and manually created pick tickets can no longer reduce an inventory item below zero.
- A requester who asks for more than is available must choose how the unallocated remainder is handled:
  - fulfill the available amount and cancel the remainder;
  - create a backorder; or
  - create a backorder linked to a Procurement requisition.
- Material-request lines now retain requested, immediately allocated, and outstanding quantities for clear auditability.

## Warehouse workflow

- Added a permission-controlled **Backorders** queue showing outstanding demand and current stock availability.
- Authorized warehouse staff can fulfill all or part of a backorder once stock is received.
- Each fulfillment creates an immutable supplemental pick ticket and records the stock issue through the existing inventory ledger.
- Request editing and deletion are blocked after fulfillment or Procurement submission so historical records cannot be orphaned.

## Procurement workflow

- Added a dedicated permission-controlled **Procurement** section.
- Procurement requisitions track status, vendor, PO number, ordered quantity, received quantity, expected delivery, notes, and responsible user.
- Procurement updates and backorder fulfillments are added to the existing material-request audit event stream.

## Safety and permissions

- Inventory allocation, request updates, backorder fulfillment, and Procurement updates use atomic transactions and deterministic row locking.
- Manual multi-line ticket creation/editing validates aggregate demand before applying any inventory changes.
- Supplemental fulfillment tickets cannot be changed or deleted through generic pick-ticket actions.
- Portal requesters cannot access warehouse Backorder or Procurement queues.

## Validation

- Full suite: 511 tests and 45 subtests passed.
- Focused shortage, request, and immutable-ledger suite: 60 tests and 7 subtests passed.
- Independent follow-up review: no blocker, high, or medium findings.
- Forward/reverse migration and synthetic legacy allocation backfill passed.
- Django system, migration drift, Python compilation, JavaScript syntax, and diff checks passed.
- Production migration `0047` applied successfully.
- Both HTTPS hosts and authenticated read-only workflow routes verified.
- Browser consoles reported zero errors; source and collected static-file hashes match.
- Production service active with zero unexpected restarts.
