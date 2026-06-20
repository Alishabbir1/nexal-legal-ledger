"""
Subscription package definitions for Nexal Legal Ledger user limits.

Portal tiers map to these keys via SSO / firm sync (subscription_tier field).
"""
from typing import Dict, Optional

DEFAULT_TIER = "essential"

PACKAGES: Dict[str, Dict] = {
    "essential": {
        "label": "Essential",
        "monthly_gbp": 39,
        "max_users": 2,
    },
    "professional": {
        "label": "Professional",
        "monthly_gbp": 79,
        "max_users": 5,
    },
    "practice_plus": {
        "label": "Practice Plus",
        "monthly_gbp": 149,
        "max_users": 10,
    },
}

# Portal / external aliases → canonical tier keys
_TIER_ALIASES = {
    "essential": "essential",
    "essentials": "essential",
    "starter": "essential",
    "professional": "professional",
    "pro": "professional",
    "practice_plus": "practice_plus",
    "practice-plus": "practice_plus",
    "practiceplus": "practice_plus",
    "plus": "practice_plus",
    "enterprise": "practice_plus",
}


def normalize_tier(tier: Optional[str]) -> str:
    """Return a canonical tier key."""
    if not tier:
        return DEFAULT_TIER
    key = str(tier).strip().lower().replace(" ", "_")
    return _TIER_ALIASES.get(key, key if key in PACKAGES else DEFAULT_TIER)


def max_users_for_tier(tier: Optional[str]) -> int:
    return PACKAGES[normalize_tier(tier)]["max_users"]


def package_display_label(tier: Optional[str]) -> str:
    info = PACKAGES[normalize_tier(tier)]
    return f"{info['label']} (£{info['monthly_gbp']}/month)"


def user_limit_message(tier: Optional[str]) -> str:
    limit = max_users_for_tier(tier)
    return (
        f"Your package allows a maximum of {limit} users. "
        "Please upgrade your package to add additional users."
    )


def can_add_billable_user(active_count: int, tier: Optional[str]) -> bool:
    return active_count < max_users_for_tier(tier)
