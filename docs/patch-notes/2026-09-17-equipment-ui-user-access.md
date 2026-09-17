# Equipment UI and Cross-App User Access

**Released:** September 17, 2026
**Feature commit:** `eb0af3c`

## Summary

Polished the Equipment workspace at constrained desktop and mobile widths, removed duplicated utility controls, corrected the sidebar sign-out treatment, and made the existing centralized Warehouse user manager discoverable from all operational apps for authorized users.

## Equipment UI changes

- Changed the Equipment dashboard to give the live register full width at constrained desktop sizes, preventing status, location, and updated text from being clipped or fragmented.
- Removed duplicate top-bar Install and Theme controls.
- Retained one labeled Install/Theme control set in the permanent sidebar or mobile drawer.
- Replaced the inherited white Sign out button with an Equipment-specific dark sidebar treatment.
- Added **Users & access** to the Equipment Management section and account menu for users with `inventory.manage_users`.

## Cross-app user management

- Added a direct **Users & access** destination in Warehouse navigation and its account menu.
- Added the same authorized destination to Material Requests navigation.
- Equipment and Requests open the existing Warehouse user manager at `https://bbx.rplwms.com/settings/users/`.
- Existing `inventory.manage_users` authorization remains required; no role or permission was broadened.
- App login cookies remain independent. Cross-app links clearly indicate that Warehouse sign-in may be required.
- The existing user manager continues to provide the **Add User** workflow with group and permission assignment.

## Verification

- Focused gate: **49 passed, 2 skipped**.
- Full gate: **562 passed, 2 skipped, 55 subtests passed**.
- Django system check: no issues.
- Migration check: no changes detected.
- Python compilation, JavaScript syntax checks, and `git diff --check`: passed.
- Independent review: no blocker, high, or medium findings.
- Populated browser QA passed at 1440px, 1121px, and 390px with no horizontal overflow or console errors.
- Production authenticated rendered-response checks passed for Equipment, Requests, and Warehouse user administration.
- Production versioned Equipment CSS matches the deployed source SHA-256.
- Production service remained active with zero unexpected restarts.

## Test maintenance

Updated one material-request test to generate a future, quarter-hour-aligned UTC delivery slot instead of relying on a now-expired hard-coded September 17 timestamp. Production behavior was not changed.
