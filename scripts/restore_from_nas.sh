#!/bin/bash
# WMS restore from NAS encrypted backup
# Usage: ./restore_from_nas.sh <backup-dir-name>
#        e.g. ./restore_from_nas.sh audit-section-14-20260912-213513
set -euo pipefail

BACKUP_DIR_NAME="${1:?usage: $0 <backup-dir-name>}"
NAS_BASE="/mnt/nas-bonkvault/wms-backups"
WORKDIR="/home/hermes/projects/ppe_inventory"
TARGET="${TARGET:-$WORKDIR}"
BACKUP_DIR="$NAS_BASE/$BACKUP_DIR_NAME"
STAGE="/tmp/ppe-restore-$$"

if [ ! -d "$BACKUP_DIR" ]; then
    echo "ERROR: backup not found: $BACKUP_DIR"
    echo "Available backups:"
    ls -1 "$NAS_BASE/"
    exit 1
fi

echo "Validating SHA256..."
sha256sum -c "$BACKUP_DIR/SHA256SUMS" || { echo "checksum mismatch"; exit 1; }

mkdir -p "$STAGE"
echo "Decrypting..."
gpg --batch --yes --decrypt "$BACKUP_DIR/${BACKUP_DIR_NAME}.tar.gz.gpg" \
    2>/dev/null > "$STAGE/backup.tar.gz"

echo "Extracting db..."
tar xzf "$STAGE/backup.tar.gz" -C "$STAGE" db.sqlite3

cp "$STAGE/db.sqlite3" "$TARGET/db.sqlite3.restored"
echo "Restored to: $TARGET/db.sqlite3.restored"

echo "Integrity check:"
/home/hermes/projects/ppe-pick-ticket-venv/bin/python -c "
import sqlite3
c = sqlite3.connect('$TARGET/db.sqlite3.restored')
print('integrity:', c.execute('PRAGMA integrity_check').fetchall())
print('items:', c.execute('SELECT COUNT(*) FROM inventory_inventoryitem').fetchone())
print('txns:', c.execute('SELECT COUNT(*) FROM inventory_inventorytransaction').fetchone())
"

rm -rf "$STAGE"
echo "DONE"
