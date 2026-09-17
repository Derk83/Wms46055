# RPL WMS Application Hub

**Released:** September 16, 2026
**Application commits:** `76042b3`, `ade3315`

## Summary

`rplwms.com` is now the central entry point for the RPL WMS application family. The former root-domain redirect to Warehouse has been replaced with a responsive launcher for the three live applications.

## Applications

- Warehouse Operations: `https://bbx.rplwms.com`
- Material Requests: `https://requests.rplwms.com`
- Equipment Management: `https://equipment.rplwms.com`

Each destination retains its own login flow and permission enforcement. The public hub does not expose operational records or bypass application authorization.

## Changes

- Added a dedicated root-domain URL configuration isolated from Warehouse, Requests, and Equipment routes.
- Added a responsive desktop/mobile application launcher using the existing app-specific marks.
- Added persistent light and dark themes with accessible toggle semantics.
- Added full-card keyboard navigation, high-contrast focus indicators, reduced-motion support, and WCAG-compliant text contrast.
- Added exact destination and host-boundary regression coverage.
- Added safe `GET` and `HEAD` support for public monitoring.
- Canonicalized `www.rplwms.com` to `https://rplwms.com/`.
- Removed only the root-domain redirect directives from the existing Nginx Proxy Manager host; DNS, TLS certificate, backend, WebSocket, and security settings were preserved.

## Validation

- Full suite: 559 passed, 2 skipped, 45 subtests passed.
- Focused hub suite: 5 passed plus 10 host-isolation subtests.
- Independent review found no blocker or high-severity issues; all medium findings were resolved.
- Django checks, migration drift, Python compilation, JavaScript syntax, and diff checks passed.
- Desktop/mobile light/dark browser QA passed with no horizontal overflow or application console errors.
- Production root hub returned HTTP 200.
- Production `www` host returned the expected HTTP 301 canonical redirect.
- Warehouse, Requests, and Equipment remained operational with expected responses.
- Versioned production CSS and JavaScript returned HTTP 200 and matched collected-source SHA-256 hashes.
- Production service remained active with zero unexpected restarts.
