# Option A mobile navigation and PDF exit

**Date:** 2026-09-13

## Summary

Implemented the user-approved Option A mobile interface while preserving the established WMS visual system.

## Changes

- Rebuilt the mobile hamburger drawer as clean, full-width 50px navigation rows.
- Added restrained Operations, Insights, Tools, and Admin section dividers.
- Corrected the mobile selector so it targets the real links nested inside `.nav-section`.
- Scoped the sectioned drawer spacing to the main WMS so the request portal retains its existing layout.
- Added a permanently visible `Exit PDF` control above the embedded daily report.
- Preserved the report preset/date when exiting back to the report screen.
- Kept Download and Print as separate actions and removed the unnecessary new-tab action.
- Added regression coverage for the mobile navigation structure and PDF escape controls.
- Corrected weekly-report tests to calculate expected dates in the command's configured `America/Chicago` timezone.

## Visual approval

The actual production templates and CSS were rendered at a 1024px iPad-class viewport. The user approved both the open hamburger drawer and embedded PDF viewer renders before deployment.

## Verification

- Full Django test suite
- Django system check
- Migration drift check
- CSS/diff whitespace checks
- Authenticated iPad-class browser render
- Live authenticated smoke test after deployment
