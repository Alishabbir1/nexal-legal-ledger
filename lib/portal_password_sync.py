"""
Synchronise Portal password hashes into tenant ledger user records.

Portal authenticates with bcrypt; ledger stores the same hash so password-gated
flows (recovery key generation, force password change) validate against Portal credentials.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_ACCEPTED_HASH_PREFIXES = ("scrypt:", "pbkdf2:", "$2a$", "$2b$", "$2y$")


def is_valid_password_hash(value: str) -> bool:
    normalized = (value or "").strip()
    if not normalized or len(normalized) > 512:
        return False
    return normalized.startswith(_ACCEPTED_HASH_PREFIXES)


def force_sync_portal_password_hash(db, user_id: int, password_hash: Optional[str]) -> bool:
    """
    Always write Portal password hash for an SSO user (repair out-of-sync records).

    Returns True when a valid hash was applied.
    """
    if not password_hash or not is_valid_password_hash(password_hash):
        return False

    normalized = password_hash.strip()
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT user_id, portal_user_id FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            """
            UPDATE users
            SET password_hash = ?, temporary_password = 0
            WHERE user_id = ?
            """,
            (normalized, user_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def sync_portal_password_hash(db, user_id: int, password_hash: Optional[str]) -> bool:
    """
    Copy Portal password hash into ledger users.password_hash when changed.

    Returns True when the ledger record was updated.
    """
    if not password_hash or not is_valid_password_hash(password_hash):
        return False

    normalized = password_hash.strip()
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return False
        if (row["password_hash"] or "") == normalized:
            return False
        conn.execute(
            """
            UPDATE users
            SET password_hash = ?, temporary_password = 0
            WHERE user_id = ?
            """,
            (normalized, user_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def prepare_sso_password_for_verification(db, session) -> None:
    """
    Re-apply Portal password hash from the active SSO session before password checks.

    Ensures recovery-key and security prompts validate against the Portal credential
    even if an earlier sync was missed or the record was created before hash sync existed.
    """
    if not session.get("sso_login"):
        return
    user_id = session.get("user_id")
    portal_hash = session.get("portal_password_hash")
    if not user_id or not portal_hash:
        return
    if force_sync_portal_password_hash(db, user_id, portal_hash):
        logger.info("Repaired SSO password hash for user_id=%s before verification", user_id)


def on_sso_login_password_sync(db, user_id: int, password_hash: Optional[str]) -> None:
    """Apply Portal hash and log when SSO token omits password_hash."""
    if password_hash and is_valid_password_hash(password_hash):
        force_sync_portal_password_hash(db, user_id, password_hash)
        return
    logger.warning(
        "SSO login for user_id=%s without portal password_hash in token; "
        "Ledger password verification may fail until Portal launch sends the hash.",
        user_id,
    )
