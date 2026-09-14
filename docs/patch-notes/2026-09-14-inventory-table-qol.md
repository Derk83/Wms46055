# Inventory Table QoL Improvements

**Release date:** September 14, 2026

Improved the main inventory list for faster daily use:

- Added a dedicated partial-match **FB Part #** filter.
- Added **FB Part # A–Z** and **FB Part # Z–A** sorting.
- Made desktop inventory table headers remain visible while scrolling.
- Made desktop rows and mobile inventory cards open item details when their non-interactive area is selected.
- Preserved normal behavior for checkboxes, links, buttons, form controls, action chips, and text selection.

## Focused verification

- Five focused inventory and portal tests passed.
- Django system check passed.
- Rendered JavaScript syntax passed.
- Production dataset filter check returned only the expected FB Part # match.
