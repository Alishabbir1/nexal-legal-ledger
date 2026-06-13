"""
Nexal Legal Phase 4A/4B — database routing module.
"""
from typing import Optional

from database import Database
from nexal_platform.router import TenantRouter

_router: Optional[TenantRouter] = None


def reset_router() -> None:
    """Reset cached router (for tests and config reload)."""
    global _router
    _router = None


def get_router() -> TenantRouter:
    global _router
    if _router is None:
        _router = TenantRouter()
    return _router


def get_db_for_firm(firm_id: str) -> Database:
    """Return the isolated ledger Database for a platform firm id."""
    database = get_router().get_database(firm_id)
    database.initialize_security_columns()
    return database


def clear_router_cache() -> None:
    get_router().clear_cache()


__all__ = ["TenantRouter", "get_router", "get_db_for_firm", "clear_router_cache", "reset_router"]
