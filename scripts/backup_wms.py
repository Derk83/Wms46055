#!/usr/bin/env python3
"""Create a restorable WMS backup archive.

Produces one timestamped tar.gz under PROJECT_DIR/backups containing:
- db.sqlite3 copied with SQLite's online backup API
- project/ source tree excluding volatile/cache/backup files
- RESTORE.txt with quick restore notes
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_DIR = Path("/home/hermes/projects/ppe_inventory")
DB_PATH = PROJECT_DIR / "db.sqlite3"
BACKUP_DIR = PROJECT_DIR / "backups"
RETENTION_DAYS = 30

EXCLUDE_DIRS = {
    "backups",
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def should_exclude(path: Path) -> bool:
    rel_parts = path.relative_to(PROJECT_DIR).parts
    if any(part in EXCLUDE_DIRS for part in rel_parts):
        return True
    if path.suffix in EXCLUDE_SUFFIXES:
        return True
    # The DB is added separately using SQLite's consistent backup API.
    if path == DB_PATH:
        return True
    return False


def copy_sqlite_backup(destination: Path) -> None:
    source = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def add_project_files(tar: tarfile.TarFile) -> None:
    for root, dirs, files in os.walk(PROJECT_DIR):
        root_path = Path(root)
        dirs[:] = [d for d in dirs if not should_exclude(root_path / d)]
        for filename in files:
            src = root_path / filename
            if should_exclude(src):
                continue
            arcname = Path("project") / src.relative_to(PROJECT_DIR)
            tar.add(src, arcname=arcname)


def prune_old_backups() -> None:
    cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
    for pattern in ("wms-backup-*.tar.gz", "db-*.sqlite3.gz"):
        for path in BACKUP_DIR.glob(pattern):
            if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                path.unlink()


def main() -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"SQLite database not found: {DB_PATH}")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_path = BACKUP_DIR / f"wms-backup-{stamp}.tar.gz"

    with tempfile.TemporaryDirectory(prefix="wms-backup-") as tmp:
        tmp_path = Path(tmp)
        db_copy = tmp_path / "db.sqlite3"
        copy_sqlite_backup(db_copy)

        restore_txt = tmp_path / "RESTORE.txt"
        restore_txt.write_text(
            "WMS backup restore notes\n"
            "========================\n\n"
            f"Created: {datetime.now().isoformat(timespec='seconds')}\n"
            f"Project path: {PROJECT_DIR}\n"
            "Service: ppe-inventory.service\n\n"
            "Fast DB-only restore:\n"
            "  sudo systemctl stop ppe-inventory.service\n"
            "  cp /path/to/extracted/db.sqlite3 /home/hermes/projects/ppe_inventory/db.sqlite3\n"
            "  sudo chown hermes:hermes /home/hermes/projects/ppe_inventory/db.sqlite3\n"
            "  sudo systemctl start ppe-inventory.service\n\n"
            "Full app restore:\n"
            "  sudo systemctl stop ppe-inventory.service\n"
            "  tar -xzf wms-backup-YYYYMMDD-HHMMSS.tar.gz -C /tmp/wms-restore\n"
            "  rsync -a --delete /tmp/wms-restore/project/ /home/hermes/projects/ppe_inventory/\n"
            "  cp /tmp/wms-restore/db.sqlite3 /home/hermes/projects/ppe_inventory/db.sqlite3\n"
            "  sudo chown -R hermes:hermes /home/hermes/projects/ppe_inventory\n"
            "  sudo systemctl start ppe-inventory.service\n",
            encoding="utf-8",
        )

        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(db_copy, arcname="db.sqlite3")
            tar.add(restore_txt, arcname="RESTORE.txt")
            add_project_files(tar)

    prune_old_backups()
    print(f"Created WMS backup: {archive_path}")


if __name__ == "__main__":
    main()
