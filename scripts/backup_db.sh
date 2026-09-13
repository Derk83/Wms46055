#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/hermes/projects/ppe_inventory"
DB_PATH="$PROJECT_DIR/db.sqlite3"
BACKUP_DIR="$PROJECT_DIR/backups"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_PATH="$BACKUP_DIR/db-$STAMP.sqlite3"

mkdir -p "$BACKUP_DIR"
sqlite3 "$DB_PATH" ".backup '$BACKUP_PATH'"
gzip -f "$BACKUP_PATH"
find "$BACKUP_DIR" -name 'db-*.sqlite3.gz' -type f -mtime +30 -delete
printf 'Created backup: %s.gz\n' "$BACKUP_PATH"
