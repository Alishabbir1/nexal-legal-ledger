"""
portal_bridge.py - Phase 4B: Portal to Ledger identity bridge.
"""
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from werkzeug.security import generate_password_hash

from db_router import get_db_for_firm
from nexal_platform.platform_db import PlatformDatabase
from nexal_platform.portal_link import resolve_active_portal_firm
from sso_auth import (
    build_session_from_token,
    map_portal_role_to_ledger,
    validate_sso_token,
)

logger = logging.getLogger(__name__)

ROLE_MAP = {
    "firm_admin": "admin",
    "admin": "admin",
    "cashier": "staff",
    "staff": "staff",
    "read_only": "staff",
}


def resolve_platform_firm(
    portal_firm_id: str,
    jwt_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Map portal firm id to platform firm record."""
    return resolve_active_portal_firm(portal_firm_id, jwt_payload)


def _derive_username(email: str, portal_user_id: str, preferred: Optional[str] = None) -> str:
    if preferred:
        return preferred.strip().lower()
    local = email.split("@")[0].strip().lower()
    return local or ("user-" + portal_user_id[:8])


def resolve_portal_user(
    email: str,
    portal_user_id: str,
    platform_firm_id: str,
    preferred_username: Optional[str] = None,
) -> Dict[str, Any]:
    """Find existing ledger user linked to portal identity."""
    db = get_db_for_firm(platform_firm_id)
    conn = db.get_connection()
    try:
        row = conn.execute(
            """
            SELECT user_id, username, role, firm_id, portal_user_id, email
            FROM users
            WHERE portal_user_id = ?
            """,
            (portal_user_id,),
        ).fetchone()
        if row:
            return dict(row)

        if email:
            row = conn.execute(
                """
                SELECT user_id, username, role, firm_id, portal_user_id, email
                FROM users
                WHERE lower(email) = lower(?)
                """,
                (email,),
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE users
                    SET portal_user_id = ?, firm_id = ?, email = ?
                    WHERE user_id = ?
                    """,
                    (portal_user_id, platform_firm_id, email.lower(), row["user_id"]),
                )
                conn.commit()
                updated = conn.execute(
                    "SELECT user_id, username, role, firm_id, portal_user_id, email FROM users WHERE user_id = ?",
                    (row["user_id"],),
                ).fetchone()
                return dict(updated)
    finally:
        conn.close()
    raise LookupError("Portal user not found in ledger database")


def provision_portal_user(
    email: str,
    portal_user_id: str,
    platform_firm_id: str,
    portal_role: str = "firm_admin",
    preferred_username: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a ledger user automatically from portal identity."""
    db = get_db_for_firm(platform_firm_id)
    db.initialize_security_columns()
    username = _derive_username(email, portal_user_id, preferred_username)
    ledger_role = map_portal_role_to_ledger(portal_role)
    password_hash = generate_password_hash(secrets.token_urlsafe(32), method="scrypt")

    conn = db.get_connection()
    try:
        existing = conn.execute(
            "SELECT user_id FROM users WHERE lower(username) = lower(?)",
            (username,),
        ).fetchone()
        if existing:
            username = username + "-" + portal_user_id[:6]

        conn.execute(
            """
            INSERT INTO users (
                username, password_hash, role, active,
                portal_user_id, email, firm_id, portal_role, temporary_password
            ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, 0)
            """,
            (
                username,
                password_hash,
                ledger_role,
                portal_user_id,
                email.lower(),
                platform_firm_id,
                portal_role,
            ),
        )
        conn.commit()
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute(
            "SELECT user_id, username, role, firm_id, portal_user_id, email FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def ensure_portal_user_in_ledger(payload: Dict[str, Any], platform_firm_id: str) -> Dict[str, Any]:
    """Resolve or auto-provision portal user in the firm ledger database."""
    portal_user_id = payload["sub"]
    email = payload["email"]
    portal_role = payload.get("role", "firm_admin")
    preferred_username = payload.get("username")

    try:
        return resolve_portal_user(email, portal_user_id, platform_firm_id, preferred_username)
    except LookupError:
        return provision_portal_user(
            email,
            portal_user_id,
            platform_firm_id,
            portal_role,
            preferred_username,
        )


def establish_sso_session(flask_session, jwt_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate firm, resolve user, and populate Flask session."""
    platform_firm = resolve_platform_firm(str(jwt_payload["firm_id"]), jwt_payload)
    platform_firm_id = platform_firm["id"]
    ledger_user = ensure_portal_user_in_ledger(jwt_payload, platform_firm_id)
    jwt_payload["username"] = ledger_user["username"]
    session_data = build_session_from_token(jwt_payload, ledger_user["user_id"], platform_firm_id)
    for key, value in session_data.items():
        flask_session[key] = value
    flask_session["sso_established_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return session_data


def clear_sso_session(flask_session) -> None:
    for key in (
        "user_id",
        "username",
        "email",
        "firm_id",
        "role",
        "portal_user_id",
        "portal_role",
        "sso_login",
        "logged_in",
        "sso_established_at",
    ):
        flask_session.pop(key, None)


def validate_sso_request(token: str) -> Dict[str, Any]:
    payload = validate_sso_token(token)
    resolve_platform_firm(str(payload["firm_id"]), payload)
    return payload


def log_sso_audit(db, username: str, role: str, action: str, details: str) -> None:
    try:
        db.insert_audit_log(username, role, action, "Authentication", None, details)
    except Exception as exc:
        logger.warning("SSO audit log failed: %s", exc)
