# WMS Patch Notes — 2026-09-13 — Receiving: bulk-receipt ticket grouping

**Date:** 2026-09-13
**Service:** `ppe-inventory.service` (Gunicorn, 3 workers on `127.0.0.1:8089`)
**Scope:** Audit item **#3** — bulk-receiving ticket grouping + double-count regression lock

---

## TL;DR

Bulk-receiving now groups spreadsheet rows into **one `ReceivingTicket` per PO/vendor/notes group** instead of one ticket per row. The double-count regression that the audit flagged is **already prevented** by the existing model-layer code path; new tests lock it down so it can never come back. No model or migration changes. Pure view-layer refactor.

---

## What changed

### Behavior change

| Scenario | Before | After |
|---|---|---|
| 10 rows, same PO, same vendor | **10 tickets, 1 line each** | **1 ticket, 10 lines** |
| 5 rows, PO-A + 3 rows, PO-B | **8 tickets** | **2 tickets** (one per PO) |
| 4 rows, no PO column at all | **4 tickets** | **1 ticket** (since all share empty PO + empty vendor + same notes) |
| One bad row in a 10-row batch | rolls back the whole batch, error_count = 10 | commits the 9 good rows, error_count = 1 |

### The double-count claim — already fixed

The audit said:

> `process_bulk_receiving()` creates a `ReceivingLine`; `ReceivingLine.save()` already increments stock and records a receipt. The same code then calls `InventoryTransaction.record_receipt()` again.

That is **no longer true** in the current code. Today's code calls `ReceivingLine.save()`, which is the **single source of truth** — it writes one RECEIPT ledger row and adjusts stock once. No caller double-calls.

`test_bulk_receiving_does_not_double_count_stock` (in the new test file) locks this down: Q=10 → `quantity_on_hand = 10`, exactly **one** RECEIPT row.

### Other improvements

- **Bad-row isolation** — each row is its own atomic block. A failing row in a batch no longer takes the whole batch down.
- **Spreadsheet header detection** — `PO` and `Vendor` columns now recognized (case-insensitive, also matches `Supplier`, `Purchase Order`, `PO #`). Old sheets without these still work; rows just share the empty bucket.
- **Error messages** — include the row index and the failing cell value, not just a quantity string.

---

## What did NOT change

- **Stock math** — same single-source-of-truth path. `ReceivingLine.save()` is still the only place that writes RECEIPT ledger rows and adjusts `quantity_on_hand`.
- **`ReceivingLine.delete()`** — still correctly emits a REVERSAL with `reverses_transaction=receipt`, validates item/quantity/reference match before deleting. Audit-grade immutability preserved.
- **Single-line receiving** — `receive_stock` view at `/inventory/<pk>/receive/` still calls `InventoryTransaction.record_receipt()` directly (no `ReceivingLine` involved) and is unchanged.
- **Schema / migrations** — none. This is a pure view-layer refactor.
- **API surface** — `process_bulk_receiving()` signature and return shape (`(success_count, error_count, errors)`) are unchanged.
- **Permissions** — same `inventory.receive_stock` gate at the view layer.

---

## Test coverage added

5 new tests in `inventory/test_audit_03_bulk_receiving.py`:

| Test | Locks down |
|---|---|
| `test_bulk_receiving_does_not_double_count_stock` | Audit's specific Q=10 → +20 claim |
| `test_bulk_receiving_multiple_rows_distinct_items_no_double_count` | Multiple items, each gets exactly one RECEIPT row |
| `test_bulk_receiving_groups_rows_by_po_into_one_ticket` | Same PO → one ticket, both lines under it |
| `test_bulk_receiving_separate_po_gets_separate_tickets` | Different POs → different tickets |
| `test_ticket_edit_notes_only_change_keeps_ledger_unchanged` | `ReceivingLine.save()` on a notes/shipper edit does NOT touch the ledger |

## Verification

- **340 pytest tests pass** (was 333 before the new file)
- **247/247 Django tests pass**
- `manage.py check` — 0 issues
- `makemigrations --check --dry-run` — no changes
- Production service: `ppe-inventory.service` **active** (Gunicorn)
- Git: `master` is now at commit `109a744`, pushed to `origin`

---

## Deployment

This change is in the **bulk-receiving code path only**. Gunicorn does not auto-reload source changes, so the running workers are still serving the **old** code. To activate:

1. `sudo systemctl restart ppe-inventory.service`
2. Confirm with: `sudo systemctl status ppe-inventory.service` → 3 workers active
3. Smoke test on `bbx.rplwms.com/receiving/` — upload a small 2-row spreadsheet with `["Part #", "Quantity", "PO", "Notes"]` headers and confirm:
   - One ticket created, two lines under it
   - Stock went up by the right amount
   - One RECEIPT row per line in `InventoryTransaction`

## Rollback

```bash
git revert 109a744 && git push origin master
sudo systemctl restart ppe-inventory.service
```

No DB rollback needed — the change is view-layer only.

## Side observation worth flagging

The audit doc (`ppe-wms-comprehensive-audit-2026-09-12.md`) item **#7** said production was running `manage.py runserver 0.0.0.0:8088 --insecure` with `systemd-analyze security` score 9.2/UNSAFE. The live process list right now shows Gunicorn 3 workers on `127.0.0.1:8089`. Audit item #7 is **already shipped** — whoever restarted it post-audit moved it to Gunicorn. The audit doc needs a refresh on that one point.

## Audit progress after this commit

| P0 item | Status |
|---|---|
| #1 Requester ownership | ✅ shipped earlier today |
| #2 Vulnerable dependencies | open (next candidate) |
| **#3 Bulk-receiving double-count** | **✅ shipped in this commit** |
| #4 Attachment routes | open |
| #5 Stock lifecycle redesign | open (large) |
| #6 Immutable ledger | open (large) |

3 of 6 P0 items now closed.
