# Manager equipment request deletion

**Released:** September 18, 2026
**Applications:** Equipment Manager and Equipment Requests

## Added

- Equipment managers can permanently delete an equipment request from its manager detail page.
- Deletion requires entering the exact request number and submitting a CSRF-protected confirmation form.
- Requesters and other users without `equipment.manage_equipment_requests` cannot use the deletion endpoint.

## Audit and custody safety

- Every deletion creates an immutable tombstone containing the complete request, item lines, allocation records, activity events, assignment/cancellation details, and linked reservation/asset state.
- Pending or approved linked reservations are cancelled before deletion and reserved assets are released.
- Reservation release writes immutable asset-history events and records each asset's status before and after the operation.
- Fulfilled reservations, checkout records, active custody, and current checked-out asset state are preserved.
- The entire operation is atomic; a failure restores the request, reservation, asset state, events, and deletion record together.

## Verification

- Full release gate: 650 tests passed, 2 skipped, and 55 subtests passed.
- Django checks, migration drift, Python compilation, JavaScript syntax, and diff checks passed.
- Independent security/workflow review passed with no blocker, high, medium, or low findings.
- Populated rendered browser QA confirmed the manager detail and deletion confirmation pages without overflow or console errors.
- Production migration `equipment.0009_equipmentrequestdeletion` applied successfully.
- Read-only production verification confirmed the live manager detail and confirmation pages, exact request-number prompt, destructive label, and CSRF control without deleting a production request.
