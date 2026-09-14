# Master Warehouse Inventory Import

**Import date:** September 14, 2026

**Application:** RPL Warehouse WMS

## Source

- Workbook: `RPL Master Warehouse Inventory for WMS.xlsx`
- Imported sheet: `Inventory`
- Source SHA-256: `551b7ae5e38d5c96c897851e73b05f5e4fa7d95757bfed9d107ad711635372aa`
- The `Pick Tickets` and `Backorder` sheets were not imported into inventory.

## Backup

A database/application backup was created immediately before import:

- `backups/wms-backup-20260914-112603.tar.gz`
- SHA-256: `6216c9ac31c32d9bdb92fc6af8b47ec67331b2fc60a1ec18e5cc42f925a0a117`

## Import results

- Inventory items created: **392**
- Total on-hand quantity: **9,746**
- Immutable import-ledger entries: **387**
- Items with zero quantity: **5**
- Items with an FB Part #: **169**
- Structured rack/bin locations: **285**
- Named/nonstandard warehouse locations preserved: **107**

### Categories

| Category | Items |
|---|---:|
| OFCI | 167 |
| CFCI | 9 |
| Equipment | 106 |
| Supplies | 110 |

## Data handling

- Repeated manufacturer part/location rows were retained as separate inventory records rather than merged, preserving separate reels and stock lines.
- Spreadsheet FB Part # placeholders containing `-` were stored as blank values.
- Human-readable locations such as Floor, Firebox, Cage, and office Rack locations were retained in the building/location field.
- The blank rack on source row 94 was assigned to rack A based on the adjacent rack A / 05-06 entry.
- Two genuine items without model numbers received traceable identifiers:
  - `NO-MODEL-SOURCE-0023` — One Click Cleaner
  - `NO-MODEL-SOURCE-0633` — 55 Gallon Trash Can
- Empty placeholder rows 519 and 520 were skipped.
- Cost and Total Cost columns were not imported because the WMS inventory model does not contain cost fields.

## Verification

- Exact source-to-database reconciliation: **392/392 rows matched**.
- Inventory on-hand total and immutable ledger total both equal **9,746**.
- Every item's on-hand quantity reconciles to its ledger entries.
- Authenticated inventory page returned HTTP 200.
- Production XLSX export returned all 392 items and placed `FB Part #` in column 2.
- Production service remained active with zero unexpected restarts.
