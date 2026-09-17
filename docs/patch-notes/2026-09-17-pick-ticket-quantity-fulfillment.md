# Pick-ticket quantity fulfillment and signoff

**Released:** 2026-09-17

**Application:** RPL Warehouse WMS

**Code release:** `bc2dcd3`

## Summary

Pick tickets now preserve the requested quantity while recording the actual quantity picked for every line. Variances require a line-specific reason, inventory is reconciled atomically, and fulfillment records distinct picker and QA checker identities.

## Changes

- Added an **actual picked quantity** field for every pick-ticket line.
- Preserved the original requested quantity as immutable fulfillment history.
- Required a **variance reason** whenever actual quantity is above or below the requested quantity.
- Restored unused reserved inventory for short picks.
- Consumed additional available inventory for over-picks and rejected the entire fulfillment when sufficient stock is unavailable.
- Added separate active, authorized **picker** and **QA checker** signoffs; the same user cannot perform both roles.
- Linked material-request assignment to the ticket picker and required acknowledgement before assigned work can be fulfilled.
- Migrated existing open material-request assignments onto their linked tickets.
- Prevented direct workflow-status bypasses and blocked post-fulfillment ticket or linked-request edits that could alter recorded inventory history.
- Linked every ticket line to its exact reservation and variance ledger transactions for safe compensation, including duplicate-item lines.
- Improved transaction-history labels to show requested, actual, and variance quantities clearly.
- Restricted ticket assignment choices to users authorized to fulfill tickets.
- Added warehouse-host alias coverage for assignment acknowledgements.

## Data and migration safety

- Migration `inventory.0050_pick_quantity_signoff_acknowledgement` backfills historical completed tickets as exact picks where no documented variance exists.
- Existing reservation transactions are linked to their exact ticket lines.
- Existing open assignments are preserved.
- The migration is reversible; isolated rollback testing restored the pre-feature stock balance and original reservation ledger.
- A production backup was created before migration.

## Verification

- Full automated gate: **581 passed, 2 skipped, 55 subtests passed**.
- Django system check: no issues.
- Migration drift check: no changes detected.
- Python compilation, JavaScript syntax checks, and `git diff --check`: passed.
- Independent inventory/security review: no blocker, high, or medium findings.
- Production migration and static collection completed successfully.
- Authenticated dashboard, ticket list, transaction history, request portal, and an open ticket detail returned HTTP 200.
- The live ticket detail displayed picked-quantity, variance-reason, and QA controls.
- Existing open assignment synchronization reported zero mismatches.
- Production browser console reported zero JavaScript errors.
- `ppe-inventory.service` remained active with zero unexpected restarts.
