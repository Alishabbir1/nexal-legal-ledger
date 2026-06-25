"""
Phase 4B — link portal firms to ledger platform tenants.
"""
import logging
import os
import re
import shutil
import sqlite3
from typing import Any, Dict, Optional

from nexal_platform.config import (
    get_platform_paths,
    is_forbidden_runtime_path,
    resolve_workspace_database_path,
)
from nexal_platform.platform_db import PlatformDatabase
from nexal_platform.provision import provision_firm
from nexal_platform.template import clone_template_to_firm_db, ensure_template_database

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


def slug_from_portal_firm(name: str, portal_firm_id: str) -> str:
    """Derive a unique, valid tenant slug from portal firm metadata."""
    normalized = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    if len(normalized) < 2:
        normalized = "firm"
    suffix = portal_firm_id.replace("-", "")[:8].lower()
    candidate = f"{normalized}-{suffix}"[:64].strip("-")
    if not _SLUG_RE.match(candidate):
        candidate = f"firm-{suffix}"
    return candidate


def _lookup_portal_firm(platform: PlatformDatabase, portal_firm_id: str) -> Optional[Dict[str, Any]]:
    firm = platform.get_firm_by_portal_firm_id(portal_firm_id)
    if firm is not None:
        return firm
    try:
        return platform.get_firm(portal_firm_id)
    except KeyError:
        return None


def _tenant_database_is_valid(db_path: str) -> bool:
    """Return True when tenant DB exists and has the ledger users schema."""
    if is_forbidden_runtime_path(db_path):
        return False
    try:
        if not os.path.isfile(db_path):
            return False
    except OSError:
        return False
    try:
        if os.path.getsize(db_path) < 512:
            return False
    except OSError:
        return False

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def ensure_firm_tenant_ready(
    platform: PlatformDatabase,
    firm: Dict[str, Any],
    jwt_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Ensure platform firm has an active workspace and a valid tenant database file.

    Repairs orphaned platform records (firm without workspace, or missing/corrupt DB).
    """
    paths = platform.paths
    firm_id = firm["id"]
    template_path = ensure_template_database(paths)

    try:
        workspace = platform.get_workspace_for_firm(firm_id)
    except KeyError:
        logger.warning(
            "Repairing missing workspace for portal firm %s (platform firm %s)",
            firm.get("portal_firm_id"),
            firm_id,
        )
        tenant_db_path = paths.tenant_db_path(firm_id)
        _ensure_tenant_database_file(template_path, tenant_db_path, allow_repair=True)
        workspace = platform.create_workspace(firm_id=firm_id, database_path=tenant_db_path)
        _finalize_repaired_tenant(firm, tenant_db_path, jwt_payload)
        return workspace

    db_path = resolve_workspace_database_path(
        platform,
        firm_id,
        workspace["database_path"],
        paths,
    )
    if not _tenant_database_is_valid(db_path):
        logger.warning(
            "Repairing invalid tenant database for portal firm %s at %s",
            firm.get("portal_firm_id"),
            db_path,
        )
        _ensure_tenant_database_file(template_path, db_path, allow_repair=True)
        _finalize_repaired_tenant(firm, db_path, jwt_payload)

    if jwt_payload:
        tier = (
            jwt_payload.get("subscription_tier")
            or jwt_payload.get("package")
            or jwt_payload.get("plan")
        )
        if tier:
            try:
                platform.update_firm_subscription_tier(firm_id, str(tier))
            except Exception as exc:
                logger.warning("Could not sync subscription tier for firm %s: %s", firm_id, exc)

    return platform.get_workspace_for_firm(firm_id)


def _finalize_repaired_tenant(
    firm: Dict[str, Any],
    db_path: str,
    jwt_payload: Optional[Dict[str, Any]] = None,
) -> None:
    """Mark repaired tenant DB as provisioned and cache subscription tier."""
    from database import Database
    from lib.firm_package import cache_tier_in_tenant_db

    tier = (
        (jwt_payload or {}).get("subscription_tier")
        or (jwt_payload or {}).get("package")
        or firm.get("subscription_tier")
        or "essential"
    )
    tenant_db = Database(db_path=db_path, skip_user_seed=True)
    cache_tier_in_tenant_db(tenant_db, str(tier))
    tenant_db.set_config(
        "provisioned_tenant",
        "1",
        "Multi-tenant firm database — repaired by portal SSO link",
    )


def _ensure_tenant_database_file(
    template_path: str,
    target_path: str,
    *,
    allow_repair: bool = False,
) -> str:
    if _tenant_database_is_valid(target_path):
        return target_path

    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    if os.path.exists(target_path):
        if not allow_repair:
            raise ValueError(f"Tenant database invalid: {target_path}")
        if os.path.isdir(target_path):
            shutil.rmtree(target_path)
        else:
            os.remove(target_path)

    return clone_template_to_firm_db(template_path, target_path)


def ensure_portal_firm_linked(
    portal_firm_id: str,
    jwt_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Resolve a portal firms.id to a platform firm, auto-provisioning on first SSO.
    """
    platform = PlatformDatabase()
    firm = _lookup_portal_firm(platform, portal_firm_id)
    if firm is not None:
        ensure_firm_tenant_ready(platform, firm, jwt_payload)
        return firm

    if jwt_payload is None:
        raise ValueError("Firm not found for portal firm id: " + portal_firm_id)

    name = (jwt_payload.get("firm_name") or "").strip() or ("Portal Firm " + portal_firm_id[:8])
    slug = slug_from_portal_firm(name, portal_firm_id)
    email = (jwt_payload.get("email") or "").strip() or None
    portal_user_id = jwt_payload.get("sub")
    subscription_tier = (
        jwt_payload.get("subscription_tier")
        or jwt_payload.get("package")
        or jwt_payload.get("plan")
        or "essential"
    )

    try:
        result = provision_firm(
            name=name,
            slug=slug,
            portal_firm_id=portal_firm_id,
            owner_email=email,
            portal_user_id=portal_user_id,
            subscription_tier=subscription_tier,
        )
        return result["firm"]
    except ValueError:
        firm = _lookup_portal_firm(platform, portal_firm_id)
        if firm is not None:
            ensure_firm_tenant_ready(platform, firm, jwt_payload)
            return firm
        raise


def resolve_active_portal_firm(
    portal_firm_id: str,
    jwt_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve portal firm and ensure firm/workspace/tenant DB are active."""
    platform = PlatformDatabase()
    firm = ensure_portal_firm_linked(portal_firm_id, jwt_payload)
    ensure_firm_tenant_ready(platform, firm, jwt_payload)
    if firm["status"] != "active":
        raise ValueError("Firm is not active (status: " + str(firm["status"]) + ")")
    workspace = platform.get_workspace_for_firm(firm["id"])
    if workspace["status"] != "active":
        raise ValueError("Workspace is not active for firm: " + firm["id"])
    return firm
