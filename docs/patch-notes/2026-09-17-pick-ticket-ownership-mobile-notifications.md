# Pick ticket ownership, notifications, and mobile cleanup

**Released:** September 17, 2026

**Application:** RPL Warehouse WMS

## What changed

- The first logistics specialist to accept a material request is now recorded as both the request/ticket assignee and the ticket's picker.
- Accepted pick tickets are locked to that specialist. Later reassignment requires a manager and is recorded in the material-request event history.
- Manager reassignment clears the prior acknowledgement so the newly assigned specialist must acknowledge the work before picking.
- Existing open tickets with a stored ticket assignee are safely backfilled into the locked-picker field.
- Logistics managers, senior logistics managers, procurement managers, and superusers no longer receive first-claim popups or actionable first-claim push alerts.
- New actionable claim alerts request system sound and vibration. Foreground WMS alerts also provide an audible chime and supported-device vibration after browser audio has been enabled by user interaction.
- Pick-ticket screens now show FB Part # and bin location, including the print layout.

## Mobile interface cleanup

- Reduced the pick-ticket title/action area to a compact header.
- Replaced tall, wrapping action buttons with short single-line actions.
- Converted ticket metadata into a two-column at-a-glance summary.
- Moved secondary audit fields under **More ticket details** on mobile.
- Rendered pick lines as responsive cards without horizontal scrolling.
- Preserved accessible table headers for assistive technology.
- Verified light and dark themes at a 390 px mobile viewport with no clipping or horizontal overflow.

## Safety and verification

- Full automated gate: **594 passed, 2 skipped, 55 subtests passed**.
- Django system check passed.
- No ungenerated model changes were detected.
- Data migration was exercised forward, backward, and forward again on an isolated database.
- Independent permission, ownership, notification, and responsive-UI review completed.
- Live authenticated ticket detail returned HTTP 200 using rollback-only temporary verification data.
- `bbx.rplwms.com` and `requests.rplwms.com` verified over HTTPS with no browser console errors.
- Production service active with zero unexpected restarts.
