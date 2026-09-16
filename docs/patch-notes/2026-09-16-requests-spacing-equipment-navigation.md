# Request board spacing and Equipment navigation

**Released:** September 16, 2026

## Material Requests

- Added a consistent vertical gutter between the status-column row and the empty-state card.
- Preserved the compact status columns and existing responsive board layout.

## Equipment

- Added an install control for the Equipment progressive web app.
- Uses the browser's native install prompt where available.
- Provides Share → Add to Home Screen guidance on iPhone and iPad.
- Updated the Equipment offline shell cache so existing devices retrieve the new install-enabled assets.
- Condensed the desktop header to the daily actions: Dashboard, Equipment, Scan, Custody, and Reservations.
- Grouped Maintenance, Rentals, Reports, History, and Imports under a structured More menu.
- Replaced the full username and sign-out row with a compact account menu.
- Moved install and theme controls into the mobile navigation drawer.
- Aligned Equipment's responsive navigation breakpoint with the Warehouse header at 1120px.

## Verification

- 34 focused regression tests passed.
- Django system checks and migration drift checks passed.
- Python compilation and JavaScript syntax checks passed.
- Request-board spacing was measured at 14px in isolated and production browser checks.
- Equipment navigation was exercised at 1280px, 1110px, and 390px with no horizontal overflow.
- Desktop More/account menus, mobile drawer scrolling, theme control, native install prompt, and iOS installation guidance were exercised.
- Production service remained active with zero unexpected restarts.
