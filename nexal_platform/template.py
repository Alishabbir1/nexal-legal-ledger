"""
Template database management for per-firm provisioning.
"""
import os
import shutil
from typing import Optional

from database import Database

from nexal_platform.config import PlatformPaths, get_platform_paths, require_safe_tenant_db_path, safe_makedirs


def ensure_template_database(paths: Optional[PlatformPaths] = None) -> str:
    """
    Ensure a clean firm template database exists.

    The template contains schema and system-only seed accounts (admin/staff)
    that are removed when cloned to a new firm tenant.
    """
    paths = paths or get_platform_paths()
    template_path = paths.template_db

    if os.path.isfile(template_path):
        return template_path

    safe_makedirs(os.path.dirname(template_path), context="template database")
    Database(db_path=template_path, is_template=True)
    return template_path


def scrub_provisioned_tenant_users(target_path: str) -> None:
    """Remove template system users from a newly provisioned firm ledger database."""
    import sqlite3

    conn = sqlite3.connect(target_path)
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(users)")
        columns = {row[1] for row in cursor.fetchall()}
        if "is_system" in columns:
            cursor.execute("DELETE FROM users WHERE COALESCE(is_system, 0) = 1")
        # Remove legacy template seed accounts (admin/staff) from provisioned tenants
        cursor.execute("DELETE FROM users WHERE username IN ('admin', 'staff')")
        conn.commit()
    finally:
        conn.close()


def clone_template_to_firm_db(template_path: str, target_path: str) -> str:
    """Clone the template database to a firm-specific ledger database path."""
    if not os.path.isfile(template_path):
        raise FileNotFoundError(f"Template database not found: {template_path}")

    target_path = require_safe_tenant_db_path(target_path, context="clone_template_to_firm_db")
    safe_makedirs(os.path.dirname(target_path), context="tenant database parent")
    if os.path.exists(target_path):
        raise FileExistsError(f"Tenant database already exists: {target_path}")

    shutil.copy2(template_path, target_path)
    scrub_provisioned_tenant_users(target_path)
    return target_path
