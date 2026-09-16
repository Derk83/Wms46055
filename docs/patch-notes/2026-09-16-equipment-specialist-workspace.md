# Equipment Specialist Workspace

**Released:** September 16, 2026
**Production commit:** `e9774a4`

## Summary

The Equipment application now uses a purpose-built specialist workspace designed for daily asset accountability rather than a compact generic dashboard.

## Improvements

- Added a persistent desktop sidebar organized around Equipment, Operations, Insights, and Management workflows.
- Added an accessible off-canvas navigation drawer for tablets and phones.
- Added a compact utility bar with global equipment search, install, theme, and account controls.
- Expanded the dashboard with operational KPIs for tracked, available, checked-out, attention, and rental-return equipment.
- Added live equipment search by tag, serial, model, custodian, location, status, and category.
- Added specialist queues for attention items, activity, custody, reservations, maintenance, and rental obligations.
- Added category utilization, availability, and permission-protected rental cost intelligence.
- Preserved existing Equipment permissions and hid sensitive rental costs unless the user has both rental visibility and asset-cost access.
- Improved active-page announcements, keyboard focus containment, Escape handling, and hidden-drawer accessibility.
- Prevented stale live-search responses and restored the recent-equipment preview when search criteria are cleared.
- Verified light and dark themes with the populated 336-asset production dataset copy.

## Validation

- Full test gate: **556 passed**, **45 subtests passed**.
- Equipment suite: **36 passed** after review fixes.
- Django system check: no issues.
- Migration check: no changes detected.
- JavaScript syntax, Python compilation, and Git whitespace checks passed.
- Independent review: no blocker or high-severity findings; medium findings resolved before release.
- Production HTTPS, authenticated dashboard rendering, browser console, and static-file hashes verified.
- `ppe-inventory.service` active with zero unexpected restarts.
