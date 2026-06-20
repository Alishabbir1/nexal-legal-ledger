"""
Phase 4B — link portal firms to ledger platform tenants.
"""
import re
from typing import Any, Dict, Optional

from nexal_platform.platform_db import PlatformDatabase
from nexal_platform.provision import provision_firm

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
            return firm
        raise


def resolve_active_portal_firm(
    portal_firm_id: str,
    jwt_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve portal firm and ensure firm/workspace are active."""
    platform = PlatformDatabase()
    firm = ensure_portal_firm_linked(portal_firm_id, jwt_payload)
    if firm["status"] != "active":
        raise ValueError("Firm is not active (status: " + str(firm["status"]) + ")")
    workspace = platform.get_workspace_for_firm(firm["id"])
    if workspace["status"] != "active":
        raise ValueError("Workspace is not active for firm: " + firm["id"])
    return firm
