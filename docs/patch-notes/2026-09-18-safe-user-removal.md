# Safe user removal with protected operational history

**Released:** September 18, 2026
**Application:** RPL Warehouse user management

## Fix

- Fixed the HTTP 500 raised when removing a user referenced by protected operational records.
- User accounts with inventory or equipment audit history are now retained as inactive identities instead of being hard deleted.
- Removing access immediately disables sign-in, assigns an unusable password, removes staff/superuser status, and clears group and direct permissions.
- Receiving, cycle-count, inventory-ledger, material-request, equipment, and other protected or nullable audit attribution remains linked to the original identity.
- Accounts without operational history can still be permanently deleted.
- Only a superuser can remove another superuser account, with authorization revalidated inside the transaction.
- Updated the confirmation page to explain whether the operation will remove access or permanently delete the account.

## Verification

- Reproduced the original `mobileqa` failure as a `ProtectedError` involving a cycle count and receiving ticket.
- Full release suite: **643 passed, 2 skipped, 55 subtests passed**.
- Independent security/workflow review passed with no blocker, high, or medium findings.
- Production read-only verification of the real account returned HTTP 200 and displayed both protected record types with a **Remove Access** action.
- All public application hosts responded normally.
- `ppe-inventory.service` is active with zero unexpected restarts.
