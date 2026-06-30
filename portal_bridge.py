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
    "owner": "admin",
    "practice_manager": "admin",
    "manager": "admin",
    "staff": "staff",
    "cashier": "staff",
    "fee_earner": "staff",
    "solicitor": "staff",
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


def _sync_portal_user_roles(
    conn,
    user_id: int,
    platform_firm_id: str,
    portal_role: str,
    full_name: Optional[str] = None,
) -> None:
    """Keep ledger role columns and display name aligned with Portal SSO on each login."""
    ledger_role = map_portal_role_to_ledger(portal_role)
    if full_name:
        conn.execute(
            """
            UPDATE users
            SET role = ?, portal_role = ?, firm_id = ?, active = 1, full_name = ?
            WHERE user_id = ?
            """,
            (ledger_role, portal_role, platform_firm_id, full_name, user_id),
        )
    else:
        conn.execute(
            """
            UPDATE users
            SET role = ?, portal_role = ?, firm_id = ?, active = 1
            WHERE user_id = ?
            """,
            (ledger_role, portal_role, platform_firm_id, user_id),
        )
    conn.commit()


def _may_relink_stale_portal_user_id(
    existing_portal_user_id: str,
    incoming_portal_user_id: str,
    portal_customer_id: Optional[str],
) -> bool:
    """
    Allow relink when ledger still stores customers.id from early launch tokens
    and the portal now sends firm_users.id for the same authenticated customer.
    """
    if str(existing_portal_user_id) == str(incoming_portal_user_id):
        return False
    if not portal_customer_id:
        return False
    return str(existing_portal_user_id) == str(portal_customer_id)


def resolve_portal_user(
    email: str,
    portal_user_id: str,
    platform_firm_id: str,
    preferred_username: Optional[str] = None,
    portal_role: Optional[str] = None,
    password_hash: Optional[str] = None,
    portal_customer_id: Optional[str] = None,
    full_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Find existing ledger user linked to portal identity."""
    from lib.portal_password_sync import sync_portal_password_hash

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
            if portal_role:
                _sync_portal_user_roles(
                    conn, row["user_id"], platform_firm_id, portal_role, full_name
                )
                row = conn.execute(
                    """
                    SELECT user_id, username, role, firm_id, portal_user_id, email
                    FROM users WHERE user_id = ?
                    """,
                    (row["user_id"],),
                ).fetchone()
            sync_portal_password_hash(db, row["user_id"], password_hash)
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
                existing_portal_user_id = row["portal_user_id"]
                if existing_portal_user_id and str(existing_portal_user_id) != str(portal_user_id):
                    if _may_relink_stale_portal_user_id(
                        str(existing_portal_user_id),
                        str(portal_user_id),
                        portal_customer_id,
                    ):
                        logger.warning(
                            "Relinking stale portal_user_id for %s: %s -> %s",
                            email,
                            existing_portal_user_id,
                            portal_user_id,
                        )
                        conn.execute(
                            """
                            UPDATE users
                            SET portal_user_id = ?, firm_id = ?, email = ?
                            WHERE user_id = ?
                            """,
                            (
                                portal_user_id,
                                platform_firm_id,
                                email.lower(),
                                row["user_id"],
                            ),
                        )
                        conn.commit()
                    else:
                        raise LookupError("Portal user email conflict in ledger database")
                elif not existing_portal_user_id:
                    conn.execute(
                        """
                        UPDATE users
                        SET portal_user_id = ?, firm_id = ?, email = ?
                        WHERE user_id = ?
                        """,
                        (portal_user_id, platform_firm_id, email.lower(), row["user_id"]),
                    )
                    conn.commit()

                if portal_role:
                    _sync_portal_user_roles(
                        conn, row["user_id"], platform_firm_id, portal_role, full_name
                    )
                updated = conn.execute(
                    "SELECT user_id, username, role, firm_id, portal_user_id, email FROM users WHERE user_id = ?",
                    (row["user_id"],),
                ).fetchone()
                sync_portal_password_hash(db, updated["user_id"], password_hash)
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
    password_hash: Optional[str] = None,
    full_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a ledger user automatically from portal identity.

    User-limit enforcement is the sole responsibility of the Portal.
    The Ledger provisions any user that arrives via a valid SSO token.
    """
    from lib.portal_password_sync import is_valid_password_hash, sync_portal_password_hash

    db = get_db_for_firm(platform_firm_id)
    db.initialize_security_columns()

    username = _derive_username(email, portal_user_id, preferred_username)
    ledger_role = map_portal_role_to_ledger(portal_role)
    if is_valid_password_hash(password_hash or ""):
        stored_hash = password_hash.strip()
    else:
        stored_hash = generate_password_hash(secrets.token_urlsafe(32), method="scrypt")

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
                portal_user_id, email, firm_id, portal_role, temporary_password, full_name
            ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, 0, ?)
            """,
            (
                username,
                stored_hash,
                ledger_role,
                portal_user_id,
                email.lower(),
                platform_firm_id,
                portal_role,
                full_name,
            ),
        )
        conn.commit()
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        sync_portal_password_hash(db, user_id, password_hash)
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
    portal_password_hash = payload.get("password_hash")
    portal_customer_id = payload.get("portal_customer_id")

    first_name = (payload.get("first_name") or "").strip()
    last_name = (payload.get("last_name") or "").strip()
    full_name: Optional[str] = None
    if first_name or last_name:
        full_name = " ".join(filter(None, [first_name, last_name]))

    try:
        return resolve_portal_user(
            email,
            portal_user_id,
            platform_firm_id,
            preferred_username,
            portal_role,
            portal_password_hash,
            portal_customer_id,
            full_name,
        )
    except LookupError as exc:
        if "conflict" in str(exc).lower():
            raise
        return provision_portal_user(
            email,
            portal_user_id,
            platform_firm_id,
            portal_role,
            preferred_username,
            portal_password_hash,
            full_name,
        )


def establish_sso_session(flask_session, jwt_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate firm, resolve user, and populate Flask session."""
    account_status = str(jwt_payload.get("account_status") or "ACTIVE").strip().upper()
    if account_status != "ACTIVE":
        raise ValueError("Firm account is not active for ledger access.")

    from lib.firm_package import cache_tier_in_tenant_db, resolve_firm_tier
    from lib.sso_trace import log_sso_detail, sso_stage
    from nexal_platform.config import get_runtime_data_root, repair_all_stale_workspace_paths

    email = jwt_payload.get("email")
    portal_firm_id = str(jwt_payload["firm_id"])

    # Repair stale workspace paths before any tenant filesystem access (SSO hot path).
    platform_for_repair = PlatformDatabase()
    repaired = repair_all_stale_workspace_paths(platform_for_repair)
    if repaired:
        logger.warning(
            "Repaired %d stale workspace database_path(s) during SSO for %s",
            repaired,
            email,
        )
        # Invalidate the global TenantRouter cache so repaired paths take effect
        # immediately.  Without this, any Database object created before the repair
        # (pointing at a forbidden path) would continue to be used for the rest of
        # this process lifetime.
        from db_router import clear_router_cache
        clear_router_cache()

    log_sso_detail(
        email,
        "jwt_decoded",
        portal_firm_id=portal_firm_id,
        portal_user_id=jwt_payload.get("sub"),
        data_root=get_runtime_data_root(),
        workspace_paths_repaired=repaired,
    )

    platform_firm = sso_stage(
        email,
        "resolve_platform_firm",
        lambda: resolve_platform_firm(portal_firm_id, jwt_payload),
    )
    platform_firm_id = platform_firm["id"]
    log_sso_detail(
        email,
        "firm_located",
        platform_firm_id=platform_firm_id,
        portal_firm_id=portal_firm_id,
    )

    workspace = sso_stage(
        email,
        "workspace_lookup",
        lambda: PlatformDatabase().get_workspace_for_firm(platform_firm_id),
    )
    log_sso_detail(
        email,
        "database_path",
        workspace_db=workspace.get("database_path"),
        workspace_status=workspace.get("status"),
    )

    ledger_user = sso_stage(
        email,
        "ledger_user_lookup",
        lambda: ensure_portal_user_in_ledger(jwt_payload, platform_firm_id),
    )
    log_sso_detail(
        email,
        "ledger_user_found",
        user_id=ledger_user.get("user_id"),
        username=ledger_user.get("username"),
    )
    jwt_payload["username"] = ledger_user["username"]
    session_data = build_session_from_token(
        jwt_payload,
        ledger_user["user_id"],
        platform_firm_id,
        ledger_role=ledger_user["role"],
    )
    portal_password_hash = jwt_payload.get("password_hash")
    if portal_password_hash:
        session_data["portal_password_hash"] = portal_password_hash
    for key, value in session_data.items():
        flask_session[key] = value
    flask_session["sso_established_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    tenant_db = sso_stage(
        email,
        "tenant_open",
        lambda: get_db_for_firm(platform_firm_id),
    )
    from lib.portal_password_sync import on_sso_login_password_sync

    on_sso_login_password_sync(tenant_db, ledger_user["user_id"], portal_password_hash)
    tenant_db.reset_recovery_confirm_attempts(ledger_user["user_id"])
    tier = resolve_firm_tier(session_data, tenant_db)
    cache_tier_in_tenant_db(tenant_db, tier)

    # Cache Portal-enforced max_users so the Ledger display reflects overrides.
    portal_max_users = jwt_payload.get("max_users")
    if portal_max_users is not None:
        try:
            tenant_db.set_config(
                "firm_max_users",
                str(int(portal_max_users)),
                "Maximum users (Portal-enforced, synced via SSO)",
            )
        except Exception as exc:
            logger.warning("Could not cache max_users for firm %s: %s", platform_firm_id, exc)

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
        "portal_password_hash",
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
