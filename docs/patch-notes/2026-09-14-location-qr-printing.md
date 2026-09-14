# Location QR Printing Fix

**Release date:** September 14, 2026

Corrected section-level location QR printing:

- Six bin QR labels now remain in the intended 2-column × 3-row layout on one Letter portrait page.
- The narrow-screen preview rule is now limited to screen display and can no longer override print pagination.
- Replaced the CSP-blocked inline Print button action with the approved external application JavaScript.

## Focused verification

- Single-section six-bin pagination test passed.
- Multiple-section pagination test passed.
- A-01 rendered as six labels on one physical 612 × 792 point Letter PDF page.
- JavaScript syntax and Django system checks passed.
