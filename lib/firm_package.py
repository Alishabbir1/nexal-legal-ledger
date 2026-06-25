"""
Resolve firm subscription tier from platform registry and enforce user limits.
"""
from typing import Any, Dict, Optional

from lib.subscription_packages import (
    DEFAULT_TIER,
    can_add_billable_user,
    max_users_for_tier,
    normalize_tier,
    package_display_label,
    user_limit_message,
)
from nexal_platform.platform_db import PlatformDatabase

FIRM_SUBSCRIPTION_CONFIG_KEY = "firm_subscription_tier"
FIRM_MAX_USERS_CONFIG_KEY = "firm_max_users"


def get_platform_firm_tier(platform_firm_id: str) -> str:
    """Read subscription tier from platform.db firms record."""
    platform = PlatformDatabase()
    firm = platform.get_firm(platform_firm_id)
    return normalize_tier(firm.get("subscription_tier"))


def cache_tier_in_tenant_db(db, tier: str) -> None:
    """Persist tier in tenant system_config for offline / legacy reads."""
    db.set_config(
        FIRM_SUBSCRIPTION_CONFIG_KEY,
        normalize_tier(tier),
        "Firm subscription tier (synced from Operations Portal)",
    )


def resolve_firm_tier(session: Dict[str, Any], db) -> str:
    """
    Resolve tier for the active request.

    Prefer platform registry when SSO firm_id is present; fall back to tenant cache.
    """
    firm_id = session.get("firm_id")
    if firm_id:
        try:
            tier = get_platform_firm_tier(firm_id)
            cache_tier_in_tenant_db(db, tier)
            return tier
        except KeyError:
            pass
    cached = db.get_config(FIRM_SUBSCRIPTION_CONFIG_KEY)
    return normalize_tier(cached or DEFAULT_TIER)


def resolve_package_display_for_request(session: Dict[str, Any], db) -> str:
    return package_display_label(resolve_firm_tier(session, db))


def check_user_limit(db, session: Dict[str, Any]) -> Optional[str]:
    """Return validation message when at package user limit, else None.

    Uses Portal-enforced max_users from system_config when available.
    """
    tier = resolve_firm_tier(session, db)
    active_count = db.count_billable_active_users()
    max_users = resolve_max_users(db, tier)
    if active_count >= max_users:
        return user_limit_message(tier)
    return None


def resolve_max_users(db, tier: str) -> int:
    """
    Resolve the effective max-users for the current firm.

    Prefers the Portal-enforced value cached in system_config (which accounts
    for admin overrides) over the hardcoded package default.
    """
    cached = db.get_config(FIRM_MAX_USERS_CONFIG_KEY)
    if cached:
        try:
            value = int(cached)
            if value > 0:
                return value
        except (ValueError, TypeError):
            pass
    return max_users_for_tier(tier)


def package_usage_summary(db, session: Dict[str, Any]) -> Dict[str, Any]:
    tier = resolve_firm_tier(session, db)
    active_count = db.count_billable_active_users()
    max_users = resolve_max_users(db, tier)
    return {
        "tier": tier,
        "label": package_display_label(tier),
        "active_users": active_count,
        "max_users": max_users,
        "at_limit": active_count >= max_users,
    }


def sync_subscription_from_portal(platform_firm_id: str, tier: str) -> str:
    """
    Apply subscription tier from Operations Portal (future webhook / SSO sync).

    Updates platform registry and tenant system_config cache.
    """
    normalized = normalize_tier(tier)
    platform = PlatformDatabase()
    platform.update_firm_subscription_tier(platform_firm_id, normalized)
    from db_router import get_db_for_firm

    tenant_db = get_db_for_firm(platform_firm_id)
    cache_tier_in_tenant_db(tenant_db, normalized)
    return normalized
