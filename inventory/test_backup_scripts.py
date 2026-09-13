"""Regression and integration tests for the encrypted NAS backup contract."""
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile


ROOT = Path(__file__).resolve().parent.parent
BACKUP_SCRIPT = ROOT / "scripts" / "backup_to_nas.sh"
RESTORE_SCRIPT = ROOT / "scripts" / "restore_from_nas.sh"


def _create_db(path: Path):
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE auth_user (id INTEGER PRIMARY KEY, username TEXT);
            CREATE TABLE inventory_inventoryitem (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE inventory_inventorytransaction (id INTEGER PRIMARY KEY, quantity_delta INTEGER);
            INSERT INTO auth_user VALUES (1, 'backup-test');
            INSERT INTO inventory_inventoryitem VALUES (1, 'gloves');
            INSERT INTO inventory_inventorytransaction VALUES (1, 4);
            """
        )
        connection.commit()
    finally:
        connection.close()


def _fake_gpg(path: Path):
    path.write_text(
        """#!/bin/bash
set -euo pipefail
if [[ " $* " == *" --decrypt "* ]]; then
    cat "${@: -1}"
    exit 0
fi
output=""
previous=""
for argument in "$@"; do
    if [ "$previous" = "--output" ]; then output="$argument"; fi
    previous="$argument"
done
cp "${@: -1}" "$output"
"""
    )
    path.chmod(0o700)


def _environment(**values):
    env = os.environ.copy()
    env.update({key: str(value) for key, value in values.items()})
    return env


def test_backup_contract_is_encrypted_only_and_media_complete():
    source = BACKUP_SCRIPT.read_text()
    restore = RESTORE_SCRIPT.read_text()

    assert "db.sqlite3.bak" not in source
    assert "sqlite3.connect" in source
    assert "db.sqlite3" in source
    assert "archive_members+=(media)" in source
    assert "db.sqlite3.restored" in restore
    assert "media.restored" in restore
    assert "PRAGMA integrity_check" in restore


def test_backup_and_restore_round_trip_database_and_media(tmp_path):
    work = tmp_path / "work"
    nas = tmp_path / "nas"
    target = tmp_path / "target"
    fake_gpg = tmp_path / "fake-gpg"
    for directory in (work / "backups", work / "inventory", work / "config", work / "scripts", work / "media" / "uploads", nas, target):
        directory.mkdir(parents=True, exist_ok=True)
    (work / "requirements.txt").write_text("Django\n")
    (work / "media" / "uploads" / "proof.txt").write_text("media recovered\n")
    _create_db(work / "db.sqlite3")
    _fake_gpg(fake_gpg)

    subprocess.run(
        ["bash", str(BACKUP_SCRIPT), "integration"],
        check=True,
        env=_environment(
            WORKDIR=work,
            NAS_BASE=nas,
            PYTHON_BIN=sys.executable,
            GPG_BIN=fake_gpg,
        ),
        capture_output=True,
        text=True,
    )
    backup_dir = next(nas.iterdir())
    assert not (backup_dir / "db.sqlite3.bak").exists()

    subprocess.run(
        ["bash", str(RESTORE_SCRIPT), backup_dir.name],
        check=True,
        env=_environment(
            NAS_BASE=nas,
            TARGET=target,
            PYTHON=sys.executable,
            GPG_BIN=fake_gpg,
        ),
        capture_output=True,
        text=True,
    )

    assert (target / "media.restored" / "uploads" / "proof.txt").read_text() == "media recovered\n"
    connection = sqlite3.connect(target / "db.sqlite3.restored")
    try:
        assert connection.execute("SELECT username FROM auth_user").fetchone() == ("backup-test",)
    finally:
        connection.close()


def test_corrupt_restore_does_not_replace_previous_staged_outputs(tmp_path):
    nas = tmp_path / "nas"
    target = tmp_path / "target"
    backup_dir = nas / "corrupt-run"
    unpack = tmp_path / "archive-source"
    fake_gpg = tmp_path / "fake-gpg"
    for directory in (backup_dir, target / "media.restored", unpack / "media"):
        directory.mkdir(parents=True, exist_ok=True)
    _fake_gpg(fake_gpg)

    previous_db = target / "db.sqlite3.restored"
    previous_db.write_bytes(b"previous validated database")
    previous_media = target / "media.restored" / "keep.txt"
    previous_media.write_text("previous media\n")
    (unpack / "db.sqlite3").write_bytes(b"not a sqlite database")
    (unpack / "media" / "new.txt").write_text("must not publish\n")

    encrypted = backup_dir / "corrupt-run.tar.gz.gpg"
    with tarfile.open(encrypted, "w:gz") as archive:
        archive.add(unpack / "db.sqlite3", arcname="db.sqlite3")
        archive.add(unpack / "media", arcname="media")
    digest = hashlib.sha256(encrypted.read_bytes()).hexdigest()
    (backup_dir / "SHA256SUMS").write_text(f"{digest}  {encrypted.name}\n")

    result = subprocess.run(
        ["bash", str(RESTORE_SCRIPT), backup_dir.name],
        check=False,
        env=_environment(
            NAS_BASE=nas,
            TARGET=target,
            PYTHON=sys.executable,
            GPG_BIN=fake_gpg,
        ),
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert previous_db.read_bytes() == b"previous validated database"
    assert previous_media.read_text() == "previous media\n"
    assert not (target / "media.restored" / "new.txt").exists()
