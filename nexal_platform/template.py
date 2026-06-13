"""
Template database management for per-firm provisioning.
"""
import os
import shutil

from database import Database

from nexal_platform.config import PlatformPaths, get_platform_paths


def ensure_template_database(paths: PlatformPaths | None = None) -> str:
    """
    Ensure a clean firm template database exists.

    The template contains the full ledger schema with default admin/staff users
    but no client or transaction data.
    """
    paths = paths or get_platform_paths()
    template_path = paths.template_db

    if os.path.isfile(template_path):
        return template_path

    os.makedirs(os.path.dirname(template_path), exist_ok=True)
    Database(db_path=template_path)
    return template_path


def clone_template_to_firm_db(template_path: str, target_path: str) -> str:
    """Clone the template database to a firm-specific ledger database path."""
    if not os.path.isfile(template_path):
        raise FileNotFoundError(f"Template database not found: {template_path}")

    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    if os.path.exists(target_path):
        raise FileExistsError(f"Tenant database already exists: {target_path}")

    shutil.copy2(template_path, target_path)
    Database(db_path=target_path)
    return target_path
