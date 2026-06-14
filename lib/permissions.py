"""
Phase 4C — ledger permission helpers for portal-mapped roles.
"""
from flask import session


def get_portal_role() -> str:
    return (session.get("portal_role") or "").strip().lower()


def get_ledger_role() -> str:
    return (session.get("role") or "").strip().lower()


def is_read_only_user() -> bool:
    return get_portal_role() == "read_only"


def can_modify_ledger_data(role: str = None, portal_role: str = None) -> bool:
    """Return True when the current user may create or amend ledger records."""
    if (portal_role or get_portal_role()) == "read_only":
        return False
    return (role or get_ledger_role()) in ("admin", "staff")


def can_access_admin_functions(role: str = None) -> bool:
    return (role or get_ledger_role()) == "admin"


def can_access_financial_functions(portal_role: str = None) -> bool:
    """Cashier and admin roles may perform financial operations."""
    pr = (portal_role or get_portal_role()) or "staff"
    if pr == "read_only":
        return False
    return pr in ("firm_admin", "admin", "cashier", "staff")


def can_access_client_operations(portal_role: str = None) -> bool:
    """Fee earners (staff) and above may manage client/matter operations."""
    pr = (portal_role or get_portal_role()) or "staff"
    if pr == "read_only":
        return False
    return pr in ("firm_admin", "admin", "cashier", "staff")
