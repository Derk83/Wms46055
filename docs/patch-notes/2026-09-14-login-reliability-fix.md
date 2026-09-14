# Login Reliability Fix

**Release date:** September 14, 2026

**Application:** RPL Warehouse WMS and Material Requests portal

## Summary

Improved account login and administrator password-reset reliability after an active Procurement Manager account could not sign in following a reset.

## Changes

- Warehouse and request-portal login forms now accept:
  - The canonical username
  - Username variations in letter case
  - Accidental leading or trailing spaces around the username
  - A unique account email address, regardless of letter case
- Ambiguous duplicate email addresses are not accepted as login identifiers.
- Manager-entered password resets now require confirmation and Django password validation.
- A successful password reset atomically clears Axes failed-login counters for the account username and email.
- Delegated user managers cannot edit or reset superuser accounts.
- Non-superusers cannot grant staff or superuser status through the user editor.
- Login errors remain generic to prevent account enumeration.

## SWhitmore account repair

- Confirmed the account is active and has a usable password.
- Confirmed membership in the Procurement Manager group.
- Removed two failed-login counters associated with the username and account email.
- Did not change the account password, profile, group, or permissions.

## Verification

- 482 automated tests passed, plus 45 subtests.
- Targeted login, password-reset, Axes, permission, and navigation tests passed.
- Django system check passed.
- No migration changes were required.
- Both production login pages returned HTTP 200 with the updated form.
- SWhitmore's authenticated warehouse home returned HTTP 200 using Django `force_login`.
- Browser console reported zero errors.
- `ppe-inventory.service` remained active with zero unexpected restarts.

## Deployment

Application commit: `ba1ca4b`
