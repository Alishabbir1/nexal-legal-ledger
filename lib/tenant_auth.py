"""
Resolve users across legacy and per-firm tenant databases for direct login and recovery.

SSO users and recovery keys live in isolated tenant databases. Routes that run
without an SSO session (e.g. /login, /admin/recovery) must search those databases
instead of only the legacy default database.
"""
from typing import Optional, Tuple

from database import Database
from db_router import get_db_for_firm, get_router

AuthResult = Tuple[dict, Optional[str], Database]


def _legacy_database() -> Database:
    from app import _legacy_db

    return _legacy_db


def _iter_tenant_databases():
    router = get_router()
    for firm in router.platform.list_firms():
        if firm.get("status") != "active":
            continue
        try:
            yield firm["id"], get_db_for_firm(firm["id"])
        except (KeyError, PermissionError, OSError):
            continue


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
        tenant_db = get_db_for_firm(hint_firm_id)
        match = _lookup_active_user(tenant_db, ident)
        if match:
            return match, hint_firm_id, tenant_db

    legacy = _legacy_database()
    match = _lookup_active_user(legacy, ident)
    if match:
        firm_id = match.get("firm_id") or None
        if firm_id:
            try:
                return match, firm_id, get_db_for_firm(firm_id)
            except (KeyError, PermissionError, OSError):
                pass
        return match, None, legacy

    for firm_id, tenant_db in _iter_tenant_databases():
        match = _lookup_active_user(tenant_db, ident)
        if match:
            return match, firm_id, tenant_db

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
        tenant_db = get_db_for_firm(hint_firm_id)
        admin = tenant_db.get_admin_by_login_identifier(ident)
        if admin:
            return admin, hint_firm_id, tenant_db

    legacy = _legacy_database()
    admin = legacy.get_admin_by_login_identifier(ident)
    if admin:
        firm_id = admin.get("firm_id") or None
        if firm_id:
            try:
                return admin, firm_id, get_db_for_firm(firm_id)
            except (KeyError, PermissionError, OSError):
                pass
        return admin, None, legacy

    for firm_id, tenant_db in _iter_tenant_databases():
        admin = tenant_db.get_admin_by_login_identifier(ident)
        if admin:
            return admin, firm_id, tenant_db

    return None


def _lookup_active_user(db: Database, identifier: str) -> Optional[dict]:
    user = db.get_user_by_login_identifier(identifier)
    if user and user.get("active"):
        return user
    return None
