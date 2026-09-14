# Request Portal Reliability and Header Icon Fix

**Released:** September 14, 2026
**Application commits:** `dc64fbf`, `a4ebf8d`

## Fixes

- Prevented post-commit push-delivery failures from turning successful request actions into HTTP 500 responses.
- Added regression coverage for request creation, edit-page rendering, and board rendering after requester not-ready/reschedule responses.
- Replaced font-dependent install and theme characters with inline SVG icons.
- Applied explicit size, foreground, background, border, hover, and focus styling to request-portal header icon buttons.
- Updated both authenticated and public request-portal headers so the controls cannot fall back to white-square glyphs.

## Validation

- Full suite: 492 tests and 45 subtests passed.
- Focused portal workflows: 10 tests passed.
- Public portal icon regression: 9 tests passed.
- Django system, migration, Python, JavaScript, and diff checks passed.
- Live request portal: SVG present at 20 × 20 pixels inside a 40 × 40 high-contrast button.
- Live browser console: zero errors.
- Production service active with zero unexpected restarts.
