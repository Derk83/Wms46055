# Fictional training workflow tabs — 2026-09-26

## Changes
- The public guided inventory demo places each task's form and action within its matching fictional Inventory, Receiving, Requests, Pick Tickets, or Audit tab. The guide panel is instructions and progress only.
- Public warehouse/equipment modules and the material-request guide now show one selected fictional practice tab at a time. Finished tabs can be reviewed read-only, future tabs remain locked, and each current exercise runs inside its selected workspace tab rather than the task list or reference sidebar.
- Tab navigation cannot advance completion or access locked exercises. Training records remain session-scoped and separate from operational inventory, requests, and equipment.

## Verification
- Full pytest gate: 794 passed, 2 skipped; Django system check, migrations check, Python compilation, JavaScript syntax checks, and diff check passed.
- Independent read-only review found no security or permission blockers; corrected its wording finding.
- Live public demo: fictional Requests panel contains the request form; an empty submission returns HTTP 400 without progressing. Public warehouse Receiving course renders one active panel with no completion form in its reference sidebar. Checked light/dark computed styles and page resources.
- Deployment: migration check reported no pending migrations; static files collected, application HUP reload, service active with no unexpected restarts.
