"""
Nexal Legal – Phase 4A: Database Router
=========================================
Routes ledger database connections to the correct per-firm SQLite database.

Architecture (One Firm = One Database):

    Firm A  ->  /data/firms/FIRM001/solicitor_ledger.db
    Firm B  ->  /data/firms/FIRM002/solicitor_ledger.db
    Firm C  ->  /data/firms/FIRM003/solicitor_ledger.db

The router:
  1. Accepts a firm_id (or workspace_id)
  2. Looks up the correct db_path in platform.db
  3. Returns a Database instance connected to that firm's DB

In Phase 4A this is the ROUTING FOUNDATION only.
SSO and portal-to-ledger identity mapping are Phase 4B.

Usage:
    from db_router import get_db_for_firm, get_db_for_workspace
    db = get_db_for_firm("FIRM001")
    clients = db.get_all_clients()
"""

import os
import logging
from typing import Optional
from functools import lru_cache

from platform_db import PlatformDB
from database import Database

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level platform DB instance (shared, read-heavy)
# ---------------------------------------------------------------------------

_platform_db: Optional[PlatformDB] = None


def _get_platform_db(platform_db_path: str = None) -> PlatformDB:
    """Return a module-level PlatformDB instance (lazy init)."""
    global _platform_db
    if _platform_db is None or platform_db_path:
        _platform_db = PlatformDB(db_path=platform_db_path)
    return _platform_db


# ---------------------------------------------------------------------------
# Cache of Database instances (one per db_path)
# ---------------------------------------------------------------------------

_db_cache: dict = {}


def _get_or_create_db(db_path: str) -> Database:
    """
    Return a cached Database instance for the given db_path.
    Creating one Database per firm ensures complete data isolation.
    """
    if db_path not in _db_cache:
        logger.info("DBRouter: opening database at %s", db_path)
        _db_cache[db_path] = Database(db_path=db_path)
    return _db_cache[db_path]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_db_for_firm(firm_id: str,
                    platform_db_path: str = None) -> Database:
    """
    Return the Database instance for a given firm_id.

    Args:
        firm_id:          e.g. 'FIRM001'
        platform_db_path: Optional override for platform.db path

    Returns:
        Database instance connected to the firm's SQLite file.

    Raises:
        ValueError: if the firm is not found or is not active.
        FileNotFoundError: if the firm's DB file does not exist on disk.
    """
    pdb = _get_platform_db(platform_db_path)
    firm = pdb.get_firm(firm_id)

    if firm is None:
        raise ValueError(f"Firm '{firm_id}' not found in platform.db.")
    if firm["status"] != "active":
        raise ValueError(
            f"Firm '{firm_id}' is not active (status={firm['status']})."
        )

    db_path = firm["db_path"]
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"Firm '{firm_id}' database not found at '{db_path}'. "
            "Run provision_firm() to set up this firm."
        )

    return _get_or_create_db(db_path)


def get_db_for_workspace(workspace_id: str,
                          platform_db_path: str = None) -> Database:
    """
    Return the Database instance for a given workspace_id.

    Args:
        workspace_id:     e.g. 'WS-FIRM001'
        platform_db_path: Optional override for platform.db path

    Returns:
        Database instance connected to the workspace's SQLite file.

    Raises:
        ValueError: if the workspace is not found or is not active.
        FileNotFoundError: if the DB file does not exist on disk.
    """
    pdb = _get_platform_db(platform_db_path)
    workspace = pdb.get_workspace(workspace_id)

    if workspace is None:
        raise ValueError(
            f"Workspace '{workspace_id}' not found in platform.db."
        )
    if workspace["status"] != "active":
        raise ValueError(
            f"Workspace '{workspace_id}' is not active "
            f"(status={workspace['status']})."
        )

    db_path = workspace["db_path"]
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"Workspace '{workspace_id}' database not found at '{db_path}'."
        )

    return _get_or_create_db(db_path)


def resolve_firm_db_path(firm_id: str,
                         platform_db_path: str = None) -> str:
    """
    Return the db_path for a firm without opening a Database connection.
    Useful for health checks and admin scripts.
    """
    pdb = _get_platform_db(platform_db_path)
    firm = pdb.get_firm(firm_id)
    if firm is None:
        raise ValueError(f"Firm '{firm_id}' not found.")
    return firm["db_path"]


def list_active_firm_ids(platform_db_path: str = None) -> list:
    """Return a list of firm_ids for all currently active firms."""
    pdb = _get_platform_db(platform_db_path)
    return [f["firm_id"] for f in pdb.list_firms(status="active")]


def clear_db_cache():
    """
    Clear the in-memory Database instance cache.
    Call this after reloading firm configuration or in tests.
    """
    global _db_cache, _platform_db
    _db_cache.clear()
    _platform_db = None
    logger.info("DBRouter: cache cleared.")


# ---------------------------------------------------------------------------
# Isolation verification helper (used in tests)
# ---------------------------------------------------------------------------

def verify_isolation(firm_id_a: str, firm_id_b: str,
                     platform_db_path: str = None) -> dict:
    """
    Verify that two firms use completely different database files.
    Returns a dict with isolation status and db paths.
    """
    pdb = _get_platform_db(platform_db_path)
    firm_a = pdb.get_firm(firm_id_a)
    firm_b = pdb.get_firm(firm_id_b)

    if not firm_a:
        return {"isolated": False, "error": f"Firm {firm_id_a} not found"}
    if not firm_b:
        return {"isolated": False, "error": f"Firm {firm_id_b} not found"}

    path_a = os.path.realpath(firm_a["db_path"])
    path_b = os.path.realpath(firm_b["db_path"])
    isolated = path_a != path_b

    return {
        "isolated": isolated,
        "firm_a": {"firm_id": firm_id_a, "db_path": path_a},
        "firm_b": {"firm_id": firm_id_b, "db_path": path_b},
    }
