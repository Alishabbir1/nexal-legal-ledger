"""Portal URL helpers — Ledger is SSO-only; identity lives in the Portal."""
import os
from urllib.parse import quote

DEFAULT_PORTAL_URL = "https://nexallegal.co.uk"


def get_portal_base_url() -> str:
    """Return configured Portal base URL (no trailing slash)."""
    url = (
        os.environ.get("NEXAL_PORTAL_URL")
        or os.environ.get("PORTAL_APP_URL")
        or DEFAULT_PORTAL_URL
    ).strip().rstrip("/")
    return url


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
    """Full URL for Portal dashboard (post-logout landing)."""
    return f"{get_portal_base_url()}/portal"


def get_portal_users_url() -> str:
    """Full URL for Portal team / user management."""
    return f"{get_portal_base_url()}/portal/users"


def portal_login_redirect(next_path: str = None, reason: str = None):
    """Flask redirect to Portal login."""
    from flask import redirect

    return redirect(get_portal_login_url(next_path, reason))


def portal_logout_redirect():
    """Flask redirect to Portal dashboard after Ledger logout."""
    from flask import redirect

    return redirect(get_portal_dashboard_url())
