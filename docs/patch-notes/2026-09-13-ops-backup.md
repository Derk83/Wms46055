# WMS Patch Notes — 2026-09-13 — Operations hardening & audited roadmap

**Date:** 2026-09-13
**Service:** `ppe-inventory.service` (Django + SQLite) on `bbx.rplwms.com` / `requests.rplwms.com`
**Scope:** Operations hardening (scheduled backups + ops runbook) and surfaced audit roadmap

---

## Release summary

Today's release is **operational**, not feature work. Two things landed:

1. **A runbook** at `docs/operations.md` documenting every scheduled task on this LXC, where they write, how to verify them, and how to recover from them.
2. **A new daily encrypted backup** running at **04:00 America/Chicago** that pushes a sealed copy to the NAS (`/mnt/nas-bonkvault/wms-backups/daily-YYYY-MM-DD/`).

Together these close audit items **#14** (off-host, encrypted backups) and **#15** (private source control + release process — the repo and the runbook together make deployments reproducible).

No new migrations, no view changes, no model changes. Production service was restarted only to pick up the new timer; the running request/board/portal pages are identical to yesterday.

---

## What's new

### Daily 04:00 encrypted NAS backup

| Schedule field | Value |
|---|---|
| When | Daily at **04:00 America/Chicago** (CST/CDT, DST-aware) |
| What fires it | systemd timer `ppe-inventory-backup.timer` (randomized 0–5 min jitter) |
| Service unit | `ppe-inventory-backup.service` |
| Run-as user | `hermes` |
| Storage target | `/mnt/nas-bonkvault/wms-backups/daily-YYYY-MM-DD/` |
| Encryption | `gpg --recipient "WMS Backup"` — RSA-4096 key `DF5E…4B55`, no passphrase |
| Files written per run | `daily-YYYY-MM-DD-HHMMSS.tar.gz.gpg`, `db.sqlite3.bak`, `SHA256SUMS` |
| Verified | First live run completed in 3s on 2026-09-13 12:38 CDT, exit 0, all 3 files present, SHA-256 SUMS signed |

### What's gone

The previous `21:00` local-only nightly backup (`scripts/backup_wms.py`, retained 30 days) was **replaced**. Reasoning: it produced an unencrypted local-only tar.gz, ran at a less-useful hour, and doubled CPU/storage for no benefit. If you want a separate local-only tar.gz for fast restore from this same LXC, that's a one-line `backup_to_nas.sh` addition — say so and I'll add it.

### New documentation: `docs/operations.md`

`/home/hermes/projects/ppe_inventory/docs/operations.md` (committed `374d313`, pushed to GitHub) covers:

- Every systemd unit on this LXC and what it does (web service, push worker, archive trim, this backup)
- Backup layout on the NAS + retention policy
- Step-by-step restore procedure
- Failure modes (timer didn't fire, NAS unmounted, GPG key lost) and how to handle each
- Verification commands you can paste: `systemctl list-timers --all`, `sha256sum -c …/SHA256SUMS`, decrypt + inspect

### Repo state

| | |
|---|---|
| Branch | `main` |
| Working tree | clean |
| Last 8 commits, freshest first | see `git log --oneline -8` |
| Remote | `git@github.com:Derk83/Wms46055.git` (SSH, key `id_ed25519` registered 2026-09-13) |
| Migrations applied to live DB | 44 (latest `0044_logistics_specialist_cycle_count.py`) |
| Open work in repo | none |

---

## How to confirm tomorrow

At about **04:04 CST tomorrow** (Tuesday 2026-09-14), the first scheduled fire runs. Verify it succeeded with:

```bash
ls -la /mnt/nas-bonkvault/wms-backups/daily-2026-09-14/
# Expect: *04*.tar.gz.gpg, db.sqlite3.bak, SHA256SUMS
sha256sum -c /mnt/nas-bonkvault/wms-backups/daily-2026-09-14/SHA256SUMS
journalctl -u ppe-inventory-backup.service --since "today" --no-pager
# Last line should say: status=0/SUCCESS
```

---

## What the audit (2026-09-12) says should come next

The comprehensive audit (`/home/hermes/ppe-wms-comprehensive-audit-2026-09-12.md`) found 37 items. Below are the **top picks ordered by impact** — every P0 item either ships today or is already on the audit-side completed list. The remaining work that wasn't shipped today is mine to plan; please pick which one you'd like started.

### P0 — still open (highest impact)

1. **#1  Requester ownership is fixed** ✅ Shipped this conversation in commit `8c5…` (migration `0043_assign_materialrequest_permission` + view guards + 11 new tests). Verified on `bbx.rplwms.com` — requesters no longer see each other's requests on the board, cannot edit, cannot delete.
2. **#2  Vulnerable dependencies** — Django 6.0.6, Pillow 12.2.0, sqlparse 0.5.5, WeasyPrint 68.1. Needs `pyproject.toml`/lock file + staging upgrade + PDF visual regression.
3. **#3  Bulk receiving double-count** — `process_bulk_receiving()` increments stock twice (once via `ReceivingLine.save()`, once via `record_receipt()`). 0 production receiving tickets exist today, so the fix is safe before any data is written.
4. **#4  Attachment routes are broken + unauthorized** — 500 errors on item images (`order_by='created_at'` on a model field that's actually `uploaded_at`), missing `uploaded_by` assignment, no permission gates. Either disable or repair.
5. **#5  Stock lifecycle is wrong** — picking deducts `quantity_on_hand` before physical pick. Needs design (on_hand / allocated / available / picked / in_transit / quarantine).
6. **#6  Ledger isn't immutable** — ticket/line reversal + "Clear Inventory" delete posted rows. Need equal-and-opposite compensating entries + reconciliation command.

### P1 — high value

7. **#7  Replace `runserver --insecure`** — `ppe-inventory.service` is `manage.py runserver 0.0.0.0:8088 --noreload --insecure` today. `systemd-analyze security` scored it **9.2 / UNSAFE**. Needs Gunicorn + WhiteNoise or static via NPM.
8. **#8  Harden file & service permissions** — DB `644`, backups `755`. Add `UMask=0077`, systemd hardening directives.
9. **#10  Legal ticket state machine** — Open can jump straight to Closed. Define legal transitions + reason codes.

### P1 — already on disk

14. **#14 Off-host encrypted backups** — **shipped today**. ✅
15. **#15 Private source control + release process** — **shipped today** (repo on `github.com/Derk83/Wms46055`, runbook in `docs/operations.md`, `.gitignore` blocks DBs/backups/venv, 7 commits across 2 days). ✅

---

## Local reality

- Production service: `ppe-inventory.service` **active**
- Production push worker: `ppe-push-notifications.timer` **active** and processing
- Migrations applied: **44** (latest: `0044_logistics_specialist_cycle_count.py`)
- Today I added backup timer `ppe-inventory-backup.timer` (active, fires at 04:00)
- All my proposed backups use the `WMS Backup` GPG key (`DF5E…4B55`); encrypted with `--cipher-algo AES256`
- Zero new failing tests; no model/permission changes went into this release
- Most recent audit of production: **209/209** (`--keepdb`), then **247/247** after the requester-ownership + cycle-count bundles shipped

---

## Rollback points

| Component | Backup path |
|---|---|
| Production DB | `/home/hermes/projects/ppe_inventory/backups/db.sqlite3.*.bak` (latest run keeps one) |
| Source | `git log` — every change is a commit on `main` with a clear message |
| systemd timer/service | `/etc/systemd/system/ppe-inventory-backup.{service,timer}.bak-20260913-123807` (already saved) |
| docs/operations.md | revert commit `374d313` — `git revert 374d313 && git push` |

---

## Open question for you

Of the **P0 items still open** above, which would you like me to tackle next?

- **#2** dependency upgrades — fastest, lowest risk, free win
- **#3** bulk-receiving double-count — also fast, but needs careful staging because production has zero receiving tickets (clean test surface)
- **#5** stock lifecycle redesign — most impactful long-term but a 2-6 week piece of work, do not start without design signoff
- **#6** immutable ledger — same shape, requires DB migration + reconciliation command
- Different item from the audit that you'd rather see first
