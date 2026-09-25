# Centralized demo landing page

**Released:** 2026-09-24

## Summary

Added a dedicated, public, read-only demo launcher at `https://demo.rplwms.com/`. The launcher centralizes currently available guided demonstrations while keeping every operational application, login flow, and permission boundary separate.

## Available demo

- **Guided Inventory Demo** — links to the existing authenticated walkthrough at `https://bbx.rplwms.com/demo/`.
- Uses six fictional inventory items and five guided steps covering receiving, material requests, picking, and audit review.
- The demo remains session-isolated and performs no production inventory writes.

## Navigation and UI

- Warehouse desktop and mobile **Tools → Guided Demo** links now open the centralized launcher.
- Reuses the established RPL application-launcher visual system.
- Supports responsive desktop/mobile layouts and light/dark themes.
- Static assets are content-versioned for immediate cache refresh.

## Security and isolation

- The launcher is public, read-only, and performs zero database queries.
- It exposes only the root landing route.
- Operational paths including `/admin/`, `/inventory/`, `/demo/`, and `/demo/action/` return `404` on the demo hostname.
- The hostname is not added to `CSRF_TRUSTED_ORIGINS`.
- Existing application authentication and authorization boundaries remain unchanged.

## Infrastructure

- Added a zone-scoped Cloudflare DNS record for `demo.rplwms.com`.
- Added Nginx Proxy Manager host routing to the existing WMS backend.
- Issued and attached a dedicated Let's Encrypt certificate using Cloudflare DNS validation.
- Enabled forced HTTPS, HTTP/2, and HSTS.

## Verification

- Full release gate: **671 tests passed**, **61 subtests passed**, **2 skipped**.
- Independent host-isolation and UI review passed after resolving all findings.
- Public HTTPS landing page returned `200`.
- Versioned demo stylesheet returned `200`.
- Browser console contained no JavaScript errors or warnings.
- Light and dark themes rendered without horizontal overflow.
- Operational demo-host paths returned `404`.
- Production service remained active with `NRestarts=0`.
- Local `HEAD` matched `origin/master` at feature commit `8766396` before this release-note commit.

## User action

The temporary Cloudflare API token was posted in chat and must be revoked from **Cloudflare → My Profile → API Tokens** after deployment.
