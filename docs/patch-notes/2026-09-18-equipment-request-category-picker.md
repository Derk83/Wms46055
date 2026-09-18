# Equipment request category item picker

**Released:** September 18, 2026
**Application:** Equipment Requests (`eqreq.rplwms.com`)

## Changes

- Added an optional **Preferred item** dropdown beside each equipment category on request lines.
- Loads only assets from the selected category through an authenticated, category-scoped endpoint.
- Shows the asset name, asset tag, and current status.
- Keeps checked-out and otherwise operationally unavailable assets visible, but disables and greys them out.
- Excludes archived and terminal-state assets from requester choices.
- Preserves the preferred item on request details and in manager review.
- Records preferred-item changes in the immutable request audit metadata.
- Revalidates category, availability, archive state, and duplicate selections inside the serialized equipment mutation transaction.

## Verification

- Full release suite: **639 passed, 2 skipped, 55 subtests passed**.
- Django system checks, migration drift check, Python compilation, JavaScript syntax, and diff checks passed.
- Populated browser QA passed at 390px mobile and 1280px desktop in light/dark themes with no horizontal overflow.
- Production authenticated form and category lookup returned HTTP 200.
- Public Equipment Requests JavaScript and CSS SHA-256 hashes match the deployed source.
- `ppe-inventory.service` is active with zero unexpected restarts.
