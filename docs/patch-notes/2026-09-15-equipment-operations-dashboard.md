# Equipment operations dashboard

**Released:** September 15, 2026  
**Portal:** [equipment.rplwms.com](https://equipment.rplwms.com)

## Overview

The equipment landing page is now a detailed operational control center rather than a compact summary. It retains the established RPL Equipment visual system while surfacing live custody, scheduling, maintenance, rental, audit, and data-quality work.

## Dashboard additions

- Expanded linked status totals for all assets, availability, active custody, overdue custody, reservations, maintenance, review exceptions, and rental returns.
- A prominent **Needs attention** queue for overdue custody, lost assets, imported records requiring review, critical or overdue maintenance, and upcoming or overdue rental returns.
- Detailed **Active custody** and **Upcoming reservations** tables.
- A 20-row equipment register preview with asset identity, category, status, custodian, location, condition, next obligation, and last update.
- Maintenance and rental operational queues.
- Category-level capacity showing total, available, checked out, reserved, service, and effective availability.
- Recent immutable equipment activity for authorized audit users.
- Permission-aware quick actions and dashboard sections.
- Responsive mobile table cards and verified light/dark presentation.

## Operational safeguards

- Self-service reservation users can see only reservations linked to their own equipment-party record; managers retain the global operational view.
- Attention totals are calculated from complete querysets rather than displayed samples.
- Lost assets are explicitly escalated in the attention queue.
- Rental return counts use open rental lines, honor per-asset return dates with contract-end fallback, exclude returned assets, and prioritize the true effective due date before limiting the queue.
- Null maintenance and rental deadlines sort after dated obligations.

## Verification

- Equipment regression suite: **32 passed**.
- Full release gate: **552 passed**, plus **45 Django subtests**.
- Django system check: no issues.
- Migration drift: none.
- Independent review completed; all blocker/high/medium findings resolved.
- Rendered desktop and mobile checks completed in light and dark themes with no page overflow or console errors.
- Production authenticated dashboard returned HTTP `200` with all new panels present.
- Updated equipment stylesheet returned HTTP `200` and contained the release marker.
- `bbx.rplwms.com` and `requests.rplwms.com` remained healthy after deployment.
- `ppe-inventory.service` active with zero unexpected restarts.

## Release

- Application commit: `b79a7f6`
- Pre-deployment backup: `backups/wms-backup-20260915-232558.tar.gz`
