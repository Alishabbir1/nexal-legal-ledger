"""
Firm provisioning — register firm, workspace, and isolated ledger database.
"""
import re
from typing import Any

from nexal_platform.config import get_platform_paths
from nexal_platform.platform_db import PlatformDatabase
from nexal_platform.template import clone_template_to_firm_db, ensure_template_database


_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


def _validate_slug(slug: str) -> str:
    normalized = slug.strip().lower()
    if not _SLUG_RE.match(normalized):
        raise ValueError(
            "Slug must be 2–64 lowercase letters, numbers, or hyphens, "
            "and cannot start or end with a hyphen."
        )
    return normalized


def provision_firm(
    name: str,
    slug: str,
    firm_code: str | None = None,
    owner_email: str | None = None,
    portal_firm_id: str | None = None,
    portal_user_id: str | None = None,
) -> dict[str, Any]:
    """
    Provision a new law firm tenant.

    Steps:
    1. Register firm in platform.db
    2. Clone template database to tenants/{firm_id}/ledger.db
    3. Register workspace with database path
    4. Optionally register platform user with portal_user_id
    """
    if not name.strip():
        raise ValueError("Firm name is required.")

    normalized_slug = _validate_slug(slug)
    paths = get_platform_paths()
    platform = PlatformDatabase(paths)

    if platform.get_firm_by_slug(normalized_slug):
        raise ValueError(f"A firm with slug '{normalized_slug}' already exists.")

    if firm_code and platform.get_firm_by_code(firm_code):
        raise ValueError(f"A firm with code '{firm_code}' already exists.")

    template_path = ensure_template_database(paths)
    firm = platform.create_firm(
        name=name,
        slug=normalized_slug,
        firm_code=firm_code,
        portal_firm_id=portal_firm_id,
    )
    firm_id = firm["id"]
    tenant_db_path = paths.tenant_db_path(firm_id)

    try:
        clone_template_to_firm_db(template_path, tenant_db_path)
        workspace = platform.create_workspace(firm_id=firm_id, database_path=tenant_db_path)
    except Exception:
        _rollback_partial_provision(platform, firm_id, tenant_db_path)
        raise

    platform_user = None
    if owner_email:
        platform_user = platform.create_user(
            firm_id=firm_id,
            email=owner_email,
            portal_user_id=portal_user_id,
        )

    return {
        "firm": firm,
        "workspace": workspace,
        "platform_user": platform_user,
        "database_path": tenant_db_path,
    }


def _rollback_partial_provision(platform: PlatformDatabase, firm_id: str, tenant_db_path: str) -> None:
    """Best-effort cleanup when provisioning fails mid-flight."""
    import os

    conn = platform.get_connection()
    try:
        conn.execute("DELETE FROM workspaces WHERE firm_id = ?", (firm_id,))
        conn.execute("DELETE FROM users WHERE firm_id = ?", (firm_id,))
        conn.execute("DELETE FROM firms WHERE id = ?", (firm_id,))
        conn.commit()
    finally:
        conn.close()

    if os.path.isfile(tenant_db_path):
        os.remove(tenant_db_path)
    tenant_dir = os.path.dirname(tenant_db_path)
    if os.path.isdir(tenant_dir) and not os.listdir(tenant_dir):
        os.rmdir(tenant_dir)
