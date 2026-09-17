# Equipment Dashboard Cleanup

**Released:** September 16, 2026
**Production commit:** `1a1aadf`

## Summary

The Equipment Specialist dashboard now uses a cleaner operational hierarchy while preserving the complete tracking, custody, reservation, maintenance, rental, reporting, import, and audit workflows.

## Changes

- Consolidated the five status metrics into a lighter unified summary strip on desktop.
- Reduced the default live-register preview to six recent records, with three visible on mobile and the full register one action away.
- Simplified the live register to the five most useful operational columns.
- Reworked the priority queue as an Action Center with an explicit sampled-versus-total count.
- Reduced recent activity to the three latest audited events.
- Added focused utilization and tight-capacity summaries while retaining complete category capacity in an expandable table.
- Surfaced categories that have no operational assets.
- Reduced heavy borders, shadows, spacing, and repeated card weight throughout the workspace.
- Preserved complete detailed custody, reservation, maintenance, rental, and audit sections.
- Corrected responsive behavior at the 760/761-pixel table-to-card transition.
- Preserved light/dark themes, permissions, search behavior, navigation, and existing routes.

## Validation

- Full suite: 555 passed, 2 skipped, 45 subtests passed.
- Equipment-focused suite after review fixes: 35 passed, 2 skipped.
- Django system check and migration drift check passed.
- Populated browser QA passed at desktop, mobile, and key responsive breakpoints in light and dark themes.
- No horizontal overflow or browser console errors were detected.
- Independent review found no blocker or high-severity issues; all medium findings were resolved before release.
- Production authenticated dashboard returned HTTP 200 and collected static hashes matched source.
