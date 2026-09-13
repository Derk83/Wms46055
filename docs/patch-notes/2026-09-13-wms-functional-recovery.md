# WMS recovery — functional and data-integrity repairs

**Date:** 2026-09-13

## Scope

Recovery audit of recent WMS changes. This bundle intentionally preserves the established visual design; the replacement mobile navigation and universal PDF Exit toolbar are held for owner approval from rendered mockups.

## Fixed

- Sealed completed, cancelled, and archived cycle counts against further count edits.
- Locked and re-read cycle-count state in the same transaction as count entry updates to close a completion/edit race.
- Made cycle-count archive and unarchive actions POST-only; existing forms retain CSRF protection.
- Corrected daily-report PDF presets so multi-day selections use the same full range as the HTML report.
- Allowed same-origin report PDF embedding through CSP and X-Frame-Options so the in-app viewer can load its PDF.
- Corrected weekly report boundaries and generated timestamps to `America/Chicago`.
- Recovered the weekly report service/timer definitions into version control.
- Corrected navigation permission checks so links only appear when their destinations are accessible.
- Fixed a bulk-receiving transaction bug that could reuse a rolled-back ticket reference and orphan or misassociate receipt lines.
- Rebuilt NAS backup/restore handling:
  - coherent SQLite online snapshot;
  - uploaded media included;
  - encrypted archive only on NAS (no plaintext database copy);
  - checksums use portable relative filenames;
  - restore validates archive paths and SQLite integrity before publishing staged output;
  - staged restore never overwrites the live database or live media;
  - previous staged output remains intact when validation fails.
- Updated operations documentation for the recovered automation and encrypted-only restore workflow.

## Verification

- Regression tests were first observed failing on the broken behavior, then passing after repair.
- Backup→decrypt→restore integration test verifies both database and media recovery.
- Corrupt restore integration test verifies that existing staged output is preserved.
- Django system check passes.
- Migration drift check reports no changes.
- Shell syntax checks pass.
- Systemd unit verification passes.
- Independent second-pass code review: **PASS**.

## Held for visual approval

No hamburger-menu or PDF-toolbar styling is included in this recovery commit. Two iPad-sized renders were prepared for owner review; the selected direction will be implemented, tested, rendered, and approved before production deployment.
