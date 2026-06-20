"""
Resolve users across legacy and per-firm tenant databases for internal lookup.

SSO users live in isolated tenant databases. Legacy helpers remain for
provisioning and internal resolution only — Ledger has no direct login UI.
instead of only the legacy default database.
"""
import logging
import sqlite3
from typing import Optional, Tuple

from database import Database
from db_router import get_db_for_firm, get_router

logger = logging.getLogger(__name__)

AuthResult = Tuple[dict, Optional[str], Database]

_TENANT_DB_ERRORS = (KeyError, PermissionError, OSError, sqlite3.OperationalError, sqlite3.DatabaseError)


def _legacy_database() -> Database:
    from app import _legacy_db

    return _legacy_db


def _safe_tenant_db(firm_id: Optional[str]) -> Optional[Database]:
    if not firm_id:
        return None
    try:
        return get_db_for_firm(firm_id)
    except _TENANT_DB_ERRORS as exc:
        logger.warning("Skipping tenant database %s: %s", firm_id, exc)
        return None


def _iter_tenant_databases():
    router = get_router()
    try:
        firms = router.platform.list_firms()
    except _TENANT_DB_ERRORS as exc:
        logger.warning("Unable to list platform firms: %s", exc)
        return
    for firm in firms:
        if firm.get("status") != "active":
            continue
        firm_id = firm.get("id")
        tenant_db = _safe_tenant_db(firm_id)
        if tenant_db is not None:
            yield firm_id, tenant_db


def resolve_user_for_login(
    identifier: str,
    hint_firm_id: Optional[str] = None,
) -> Optional[AuthResult]:
    """
    Find an active user by username or email for direct Ledger login.

    Returns (user, firm_id, database). firm_id is None for legacy-only users.
    """
    ident = (identifier or "").strip().lower()
    if not ident:
        return None

    if hint_firm_id:
        tenant_db = _safe_tenant_db(hint_firm_id)
        if tenant_db is not None:
            match = _lookup_active_user(tenant_db, ident)
            if match:
                return match, hint_firm_id, tenant_db

    for firm_id, tenant_db in _iter_tenant_databases():
        match = _lookup_active_user(tenant_db, ident)
        if match:
            return match, firm_id, tenant_db

    legacy = _legacy_database()
    try:
        match = _lookup_active_user(legacy, ident)
    except _TENANT_DB_ERRORS as exc:
        logger.warning("Legacy login lookup failed: %s", exc)
        match = None
    if match:
        firm_id = match.get("firm_id") or None
        if firm_id:
            tenant_db = _safe_tenant_db(firm_id)
            if tenant_db is not None:
                return match, firm_id, tenant_db
        return match, None, legacy

    return None


def resolve_admin_for_recovery(
    identifier: str,
    hint_firm_id: Optional[str] = None,
) -> Optional[AuthResult]:
    """
    Find an admin user by username or email for the recovery-key flow.

    Returns (admin_user, firm_id, database). firm_id is None for legacy admins.
    """
    ident = (identifier or "").strip().lower()
    if not ident:
        return None

    if hint_firm_id:
        tenant_db = _safe_tenant_db(hint_firm_id)
        if tenant_db is not None:
            admin = _lookup_admin(tenant_db, ident)
            if admin:
                return admin, hint_firm_id, tenant_db

    for firm_id, tenant_db in _iter_tenant_databases():
        admin = _lookup_admin(tenant_db, ident)
        if admin:
            return admin, firm_id, tenant_db

    legacy = _legacy_database()
    try:
        admin = _lookup_admin(legacy, ident)
    except _TENANT_DB_ERRORS as exc:
        logger.warning("Legacy recovery lookup failed: %s", exc)
        admin = None
    if admin:
        firm_id = admin.get("firm_id") or None
        if firm_id:
            tenant_db = _safe_tenant_db(firm_id)
            if tenant_db is not None:
                return admin, firm_id, tenant_db
        return admin, None, legacy

    return None


def _lookup_active_user(db: Database, identifier: str) -> Optional[dict]:
    try:
        user = db.get_user_by_login_identifier(identifier)
    except _TENANT_DB_ERRORS as exc:
        logger.warning("User lookup failed on %s: %s", getattr(db, "db_path", db), exc)
        return None
    if user and user.get("active"):
        return user
    return None


def _lookup_admin(db: Database, identifier: str) -> Optional[dict]:
    try:
        return db.get_admin_by_login_identifier(identifier)
    except _TENANT_DB_ERRORS as exc:
        logger.warning("Admin lookup failed on %s: %s", getattr(db, "db_path", db), exc)
        return None
