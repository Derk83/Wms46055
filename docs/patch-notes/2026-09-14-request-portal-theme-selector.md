# Request Portal Theme Selector Visibility

**Release date:** September 14, 2026

Corrected the theme selector on `requests.rplwms.com`:

- Moved the public-page selector to the rightmost position after the Sign in action.
- Pushed portal header controls to the far-right edge on desktop and mobile.
- Added an explicit dark button surface and visible light icon in both themes.
- Retained the existing theme-switching behavior and accessible label.

## Verification

- Focused portal regression test passed.
- Theme icon contrast is 15.24:1.
- Django system check passed.
