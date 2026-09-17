# Mobile workflow optimization

**Released:** 2026-09-17
**Application:** RPL Warehouse Management System

## Summary

Optimized high-traffic Warehouse and Requests workflows for phones and tablets while preserving existing desktop layouts and permissions.

## Changes

- Rebuilt mobile inventory cards into a compact 180px layout:
  - non-overlapping 24px selection control with a 40px hit area;
  - part number, description, and quantity in one header row;
  - FB Part #, bin, and category in one metadata strip;
  - Receive Stock and More actions in one balanced action row.
- Added responsive record layouts for transaction history, receiving logs, cycle counts, locations, reports, user settings, backorders, and request detail/archive tables.
- Improved mobile pick-ticket and receiving-ticket line editors with readable labels and full-width complex fields.
- Improved narrow-screen page headers, action groups, forms, dialogs, scanner containers, and summary layouts.
- Made inventory and ticket camera scan boxes adapt to the available viewport and relaxed the rear-camera constraint for broader mobile-browser compatibility.
- Preserved responsive table headings for assistive technology.
- Verified light and dark themes at 320px, 390px, and 430px widths with no horizontal overflow or inventory selection overlap.

## Validation

- Full test suite: **594 passed, 2 skipped, 55 subtests passed**.
- Django system check: no issues.
- Migration drift check: no changes detected.
- Python compilation, JavaScript syntax checks, and `git diff --check`: passed.
- Independent review: no blocker or high-severity findings; accessibility finding resolved before release.
- Production verification:
  - Warehouse, Requests, and Equipment roots return HTTP 200 after redirects.
  - Authenticated inventory route renders the compact mobile-card implementation.
  - Production responsive stylesheet is current.
  - Browser console has no JavaScript errors.
  - `ppe-inventory.service` is active with zero unexpected restarts.

## Release

- Application commit: `74ed1cb48828b5e7f5bfbf109241841420829963`
