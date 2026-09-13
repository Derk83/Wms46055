# Operations: scheduled tasks, backup policy, rotate-on-deploy

This project is deployed on a Proxmox LXC at `192.168.0.177` (PVE host
`pve01`). Two systemd timer units fire background jobs that are not
visible from the Django process — they must be installed and configured
on the host, separately from the repo.

## systemd units

| Unit | Schedule | What it runs |
|------|----------|--------------|
| `ppe-inventory.service` | n/a (always-on) | Gunicorn binding `127.0.0.1:8089`, 3 workers, source `config.wsgi:application` |
| `ppe-inventory-backup.timer` | **04:00 America/Chicago daily** (jitter ±5min) | `scripts/backup_to_nas.sh daily` — pushes GPG-encrypted tar.gz + plaintext db.sqlite3.bak + SHA256SUMS to `/mnt/nas-bonkvault/wms-backups/daily-YYYY-MM-DD/` |
| `ppe-push-notifications.timer` | every few minutes | Drains the `PushDelivery` outbox to subscribed browsers via VAPID |
| `ppe-material-request-archive.timer` | daily | Rolls up old material-request board state to the archive |

The timer & service files are installed from this template (copy into
`/etc/systemd/system/` on the deploy host, then `systemctl daemon-reload`
and `systemctl enable --now <name>.timer`).

### `ppe-inventory-backup.service`

```ini
[Unit]
Description=WMS daily backup to NAS (encrypted GPG + plaintext DB + SHA256SUMS)

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
| NAS daily | `/mnt/nas-bonkvault/wms-backups/daily-YYYY-MM-DD/` (per day, e.g. `daily-2026-09-13/`) | GPG to fingerprint `1DA62B58...` + plaintext DB + SHA256SUMS | None (caller prune, currently unbounded) |
| NAS one-off | `/mnt/nas-bonkvault/wms-backups/<label>-YYYYMMDD-HHMMSS/` (manual runs of `backup_to_nas.sh <label>`) | Same as above | None |
| Local backups | `/home/hermes/projects/ppe_inventory/backups/wms-backup-*.tar.gz` | None | 30 days, pruned by `scripts/backup_wms.py RETENTION_DAYS` |

`backup_to_nas.sh` requires the `sqlite3` CLI helper **or** falls back to
Python's `sqlite3` module (the daily timer assumes the latter). Tested
on Django 6.1 / Python 3.13.

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
cd /mnt/nas-bonkvault/wms-backups/daily-2026-09-13/

# Confirm hashes match
sha256sum -c SHA256SUMS

# Decrypt in-memory and inspect contents
gpg --output /tmp/restore-test.tar.gz --decrypt \
    daily-2026-09-13-*.tar.gz.gpg

tar tzf /tmp/restore-test.tar.gz | head
tar xzf /tmp/restore-test.tar.gz wms-db/database.sqlite3
sqlite3 wms-db/database.sqlite3 \
    "SELECT COUNT(*) FROM auth_user;"
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
# Pull the encrypted tar.gz + sha256 + db.sqlite3.bak
# (or just the db.sqlite3.bak if you only need the DB)

# To restore the DB fast (no decrypt needed):
sudo systemctl stop ppe-inventory
cp /mnt/nas-bonkvault/wms-backups/<run>/db.sqlite3.bak \
   /home/hermes/projects/ppe_inventory/db.sqlite3
chown hermes:hermes /home/hermes/projects/ppe_inventory/db.sqlite3
sudo systemctl start ppe-inventory

# To restore the full project tree including DB:
cd /
gpg --output /tmp/restore.tar.gz --decrypt \
    /mnt/nas-bonkvault/wms-backups/<run>/<label>-*.tar.gz.gpg
# Paths inside the archive are absolute, so cd to / first
sudo tar xzf /tmp/restore.tar.gz -C /
# Then point Django at the restored DB and start
sudo systemctl restart ppe-inventory
```
