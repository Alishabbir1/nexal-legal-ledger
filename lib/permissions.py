"""
Phase 5.3 — ledger permission helpers for Admin / Staff portal roles.
"""
from typing import Optional

from flask import session

ADMIN_PORTAL_ROLES = frozenset({"firm_admin", "admin", "owner", "practice_manager", "manager"})
LEGACY_ADMIN_PORTAL_ROLES = ADMIN_PORTAL_ROLES


def normalize_portal_role(role: Optional[str]) -> str:
    """Map portal JWT / DB roles to admin or staff."""
    value = (role or "staff").strip().lower()
    if value in ADMIN_PORTAL_ROLES:
        return "admin"
    return "staff"


def get_portal_role() -> str:
    return normalize_portal_role(session.get("portal_role"))


def get_ledger_role() -> str:
    return (session.get("role") or "").strip().lower()


def is_read_only_user() -> bool:
    raw = (session.get("portal_role") or "").strip().lower()
    return raw == "read_only"


def can_modify_ledger_data(role: str = None, portal_role: str = None) -> bool:
    """Staff and Admin may create or amend operational ledger records."""
    if portal_role is not None and str(portal_role).strip().lower() == "read_only":
        return False
    if portal_role is None and is_read_only_user():
        return False
    ledger_role = (role or get_ledger_role()) or "staff"
    return ledger_role in ("admin", "staff")


def can_access_admin_functions(role: str = None, portal_role: str = None) -> bool:
    """Ledger administration — portal Admin only."""
    pr = normalize_portal_role(portal_role or get_portal_role())
    ledger_role = (role or get_ledger_role()) or "staff"
    return pr == "admin" and ledger_role == "admin"


def can_access_financial_functions(portal_role: str = None) -> bool:
    """Client ledger, cashbook, reconciliation."""
    raw = (portal_role if portal_role is not None else session.get("portal_role") or "").strip().lower()
    if raw == "read_only":
        return True
    return normalize_portal_role(portal_role or get_portal_role()) in ("admin", "staff")


def can_access_client_operations(portal_role: str = None) -> bool:
    """Client/matter operations."""
    raw = (portal_role if portal_role is not None else session.get("portal_role") or "").strip().lower()
    if raw == "read_only":
        return True
    return normalize_portal_role(portal_role or get_portal_role()) in ("admin", "staff")


def can_edit_client_details(portal_role: str = None) -> bool:
    if is_read_only_user() or (portal_role or "").strip().lower() == "read_only":
        return False
    return can_access_client_operations(portal_role)


def can_access_reports(portal_role: str = None) -> bool:
    return normalize_portal_role(portal_role or get_portal_role()) in ("admin", "staff")
