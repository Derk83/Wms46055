# Non-OFCI inventory restoration and retired Facebook field removal

**Released:** 2026-09-24

## Summary

Restored the approved inventory catalog from the preserved pre-reset WMS database while excluding all OFCI material. Any source item with either an `OFCI` category or a populated Facebook part number was excluded. The Facebook-specific inventory field and its runtime behavior were then removed from the WMS.

## Recovery points

- Current pre-change empty-system backup: `wms-backup-20260924-164307.tar.gz`
  - SHA-256: `e03455b061f2e1638928672cbadb663114a999551573a23594d981bf9a807c24`
- Permanent pre-reset OFCI/Facebook recovery archive: `ofci-facebook-recovery-pre-reset-20260921.tar.gz`
  - SHA-256: `c620b864176ab487bf60d281d1df7c6dfa63bd709a514fe6e41e83c3d1d25fd4`

Both archives were validated with `tar -tzf`. The permanent archive remains unchanged and retains the excluded records for a possible future OFCI restoration.

## Restore reconciliation

| Measure | Result |
|---|---:|
| Source inventory items | 392 |
| Source category OFCI | 167 |
| Source items with a Facebook part number | 169 |
| Excluded union | 169 |
| Restored non-OFCI items | 223 |
| Restored quantity | 6,893 |
| Audited opening ledger entries | 219 |
| Restored OFCI rows | 0 |
| Negative inventory rows | 0 |

The two exclusion sets overlapped except for two CFCI rows that had populated Facebook part numbers; those rows were also excluded as required.

## Application changes

- Removed the Facebook part-number field from the inventory schema and item form.
- Removed the field from inventory search, filters, sorting, data-quality filters, and global search.
- Removed it from inventory tables, item details, barcode labels, material requests, pick tickets, backorders, procurement pages, and print layouts.
- Removed it from spreadsheet import/export, CSV export, item-search API output, and picker search data.
- Realigned spreadsheet columns and location validations after removing the export column.

## Verification

- Filtered restore rehearsed in an isolated database in both rollback-only and committed modes.
- Rehearsal SQLite integrity check: `ok`; foreign-key check: no violations.
- Full test gate: **650 passed, 2 skipped, 55 subtests passed**.
- Focused post-review regression gate: **141 passed, 8 subtests passed**.
- Django system check, migration drift check, Python compilation, JavaScript syntax, and `git diff --check`: passed.
- Independent read-only review: no blocker, high, or medium findings.
- Production database integrity check: `ok`; foreign-key check: no violations.
- Authenticated production renders for inventory list, item detail, material-request creation, and requests portal returned HTTP 200 and contained no Facebook/FB Part text.
- Local reverse-proxy verification returned the expected Warehouse login redirect and HTTP 200 for Equipment Requests.
- `ppe-inventory.service` remained active with zero unexpected restarts.

## Release

- Application commit: `41d20206b30aab02ee9dacf5baaf8fe62fe92acb`
- Schema migration: `inventory.0052_remove_inventoryitem_fb_part_number`
