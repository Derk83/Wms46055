# WMS Reported UI Regression Fixes — 2026-09-14

**Released:** 2026-09-14 09:44 CDT
**Application commit:** `4bf66a1`
**Production sites:** `https://bbx.rplwms.com`, `https://requests.rplwms.com`

## Summary

This release resolves the four UI and workflow regressions reported after the full WMS UI audit. The established WMS layout was preserved; changes were limited to restoring operational controls, repairing the request-access splash, and improving light/dark readability.

## Dashboard Quick Actions

Restored the complete permission-aware seven-action set:

1. Add Item
2. Resume Work
3. Receive Stock
4. Bulk Adjust
5. Transaction History
6. Print Labels
7. Phone Scanner

Each action continues to appear only when the signed-in user has the required permission.

## Pick-ticket deletion

- Restored the **Delete Ticket** action on ticket details.
- Unlinked tickets can again be deleted by users with `delete_pickticket`.
- For a ticket linked to a material request, the confirmation page identifies the linked request and explains that both records will be removed.
- Linked deletion requires all three relevant permissions: `delete_pickticket`, `delete_materialrequest`, and `delete_materialrequestline`.
- Ticket/link detection and deletion run in one atomic transaction with database locking.
- Linked deletion uses the existing material-request deletion service so ticket quantities are restored to inventory consistently and the deletion audit event is retained.

No production ticket was deleted during release verification.

## Public access-request splash

The initial public form on `requests.rplwms.com` now asks only for:

- Full name
- Position
- Exact `@blackbox.com` email
- Contact number
- Company/department

Removed from the initial splash form:

- Project/jobsite
- Supervisor/sponsor
- Business reason

The existing database fields and historical records were not removed. The page now explicitly uses its intended light theme, and **Already approved? Sign in** is displayed as a prominent 40-pixel secondary button.

## Theme and contrast improvements

- Added separate semantic text-link colors for light and dark themes instead of reusing button red for small text.
- Strengthened form-control borders in both themes.
- Improved dashboard metric and status colors.
- Added readable light-theme colors for all Settings role badges.
- Improved Settings active-tab and Administrator badge contrast.
- Corrected Bulk Adjust part-number, quantity-delta, and pending-summary colors.
- Corrected the notification-count badge background.
- Kept warning-button hover text readable.

Measured rendered contrast included:

| Surface | Light | Dark |
|---|---:|---:|
| Settings role badges | 4.71–5.88:1 | 5.67–8.18:1 |
| Administrator badge | 6.00:1 | 5.97:1 |
| Bulk Adjust part number | 5.81:1 | 4.96:1 |
| Notification counter | 6.57:1 | 6.57:1 |
| Public splash card text | 17.94:1 | N/A |
| Public splash supporting text | 7.04:1 | N/A |
| Public splash sign-in button | 6.26:1 | N/A |

All checked normal text meets or exceeds the WCAG 4.5:1 target.

## Quality gate

- **475 tests passed**
- **42 subtests passed**
- Django system check passed
- No migration drift
- Python compilation passed
- JavaScript syntax checks passed
- Static security scan passed
- Git diff/whitespace checks passed
- Independent post-fix review: safe to deploy, no blocker/high/medium findings

The only test-suite warning was an existing Django 7 deprecation notice in the weekly-report email command; it is unrelated to this release.

## Deployment and live verification

- No database migrations were required.
- Two changed static files were collected successfully; 141 were unchanged.
- Gunicorn received a graceful HUP and `ppe-inventory.service` remained active with zero restarts.
- Local and `origin/master` matched at application commit `4bf66a1` before this documentation commit.
- `https://bbx.rplwms.com/accounts/login/` returned HTTP 200.
- `https://requests.rplwms.com/` returned HTTP 200.
- Live public splash displayed only the five retained fields and the prominent sign-in control.
- Live authenticated Dashboard under Derek's unchanged account displayed all seven Quick Actions.
- Live unlinked ticket detail displayed **Delete Ticket**.
- Production currently had no linked material-request records available for a non-destructive browser confirmation; linked behavior and permission denial were verified by automated tests.

## Hermes default model

Hermes configuration was explicitly set and validated separately from the WMS release:

- Provider: `openai-codex`
- Default model: `gpt-5.6-sol`
- Configuration version 30 passed `hermes config check`.
