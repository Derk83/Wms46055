#!/usr/bin/env python3
from __future__ import annotations

import gzip
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_DIR = Path("/home/hermes/projects/ppe_inventory")
DB_PATH = PROJECT_DIR / "db.sqlite3"
BACKUP_DIR = PROJECT_DIR / "backups"

BACKUP_DIR.mkdir(parents=True, exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
backup_sqlite = BACKUP_DIR / f"db-{stamp}.sqlite3"
backup_gz = backup_sqlite.with_suffix(".sqlite3.gz")

source = sqlite3.connect(DB_PATH)
try:
    destination = sqlite3.connect(backup_sqlite)
    try:
        source.backup(destination)
    finally:
        destination.close()
finally:
    source.close()

with backup_sqlite.open("rb") as src, gzip.open(backup_gz, "wb") as dst:
    shutil.copyfileobj(src, dst)
backup_sqlite.unlink()

cutoff = datetime.now() - timedelta(days=30)
for path in BACKUP_DIR.glob("db-*.sqlite3.gz"):
    if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
        path.unlink()

print(f"Created backup: {backup_gz}")
