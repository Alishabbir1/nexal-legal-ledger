"""
SQLite snapshot, compression, and checksum helpers.
"""
import gzip
import hashlib
import os
import shutil
import sqlite3
import zipfile
from typing import Optional, Tuple


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_sqlite_snapshot(source_path: str, dest_path: str) -> None:
    """Create a consistent SQLite copy using the online backup API."""
    if not os.path.isfile(source_path):
        raise FileNotFoundError(f"Database not found: {source_path}")

    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    if os.path.exists(dest_path):
        os.remove(dest_path)

    try:
        src = sqlite3.connect(source_path, timeout=30)
        try:
            src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            dst = sqlite3.connect(dest_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except Exception:
        shutil.copy2(source_path, dest_path)


def package_database(
    db_path: str,
    output_path: str,
    compress: bool = True,
) -> Tuple[str, int]:
    """
    Package a database file into a zip (optionally gzip-only for single file).
    Returns (checksum_sha256, size_bytes).
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    base_name = os.path.basename(db_path)

    if output_path.endswith(".gz"):
        with open(db_path, "rb") as src, gzip.open(output_path, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst)
    else:
        if not output_path.endswith(".zip"):
            output_path = output_path + ".zip"
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(db_path, arcname=base_name)

    if not os.path.isfile(output_path):
        raise RuntimeError(f"Backup package was not created: {output_path}")

    return sha256_file(output_path), os.path.getsize(output_path)


def verify_checksum(path: str, expected: str) -> bool:
    if not expected:
        return False
    return sha256_file(path) == expected.strip().lower()


def verify_zip(path: str) -> bool:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            return zf.testzip() is None
    except Exception:
        return False


def extract_database(package_path: str, dest_db_path: str) -> None:
    """Extract solicitor_ledger.db from zip or gzip package into dest_db_path."""
    os.makedirs(os.path.dirname(dest_db_path) or ".", exist_ok=True)

    if package_path.endswith(".gz"):
        with gzip.open(package_path, "rb") as src, open(dest_db_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return

    with zipfile.ZipFile(package_path, "r") as zf:
        names = [n for n in zf.namelist() if n.endswith(".db")]
        if not names:
            raise ValueError("Backup archive does not contain a database file.")
        with zf.open(names[0]) as src, open(dest_db_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
