"""Portal URL helpers — Ledger is SSO-only; identity lives in the Portal."""
import logging
import os
from urllib.parse import quote, urlparse

logger = logging.getLogger(__name__)

# Live portal application (Vercel). nexallegal.co.uk is parked — not the portal app.
LIVE_PORTAL_URL = "https://nexal-legal.vercel.app"
DEFAULT_PORTAL_URL = LIVE_PORTAL_URL

# Domains that must never be used for portal redirects (IONOS parked / marketing only).
BLOCKED_PORTAL_HOSTS = frozenset(
    {
        "nexallegal.co.uk",
        "www.nexallegal.co.uk",
    },
)


def _host_from_url(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return (parsed.hostname or "").lower()


def resolve_portal_base_url() -> str:
    """
    Single source of truth for Portal base URL used in all Ledger redirects.

    Rejects the parked nexallegal.co.uk domain even when set via NEXAL_PORTAL_URL.
    """
    raw = (
        os.environ.get("NEXAL_PORTAL_URL")
        or os.environ.get("PORTAL_APP_URL")
        or DEFAULT_PORTAL_URL
    ).strip().rstrip("/")

    host = _host_from_url(raw)
    if host in BLOCKED_PORTAL_HOSTS:
        logger.warning(
            "Portal URL %s uses parked domain %s; redirecting via %s instead",
            raw,
            host,
            LIVE_PORTAL_URL,
        )
        return LIVE_PORTAL_URL.rstrip("/")

    return raw


def get_portal_base_url() -> str:
    """Return configured Portal base URL (no trailing slash)."""
    return resolve_portal_base_url()


def get_portal_login_url(next_path: str = None, reason: str = None) -> str:
    """Full URL for Portal sign-in."""
    url = f"{get_portal_base_url()}/login"
    params = []
    if next_path:
        params.append(f"next={quote(next_path, safe='')}")
    if reason:
        params.append(f"reason={quote(reason, safe='')}")
    if params:
        url += "?" + "&".join(params)
    return url


def get_portal_dashboard_url() -> str:
    """Full URL for Portal dashboard (Open Portal links from Ledger)."""
    return f"{get_portal_base_url()}/portal"


def get_portal_users_url() -> str:
    """Full URL for Portal team / user management."""
    return f"{get_portal_base_url()}/portal/users"


def get_portal_logout_url() -> str:
    """Public Nexal Legal homepage — safe post-logout landing."""
    return f"{get_portal_base_url()}/"


def portal_login_redirect(next_path: str = None, reason: str = None):
    """Flask redirect to Portal login."""
    from flask import redirect

    return redirect(get_portal_login_url(next_path, reason))


def portal_logout_redirect():
    """Flask redirect to public Portal site after Ledger logout."""
    from flask import redirect

    return redirect(get_portal_logout_url())
