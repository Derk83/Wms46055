#!/bin/bash
# WMS encrypted backup to NAS
# Usage: ./backup_to_nas.sh [label]
set -euo pipefail

LABEL="${1:-manual}"
if [[ ! "$LABEL" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "Invalid backup label" >&2
    exit 2
fi
TS=$(date +%Y%m%d-%H%M%S)
WORKDIR="${WORKDIR:-/home/hermes/projects/ppe_inventory}"
NAS_BASE="${NAS_BASE:-/mnt/nas-bonkvault/wms-backups}"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/wms-backup.XXXXXX")"
KEY_FPR="${KEY_FPR:-1DA62B583299279F51EA6CDD13640E5246BB2F59}"
PYTHON_BIN="${PYTHON_BIN:-/home/hermes/projects/ppe-pick-ticket-venv/bin/python}"
GPG_BIN="${GPG_BIN:-gpg}"
ARCHIVE_NAME="${LABEL}-${TS}.tar.gz"
OUT="$NAS_BASE/${LABEL}-${TS}"

cleanup() {
    rm -rf "$STAGE"
}
trap cleanup EXIT

mkdir -p "$STAGE"

# Use SQLite's online backup API so the encrypted archive receives a coherent
# point-in-time database snapshot even while the WMS is running.
"$PYTHON_BIN" - "$WORKDIR/db.sqlite3" "$STAGE/db.sqlite3" <<'PY'
import sqlite3
import sys

source_path, snapshot_path = sys.argv[1:3]
source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
target = sqlite3.connect(snapshot_path)
try:
    source.backup(target)
    result = target.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise SystemExit(f"snapshot integrity check failed: {result}")
finally:
    target.close()
    source.close()
PY

cp "$WORKDIR/RESTORE.txt" "$STAGE/RESTORE.txt" 2>/dev/null || true

# Archive the coherent snapshot plus source, operational scripts, prior backup
# metadata, and all user-uploaded media referenced by the database.
archive_members=(backups inventory config scripts requirements.txt)
if [ -e "$WORKDIR/media" ]; then
    archive_members+=(media)
fi

stage_members=(db.sqlite3)
if [ -e "$STAGE/RESTORE.txt" ]; then
    stage_members+=(RESTORE.txt)
fi

tar czf "$STAGE/$ARCHIVE_NAME" \
    --exclude='__pycache__' --exclude='.pytest_cache' \
    --exclude='*.pyc' --exclude='staticfiles' \
    -C "$STAGE" "${stage_members[@]}" \
    -C "$WORKDIR" "${archive_members[@]}"

"$GPG_BIN" --batch --yes --recipient "$KEY_FPR" \
    --output "$STAGE/$ARCHIVE_NAME.gpg" \
    --encrypt "$STAGE/$ARCHIVE_NAME"

mkdir -p "$OUT"
mv "$STAGE/$ARCHIVE_NAME.gpg" "$OUT/"
(
    cd "$OUT"
    sha256sum "$ARCHIVE_NAME.gpg" > SHA256SUMS
)
ls -lh "$OUT/"

echo "BACKUP_OK: $OUT"
