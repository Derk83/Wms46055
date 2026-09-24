# Isolated guided inventory demo

**Released:** 2026-09-24

## Summary

Added a guided WMS training module at `/demo/` with six fictional inventory items. The module demonstrates the core inventory workflow without creating or changing any production inventory, request, ticket, receiving, or ledger record.

## Guided workflow

1. Locate a low-stock fictional item and confirm its bin.
2. Receive 12 fictional boxes of nitrile work gloves.
3. Create a fictional request for three packs of cable labels.
4. Fulfill fictional pick ticket `DEMO-PT-001`.
5. Review the session-only audit trail and complete the demo.

## Isolation and safety

- All demo state is stored under a dedicated authenticated-session key.
- The module does not import or call production inventory models.
- Demo state is isolated per authenticated browser session.
- Restarting the demo restores all six fictional quantities and clears its fictional workflow activity.
- GET and POST endpoints enforce their HTTP methods.
- State-changing actions require CSRF protection and reject out-of-order steps.
- Warehouse-host checks prevent access through requester-facing application hosts.
- Malformed or stale demo session state resets safely instead of affecting production data.

## User interface

- Added **Guided Demo** under the WMS **Tools** navigation on desktop and mobile.
- Added a persistent fictional-data and live-inventory isolation notice.
- Added a five-step progress guide, highlighted current task, responsive fictional inventory table/cards, and final audit-trail view.
- Verified desktop light theme and mobile dark theme with no horizontal overflow or browser-console errors.
- New small text and semantic status colors meet WCAG 2.1 AA normal-text contrast.

## Verification

- Focused integration: **33 passed**.
- Full release gate: **661 passed, 2 skipped, 55 subtests passed**.
- Django system check: no issues.
- Migration check: no changes detected.
- Python compilation, JavaScript syntax checks, and `git diff --check`: passed.
- Independent review: no blocker/high findings; the medium navigation regression finding was resolved before release.
- Production walkthrough completed all five steps.
- Production table reconciliation after the walkthrough: no row-count changes across any `inventory_*` table.
- Deployed demo stylesheet matches the repository SHA-256 exactly.
- `ppe-inventory.service`: active with zero unexpected restarts.
