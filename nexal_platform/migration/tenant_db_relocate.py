"""
Align platform workspace database_path with substantive migrated tenant data.

Used after legacy import when workspace paths or router caches may point at an
empty template while migrated data lives elsewhere under the runtime data root.
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from typing import Iterable, Optional

from nexal_platform.config import (
    PlatformPaths,
    get_platform_paths,
    repair_all_stale_workspace_paths,
    resolve_workspace_database_path,
)
from nexal_platform.platform_db import PlatformDatabase

logger = logging.getLogger(__name__)


def _connect_ro(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def tenant_client_count(db_path: str) -> int:
    if not db_path or not os.path.isfile(db_path) or os.path.getsize(db_path) < 512:
        return 0
    try:
        conn = _connect_ro(db_path)
    except (OSError, PermissionError, sqlite3.Error):
        return 0
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='clients'"
        ).fetchone()
        if row is None:
            return 0
        return int(conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0])
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def iter_tenant_database_paths(paths: PlatformPaths) -> Iterable[str]:
    tenants_dir = paths.tenants_dir
    if not os.path.isdir(tenants_dir):
        return
    for entry in sorted(os.listdir(tenants_dir)):
        candidate = os.path.join(tenants_dir, entry, "solicitor_ledger.db")
        if os.path.isfile(candidate):
            yield candidate


def find_best_tenant_database(
    paths: PlatformPaths,
    *,
    min_clients: int = 1,
) -> Optional[str]:
    best_path: Optional[str] = None
    best_count = 0
    for db_path in iter_tenant_database_paths(paths):
        count = tenant_client_count(db_path)
        if count >= min_clients and count > best_count:
            best_count = count
            best_path = db_path
    return best_path


def repair_firm_tenant_database_path(
    platform: PlatformDatabase,
    firm_id: str,
    *,
    min_clients: int = 1,
    allow_global_scan: bool = False,
) -> str:
    """
    Ensure the firm's workspace points at a tenant DB with substantive data.

    When the canonical path is empty but another tenant DB under the runtime root
    contains migrated data, copy it to the canonical path and update platform.db.
    """
    paths = platform.paths
    repair_all_stale_workspace_paths(platform)

    conn = platform.get_connection()
    try:
        row = conn.execute(
            "SELECT database_path FROM workspaces WHERE firm_id = ?",
            (firm_id,),
        ).fetchone()
        raw_stored = row["database_path"] if row else None
    finally:
        conn.close()

    workspace = platform.get_workspace_for_firm(firm_id)
    stored_path = workspace["database_path"]
    canonical = paths.tenant_db_path(firm_id)
    resolve_workspace_database_path(platform, firm_id, stored_path, paths)
    canonical_count = tenant_client_count(canonical)

    if canonical_count >= min_clients:
        if os.path.abspath(stored_path) != os.path.abspath(canonical):
            platform.update_workspace_database_path(firm_id, canonical)
        return canonical

    source: Optional[str] = None
    source_count = 0
    for candidate in (raw_stored, stored_path):
        if not candidate or not os.path.isfile(candidate):
            continue
        count = tenant_client_count(candidate)
        if count >= min_clients and count > source_count:
            source = candidate
            source_count = count

    if source is None and allow_global_scan:
        source = find_best_tenant_database(paths, min_clients=min_clients)
        source_count = tenant_client_count(source) if source else 0

    if source is None or source_count < min_clients:
        return canonical

    if os.path.abspath(source) == os.path.abspath(canonical):
        platform.update_workspace_database_path(firm_id, canonical)
        return canonical

    logger.warning(
        "Relocating migrated tenant DB for firm %s: %s (%d clients) -> %s",
        firm_id,
        source,
        source_count,
        canonical,
    )
    os.makedirs(os.path.dirname(canonical), exist_ok=True)
    staging = canonical + ".relocating"
    shutil.copy2(source, staging)
    os.replace(staging, canonical)
    platform.update_workspace_database_path(firm_id, canonical)
    return canonical


def ensure_tenant_ready_for_sso(
    platform: PlatformDatabase,
    firm_id: str,
    *,
    min_clients: int = 1,
) -> str:
    """Repair workspace path and ensure security columns exist on the tenant DB."""
    from database import Database
    from db_router import clear_router_cache

    db_path = repair_firm_tenant_database_path(platform, firm_id, min_clients=min_clients)
    tenant_db = Database(db_path=db_path, skip_user_seed=True)
    tenant_db.initialize_security_columns()
    clear_router_cache()
    return db_path
