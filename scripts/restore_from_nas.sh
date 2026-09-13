#!/bin/bash
# WMS restore validation from a NAS encrypted backup
# Usage: ./restore_from_nas.sh <backup-dir-name>
# Validates first, then publishes db.sqlite3.restored and media.restored.
# Never overwrites live db.sqlite3 or media.
set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: $0 <backup-dir-name>" >&2
    exit 2
fi

BACKUP_DIR_NAME="$1"
if [[ "$BACKUP_DIR_NAME" == */* || "$BACKUP_DIR_NAME" == "." || "$BACKUP_DIR_NAME" == ".." ]]; then
    echo "Invalid backup directory name" >&2
    exit 2
fi

NAS_BASE="${NAS_BASE:-/mnt/nas-bonkvault/wms-backups}"
BACKUP_DIR="$NAS_BASE/$BACKUP_DIR_NAME"
TARGET="${TARGET:-/home/hermes/projects/ppe_inventory}"
GPG_BIN="${GPG_BIN:-gpg}"
PYTHON="${PYTHON:-/home/hermes/projects/ppe-pick-ticket-venv/bin/python}"

if [ ! -d "$BACKUP_DIR" ]; then
    echo "Backup not found: $BACKUP_DIR" >&2
    exit 1
fi
if [ -L "$TARGET" ]; then
    echo "Refusing symlink restore target: $TARGET" >&2
    exit 1
fi
mkdir -p "$TARGET"

mapfile -t encrypted_files < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name '*.tar.gz.gpg' -printf '%f\n' | sort)
if [ "${#encrypted_files[@]}" -ne 1 ]; then
    echo "Expected exactly one encrypted archive in $BACKUP_DIR; found ${#encrypted_files[@]}" >&2
    exit 1
fi
ENCRYPTED_NAME="${encrypted_files[0]}"

(
    cd "$BACKUP_DIR"
    sha256sum -c SHA256SUMS
)

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/wms-restore.XXXXXX")"
cleanup() {
    rm -rf -- "$STAGE"
}
trap cleanup EXIT
chmod 700 "$STAGE"
ARCHIVE="$STAGE/restore.tar.gz"
UNPACKED="$STAGE/unpacked"
mkdir -p "$UNPACKED"

"$GPG_BIN" --batch --yes --decrypt "$BACKUP_DIR/$ENCRYPTED_NAME" > "$ARCHIVE"

# Reject absolute/traversing member names before extracting selected content.
while IFS= read -r member; do
    if [[ "$member" == /* || "/$member/" == *"/../"* ]]; then
        echo "Unsafe archive member: $member" >&2
        exit 1
    fi
done < <(tar -tzf "$ARCHIVE")

tar xzf "$ARCHIVE" -C "$UNPACKED" db.sqlite3
tar xzf "$ARCHIVE" -C "$UNPACKED" --wildcards --no-anchored 'media/*' 2>/dev/null || true

if [ ! -f "$UNPACKED/db.sqlite3" ]; then
    echo "Archive does not contain db.sqlite3" >&2
    exit 1
fi

# Validation must succeed before any prior staged restore is replaced.
"$PYTHON" - "$UNPACKED/db.sqlite3" <<'PY'
import sqlite3
import sys

path = sys.argv[1]
connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
try:
    result = connection.execute("PRAGMA integrity_check").fetchall()
    if result != [("ok",)]:
        raise SystemExit(f"SQLite integrity_check failed: {result!r}")
    for table in ("auth_user", "inventory_inventoryitem", "inventory_inventorytransaction"):
        connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
finally:
    connection.close()
PY

if [ -L "$TARGET/db.sqlite3.restored" ] || [ -L "$TARGET/media.restored" ]; then
    echo "Refusing to replace symlinked staged restore output" >&2
    exit 1
fi

DB_TEMP="$(mktemp "$TARGET/.db.sqlite3.restored.XXXXXX")"
cp "$UNPACKED/db.sqlite3" "$DB_TEMP"
chmod 600 "$DB_TEMP"
mv -fT "$DB_TEMP" "$TARGET/db.sqlite3.restored"

MEDIA_TEMP="$(mktemp -d "$TARGET/.media.restored.XXXXXX")"
if [ -d "$UNPACKED/media" ]; then
    cp -a "$UNPACKED/media/." "$MEDIA_TEMP/"
fi
MEDIA_OLD=""
if [ -e "$TARGET/media.restored" ]; then
    MEDIA_OLD="$(mktemp -d "$TARGET/.media.restored.previous.XXXXXX")"
    rmdir "$MEDIA_OLD"
    mv -T "$TARGET/media.restored" "$MEDIA_OLD"
fi
mv -T "$MEDIA_TEMP" "$TARGET/media.restored"
if [ -n "$MEDIA_OLD" ]; then
    rm -rf -- "$MEDIA_OLD"
fi

printf 'users=%s\n' "$("$PYTHON" -c "import sqlite3; c=sqlite3.connect(r'$TARGET/db.sqlite3.restored'); print(c.execute('select count(*) from auth_user').fetchone()[0]); c.close()")"
printf 'items=%s\n' "$("$PYTHON" -c "import sqlite3; c=sqlite3.connect(r'$TARGET/db.sqlite3.restored'); print(c.execute('select count(*) from inventory_inventoryitem').fetchone()[0]); c.close()")"
printf 'transactions=%s\n' "$("$PYTHON" -c "import sqlite3; c=sqlite3.connect(r'$TARGET/db.sqlite3.restored'); print(c.execute('select count(*) from inventory_inventorytransaction').fetchone()[0]); c.close()")"
echo "DONE: validated restore staged at $TARGET/db.sqlite3.restored and $TARGET/media.restored"
