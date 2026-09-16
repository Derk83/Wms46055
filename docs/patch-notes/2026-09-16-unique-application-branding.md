# Unique application branding

**Released:** September 16, 2026
**Portals:** [bbx.rplwms.com](https://bbx.rplwms.com), [requests.rplwms.com](https://requests.rplwms.com), and [equipment.rplwms.com](https://equipment.rplwms.com)

## Overview

Each RPL application now has a distinct, lightweight identity while retaining one consistent visual family. The marks use open geometry, centered symbols, and theme-aware monochrome styling.

## Application identities

- **Warehouse:** a centered pallet-and-carton mark.
- **Material Requests:** a centered request checklist mark.
- **Equipment:** a centered toolbox mark.
- App names remain visible on larger layouts and collapse cleanly on small mobile headers.
- The Material Requests identity is used on both authenticated and public access/login surfaces.

## App and device integration

- Added app-specific SVG favicons.
- Added dedicated 192px, 512px, maskable, and Apple touch icons for each application.
- Updated Warehouse, Material Requests, and Equipment PWA manifests to publish the correct identity.
- Updated Warehouse and Material Requests service-worker notification icons.
- Added an Equipment service worker, offline shell, and cache-safe content-versioned CSS/JavaScript URLs.

## Verification

- Full release gate: **553 tests passed**, plus **45 Django subtests**.
- Final focused verification: **91 tests passed**, plus **6 Django subtests**; Equipment follow-up: **33 tests passed**.
- Django system check: no issues.
- Migration drift: none.
- Python compilation, JavaScript syntax checks, and `git diff --check`: clean.
- Independent review completed; all blocker/high/medium findings resolved.
- Rendered QA completed for all three apps on desktop and mobile in light and dark themes.
- Production authenticated matrix: **12/12 combinations returned HTTP 200** with 44×44 centered marks, correct theme colors, correct app labels, no overflow, and zero unexpected application console errors.
- All three production manifests and representative SVG/PNG assets returned HTTP `200` with the correct app-specific paths.
- Equipment service worker and offline shell returned HTTP `200`; the rendered worker passed JavaScript syntax validation.
- `ppe-inventory.service` active with zero unexpected restarts.

## Release

- Branding commit: `17f2078`
- Static cache follow-up: `31f5aac`
- Pre-deployment backup: `backups/wms-backup-20260916-111432.tar.gz`
