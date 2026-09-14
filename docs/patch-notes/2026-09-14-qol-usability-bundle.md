# WMS QoL Usability Bundle

**Released:** September 14, 2026
**Application commit:** `b760f1d`

## Improvements

- Added inventory quick filters for low stock, zero stock, missing location, missing FB Part #, missing model, and recently updated items.
- Added matching-result counts, active-filter summaries, filter-aware empty-state guidance, and selectable rows per page.
- Added remembered inventory sort, row-count, visible-column, URL, and scroll preferences.
- Added safe originating-list return links and Previous/Next item navigation.
- Added copy controls for common inventory identifiers and storage locations.
- Added keyboard search shortcuts, duplicate-submit protection, loading feedback, safer delete confirmation, and browser back/forward recovery.
- Increased touch targets and consolidated secondary inventory actions on mobile.
- Standardized date/time presentation across key warehouse workflows without changing timezone behavior.
- Removed the redundant Labels & QR entry from the desktop More menu while retaining Locations and the mobile QR tools entry.

## Safety and verification

- Inventory quantities, ledger records, permissions, passwords, and production business records were not changed by this release.
- Full gate: 490 tests and 45 subtests passed.
- Focused post-review regression gate: 15 tests passed.
- Django checks, migration drift check, Python compilation, JavaScript syntax, and Git diff checks passed.
- Independent review findings were resolved before deployment.
- Both production hosts passed HTTPS checks; protected inventory query strings were preserved through login redirects.
- Production service remained active with zero unexpected restarts.
- Collected CSS and JavaScript hashes match their source files; live request-portal browser console reported zero errors.
