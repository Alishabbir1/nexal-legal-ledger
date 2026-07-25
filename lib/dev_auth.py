"""
lib/dev_auth.py - Development-only authentication helpers.

These utilities are ONLY active when NEXAL_DEV=1 is set and NEXAL_PRODUCTION is
not set.  They are never executed on the VPS and have zero effect on production data,
sessions or databases.

Purpose: allow developers to log in directly to the local Ledger without going through
the Vercel Portal, so that every feature can be tested in full isolation before deploying.
"""
import logging
import os
from urllib.parse import quote

logger = logging.getLogger(__name__)


def is_dev_mode() -> bool:
    """
    Return True only in an explicit local development environment.

    Both conditions must hold:
      - NEXAL_DEV=1 is set in the environment
      - NEXAL_PRODUCTION is NOT set (or not truthy)

    This double-guard ensures the dev bypass can never activate on the VPS.
    """
    if os.environ.get("NEXAL_PRODUCTION", "").strip().lower() in ("1", "true", "yes"):
        return False
    return os.environ.get("NEXAL_DEV", "").strip() == "1"


def get_dev_login_url(next_path: str = None) -> str:
    """Return the local dev login URL with an optional next-path parameter."""
    url = "/dev/login"
    if next_path:
        url += f"?next={quote(next_path, safe='')}"
    return url
