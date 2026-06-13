"""
Nexal Legal — Phase 4A multi-tenant platform foundation.

One SQLite database per law firm. Platform metadata lives in platform.db.
"""
from nexal_platform.config import get_platform_paths
from nexal_platform.platform_db import PlatformDatabase
from nexal_platform.provision import provision_firm
from nexal_platform.router import TenantRouter
from nexal_platform.template import ensure_template_database

__all__ = [
    "get_platform_paths",
    "PlatformDatabase",
    "provision_firm",
    "TenantRouter",
    "ensure_template_database",
]
