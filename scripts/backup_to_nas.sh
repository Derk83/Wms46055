#!/bin/bash
# WMS encrypted backup to NAS
# Usage: ./backup_to_nas.sh [label]
set -euo pipefail

LABEL="${1:-manual}"
TS=$(date +%Y%m%d-%H%M%S)
WORKDIR="/home/hermes/projects/ppe_inventory"
NAS_BASE="/mnt/nas-bonkvault/wms-backups"
STAGE="/tmp/ppe-backup-stage"
KEY_FPR="1DA62B583299279F51EA6CDD13640E5246BB2F59"

mkdir -p "$STAGE"
rm -rf "$STAGE"/*
cp "$WORKDIR/db.sqlite3" "$STAGE/db.sqlite3"
sqlite3 "$STAGE/db.sqlite3" ".backup '$STAGE/db.sqlite3.snapshot'" 2>/dev/null || cp "$STAGE/db.sqlite3" "$STAGE/db.sqlite3.snapshot"
cp "$WORKDIR/RESTORE.txt" "$STAGE/RESTORE.txt" 2>/dev/null || true

cd "$WORKDIR"
tar czf "$STAGE/${LABEL}-${TS}.tar.gz" \
    --exclude='__pycache__' --exclude='.pytest_cache' \
    --exclude='*.pyc' --exclude='staticfiles' --exclude='media' \
    db.sqlite3 backups/ inventory/ config/ scripts/ requirements.txt

gpg --batch --yes --recipient "$KEY_FPR" \
    --output "$STAGE/${LABEL}-${TS}.tar.gz.gpg" \
    --encrypt "$STAGE/${LABEL}-${TS}.tar.gz"

OUT="$NAS_BASE/${LABEL}-${TS}"
mkdir -p "$OUT"
mv "$STAGE/${LABEL}-${TS}.tar.gz.gpg" "$OUT/"
mv "$STAGE/db.sqlite3.snapshot" "$OUT/db.sqlite3.bak"

sha256sum "$OUT/${LABEL}-${TS}.tar.gz.gpg" "$OUT/db.sqlite3.bak" > "$OUT/SHA256SUMS"
ls -lh "$OUT/"

rm -rf "$STAGE"

echo "BACKUP_OK: $OUT"
