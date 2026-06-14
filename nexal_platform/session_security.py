"""
Phase 4C — SSO session binding and safe redirect helpers.
"""
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from flask import session as flask_session, url_for


def safe_redirect_target(next_url: Optional[str], default_endpoint: str = "client_ledger") -> str:
    """Allow only same-application relative paths for post-SSO redirects."""
    if not next_url:
        return url_for(default_endpoint)
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc:
        return url_for(default_endpoint)
    if not next_url.startswith("/") or next_url.startswith("//"):
        return url_for(default_endpoint)
    return next_url


def validate_sso_session_binding(session_obj: Dict[str, Any], get_db_for_firm) -> Optional[str]:
    """
    Verify SSO session user_id belongs to the routed tenant database.

    Returns an error message when the session should be invalidated, else None.
    """
    if not session_obj.get("sso_login"):
        return None
    firm_id = session_obj.get("firm_id")
    user_id = session_obj.get("user_id")
    portal_user_id = session_obj.get("portal_user_id")
    if not firm_id or not user_id:
        return "SSO session missing firm or user binding"

    try:
        firm_db = get_db_for_firm(str(firm_id))
        conn = firm_db.get_connection()
        try:
            row = conn.execute(
                """
                SELECT user_id, firm_id, portal_user_id, active
                FROM users
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        return "SSO session tenant routing failed: " + str(exc)

    if row is None:
        return "SSO session user not found in tenant database"
    if not row["active"]:
        return "SSO session user is inactive"
    if row["firm_id"] and str(row["firm_id"]) != str(firm_id):
        return "SSO session firm binding mismatch"
    if (
        portal_user_id
        and row["portal_user_id"]
        and str(row["portal_user_id"]) != str(portal_user_id)
    ):
        return "SSO session portal user binding mismatch"
    return None


def clear_invalid_sso_session(session_obj=flask_session) -> None:
    session_obj.clear()
