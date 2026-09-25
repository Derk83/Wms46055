# Public no-login training and grouped Training Center

**Released:** 2026-09-25
**Application commit:** `b9e0a45763deb0c9d5a3934999b1f196d70796bb`

## Summary

- Moved all Training Center destinations to anonymous, demo-host-only routes on `demo.rplwms.com`.
- Added public sequential training for eight Warehouse modules and six Equipment modules.
- Added public five-task guides for Material Requests and Equipment Requests.
- Exposed the fictional Guided Inventory Demo on the demo host without requiring a production account.
- Grouped the Training Center into practice demos, requester workflows, Warehouse operations/controls, and Equipment lifecycle/planning sections.
- Added compact two-column desktop cards with clean single-column mobile behavior in light and dark themes.

## Security and data isolation

- Existing Warehouse, Equipment, Material Request, and Equipment Request application routes retain their authentication, host, and permission requirements.
- Public training routes exist only in the dedicated demo-host URL configuration and return `404` on operational hosts.
- Public training views import no operational models and expose no live-workspace actions, event feeds, notifications, push hooks, or service-worker registration.
- Anonymous GET requests do not create session rows. A session is created only when a learner submits progress.
- Task progress and fictional-demo state remain browser-session-only.
- Completion notes are validated but not retained.
- The original `rplwms.com` application links and canonical `www` redirect remain unchanged.

## Verification

- Full release gate: **751 passed, 61 subtests passed, 2 skipped**.
- Django system check, migration drift check, Python compilation, JavaScript syntax, and diff checks passed.
- Independent security/UI re-review found no blocker, high, or medium findings.
- Rendered 20 desktop/mobile, light/dark cases with zero overflow, zero browser errors, and minimum measured body-text contrast of 16.29:1.
- Live HTTPS verification opened all 17 Training Center destinations without login.
- Live CSRF-protected task progression advanced Warehouse Receiving, the Material Request guide, and the fictional Inventory Demo.
- Every Inventory and Equipment model count was identical before and after the live walkthrough.
- The temporary verification session was deleted, and the demo-host session cookie was not sent to the Warehouse host.
- `ppe-inventory.service` remained active with `NRestarts=0`.

## Rollback

Revert application commit `b9e0a45763deb0c9d5a3934999b1f196d70796bb`, collect static files, and reload `ppe-inventory.service`. No schema migration or production-data rollback is required.
