# Patch Notes — 2026-09-13 — Nav: refined pills (V1)

## What changed
The site nav (top header on desktop, slide-out on mobile/iPad) was upgraded
to **V1 — Refined Pills** from the three-variant sketch set approved earlier
today. Behaviour is identical (same 4 sections, same permission gates, same
mobile breakpoint at 1120px, same `<a>` links); only the visuals and the
hamburger animation are upgraded.

## Desktop nav
- **Type:** 14px / weight 650 (was 13px / weight 700)
- **Padding:** 9px × 13px (was 8px × 10px)
- **Border radius:** 10px (was 8px)
- **Hover:** 8% accent-tinted background (was solid surface-2); icon goes
  from 75% opacity to full accent color
- **Active section:** inset box-shadow with a 2px accent bottom line +
  accent-tinted background. Triggers on `.is-current` class or
  `aria-current="page"` attribute

## Section labels
All four section labels are now visible on desktop (Insights, Tools, Admin),
not just on mobile. The Operations section stays unlabelled (it's the
default section, no label is the original design intent).

## Section dividers
1px borders at 60% opacity (was 100%). Reads as a deliberate separator
between sections rather than an accidental border.

## Icons
Every nav link has a 16px glyph in a muted color that turns accent on hover:
- Operations: ▦ Inventory · ▤ Tickets · ◫ Material Requests · ↓ Receiving
- Insights: ↻ Transactions · ⌘ Reports
- Tools: ◉ Locations · ⌗ Scanner · ⊞ Cycle Count · ⤓ Cycle Count Archive
- Admin: ⚙ Settings

Glyphs use Unicode geometric symbols — no icon font, no extra HTTP request,
no FOUC. Renders consistently across Safari/iPad, Chrome, and Firefox.

## Hamburger animation
- 18px wide (was 17px) with 4px gap between bars (was 3px)
- Transform-origin: center
- Smoother easing: `.22s ease` (was `.18s ease`)
- Bars translate 6px on open (was 5px) for cleaner alignment to the X

## Mobile / iPad slide-out
- 380px wide (was 370px) — slightly more breathing room
- Buttons are no longer framed like form inputs (removed 1px border, made
  background transparent). They look like list items, not fields.
- Active section uses a 3px accent left stripe + accent-tinted background
  (was full border accent on all four sides)
- Icon-aware: 18px icon next to each label, turns accent on hover
- Mobile-nav head subtitle now visible ("Warehouse navigation")

## Brand block
The brand text in the header is now wrapped in a `.brand-text` container so
the subtitle ("Material Requests" on the request portal) can be hidden on
mobile without affecting the logo or icon glyphs. On desktop the
single-line "RPL Warehouse" still appears next to the logo.

## Permission gating
Unchanged. All links still gated on `{% if perms.… %}`. Section labels are
cosmetic — they show even if the section is empty (matches existing
behaviour for the "Tools" section which renders even with one item).

## Files touched
- `inventory/static/inventory/css/app.css` — `.site-nav`, `.nav-section`,
  `.nav-section-label`, `.site-nav a`, `.site-nav a .ico`, `.nav-toggle-icon`,
  mobile media-query rules for `.site-nav`, `.site-nav>a`, `.mobile-nav-head`,
  `.brand` span rule refactored to `.brand-text` / `.brand-primary` /
  `.brand-subtitle`
- `inventory/templates/inventory/base.html` — wrapped brand text in
  `.brand-text`, added `.ico` spans to all 10 nav links

## Compatibility
- No JS changes. No view changes. No URL changes. No permission changes.
- Same 1120px mobile breakpoint. Same 600px small-mobile breakpoint.
- Same focus rings (uses the existing `--focus` token).
- Print stylesheet unchanged (already hides `.site-header` + `.nav-toggle`).
- Light theme tokens (`--accent-hover: #a90f19`, etc.) inherit the same
  refined look automatically.

## Verification
- `manage.py check` passes
- 91 tests pass across views, reports, material requests, PDF viewer
- Live CSS served at https://bbx.rplwms.com/static/inventory/css/app.css
  contains the new `.site-nav a .ico` rule (verified with curl)
- All 11 icon glyphs render in the dashboard HTML (verified by rendering
  the view with the Django test client)
- All 4 nav sections + 3 section labels render correctly

## Not in scope (deferred)
- V2 (segmented rail) and V3 (vertical sidecar) are available as
  pre-built HTML mockups in `.hermes-sketches/` if we want to revisit the
  pattern later when the section count grows
- Per-template `aria-current="page"` setting (the CSS is ready for it,
  but the templates don't set it yet — easy follow-up if you want the
  active-link indicator)
- The WMS UI conformance audit (60+ templates) — separate cleanup pass
  per the 2026-09-13 agreement

## Commit
`7f021e1` — `Nav: refined pills (V1) — bigger type, section dividers,
icons, accent underline`
