# Operations: scheduled tasks, backup policy, rotate-on-deploy

This project is deployed on a Proxmox LXC at `192.168.0.177` (PVE host
`pve01`). Systemd timer units fire background jobs that are not
visible from the Django process — they must be installed and configured
on the host, separately from the repo.

## systemd units

| Unit | Schedule | What it runs |
|------|----------|--------------|
| `ppe-inventory.service` | n/a (always-on) | Gunicorn binding `127.0.0.1:8089`, 3 workers, source `config.wsgi:application` |
| `ppe-inventory-backup.timer` | **04:00 America/Chicago daily** (jitter ±5min) | `scripts/backup_to_nas.sh daily` — snapshots SQLite with its backup API, includes uploaded media, and sends only a GPG-encrypted archive + SHA256SUMS to the NAS |
| `ppe-inventory-weekly-report.timer` | Friday **07:00 America/Chicago** | Generates the previous Mon–Sun report and emails eligible managers |
| `ppe-push-notifications.timer` | every few minutes | Drains the `PushDelivery` outbox to subscribed browsers via VAPID |
| `ppe-material-request-archive.timer` | daily | Rolls up old material-request board state to the archive |

The timer & service files are installed from this template (copy into
`/etc/systemd/system/` on the deploy host, then `systemctl daemon-reload`
and `systemctl enable --now <name>.timer`).

### `ppe-inventory-backup.service`

```ini
[Unit]
Description=WMS daily backup to NAS (encrypted GPG archive + SHA256SUMS)

[Service]
Type=oneshot
User=hermes
Group=hermes
Environment=HOME=/home/hermes
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/home/hermes/projects/ppe_inventory/scripts/backup_to_nas.sh daily
WorkingDirectory=/home/hermes/projects/ppe_inventory

[Install]
WantedBy=multi-user.target
```

### `ppe-inventory-backup.timer`

```ini
[Unit]
Description=Daily WMS encrypted backup at 04:00 America/Chicago

[Timer]
# 04:00 every day. systemd evaluates OnCalendar in the system's local
# time; this LXC is set to America/Chicago and switches CST/CDT
# automatically.
OnCalendar=*-*-* 04:00:00
# If the system was powered off at the scheduled time, fire the backup
# immediately on next boot instead of skipping it.
Persistent=true
# Jitter so multiple LXC timers don't all contend at exactly 04:00.
RandomizedDelaySec=5min
Unit=ppe-inventory-backup.service

[Install]
WantedBy=timers.target
```

## Backup locations and retention

| Layer | Path | Encryption | Retention |
|-------|------|-----------|-----------|
| NAS daily | `/mnt/nas-bonkvault/wms-backups/daily-YYYYMMDD-HHMMSS/` | GPG to fingerprint `1DA62B58...`; no plaintext database artifact | None (caller prune, currently unbounded) |
| NAS one-off | `/mnt/nas-bonkvault/wms-backups/<label>-YYYYMMDD-HHMMSS/` | Same as above | None |
| Local backups | `/home/hermes/projects/ppe_inventory/backups/wms-backup-*.tar.gz` | None | 30 days, pruned by `scripts/backup_wms.py RETENTION_DAYS` |

`backup_to_nas.sh` uses Python's SQLite online backup API to create a coherent
point-in-time database snapshot. The encrypted archive contains that snapshot,
the application files, and uploaded media. Tested on Django 6.1 / Python 3.13.

The GPG key is **unprotected** (no passphrase) and is owned by `hermes`
under `~/.gnupg/`. The service sets `Environment=HOME=/home/hermes`
explicitly so the keyring is reachable from the systemd context.

## Verifying the schedule

```bash
systemctl list-timers ppe-inventory-backup.timer
# Should show NEXT <tomorrow> 04:0X:XX <tz>

systemctl status ppe-inventory-backup.timer
# Trigger line shows the next scheduled invocation

# Manual fire (don't wait for the timer):
sudo systemctl start ppe-inventory-backup.service

# Inspect what the last run produced:
sudo journalctl -u ppe-inventory-backup.service -n 30
ls -la /mnt/nas-bonkvault/wms-backups/ | tail -10
```

## Verifying a backup's integrity

```bash
# Pick a daily run
RUN=daily-20260913-040000
cd "/mnt/nas-bonkvault/wms-backups/$RUN"

# Confirm the encrypted artifact hash matches
sha256sum -c SHA256SUMS

# Stage and integrity-check the database + media without touching production
cd /home/hermes/projects/ppe_inventory
./scripts/restore_from_nas.sh "$RUN"
```

## Deploy procedure

```bash
# 1. Pull / merge to master on the deploy host
ssh pve01 'cd /home/hermes/projects/ppe_inventory && git pull'

# 2. Apply any new migrations
ssh pve01 'cd /home/hermes/projects/ppe_inventory && \
  sudo -u hermes /home/hermes/projects/ppe-pick-ticket-venv/bin/python \
    manage.py migrate --noinput'

# 3. Reload gunicorn (restart, the unit is Type=simple not Type=notify)
ssh pve01 'sudo systemctl restart ppe-inventory'

# 4. Smoke-test
ssh pve01 'cd /home/hermes/projects/ppe_inventory && \
  sudo -u hermes /home/hermes/projects/ppe-pick-ticket-venv/bin/python \
    manage.py check --deploy'

# 5. Verify the backup schedule is still loaded (in case someone edited the timer)
ssh pve01 'systemctl list-timers ppe-inventory-backup.timer'
```

## Restore from a backup

```bash
# Validate, decrypt, and stage the database + media without overwriting live data:
./scripts/restore_from_nas.sh <run-directory-name>

# Inspect db.sqlite3.restored and media.restored first. Stop the service and
# promote them manually only after the integrity check and file review pass.
```
