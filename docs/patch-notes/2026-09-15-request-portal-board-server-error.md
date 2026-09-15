# Request Portal Board Server Error Fix

**Released:** September 15, 2026
**Application commit:** `db21ab2`

## Fix

- Corrected an HTTP 500 on the request portal material-request board for users who have both request-portal access and warehouse-wide material-request visibility.
- The request host now always renders the host-safe requester board rather than selecting the warehouse queue from the user's additional permissions.
- Warehouse queue behavior on `bbx.rplwms.com` remains unchanged.

## Root cause

The warehouse queue links to pick-ticket routes that are intentionally unavailable in the isolated `requests.rplwms.com` URL configuration. A dual-role user's warehouse permission selected that queue on the request host, causing Django to raise `NoReverseMatch` while rendering the board.

## Validation

- Reproduced against the creator and data for the latest request, `MR-000021`.
- Patched-code production-data reproduction: HTTP 200 with requester board selected and warehouse queue excluded.
- Regression suite: 23 focused tests passed.
- Independent host-isolation review: 41 tests passed with no blocker, high, or medium findings.
- Full suite: 512 tests and 45 subtests passed.
- Django system, migration drift, Python compilation, JavaScript syntax, and diff checks passed.
- Production service active with zero unexpected restarts.
