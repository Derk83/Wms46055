# Equipment Asset Management Portal

**Release date:** September 15, 2026

**Application:** RPL Warehouse Management System

**Target host:** `equipment.rplwms.com`

## Summary

Added a dedicated equipment and serialized-asset management portal that shares WMS authentication while enforcing equipment-specific permissions and workflows.

## Included

- Searchable equipment register with asset identifiers, condition, ownership, location, assignment, components, costs, attachments, and immutable history.
- Atomic checkout and return workflows with active-custody projections and permanent event records.
- Reservation workflow with real approved holds, cancellation release, and reservation-linked checkout fulfillment.
- Maintenance work orders, out-of-service handling, rental contracts, due dates, and cost controls.
- QR labels, scanner lookup, operational reports, CSV export, import review center, and responsive light/dark interfaces.
- Equipment Manager, Equipment Technician, Equipment User, and Equipment Auditor roles provisioned after migrations.
- Permission boundaries for costs, audits, imports, exports, operations, and ordinary-user reservation ownership.
- SQLite mutation serialization, optimistic registry locking, immutable workflow records, and ambiguity-safe scanning.

## Workbook reconciliation

Imported `Equipment & Asset Tracker RPL.xlsx` as one idempotent staged batch.

| Measure | Result |
|---|---:|
| Assets created | 336 |
| Checked out / active custody | 273 |
| Source rows requiring review | 14 |
| iPads | 2 |
| Import batches | 1 |

The import preserves source provenance, reconstructs baseline custody history, excludes demonstration/access records from the equipment register, and stages exceptions for review rather than silently discarding them. A repeated import correctly reports the existing batch and creates no duplicates.

## Verification

- Full release gate: **547 tests passed**, **45 Django subtests passed**.
- Final focused equipment gate: **28 tests passed**.
- Django system check: no issues.
- Migration drift: none.
- Independent security and data-integrity review completed; all blocker, high, and medium findings resolved.
- Production migrations `equipment.0001` through `equipment.0003` applied successfully.
- Production service active with zero unexpected restarts.
- Authenticated checks returned HTTP 200 for dashboard, register, asset detail, reservations, maintenance, rentals, history, reports, imports, and scanner.
- Current equipment static CSS returned HTTP 200 from the production Nginx edge.

## External activation dependency

Application code, schema, production data, service, and local edge routing are deployed. Public access remains unavailable until `equipment.rplwms.com` receives a DNS record for `70.233.106.108` and a matching Nginx Proxy Manager host/certificate is added on the existing edge appliance. No DNS or Nginx Proxy Manager credentials were available on the application host during deployment.
