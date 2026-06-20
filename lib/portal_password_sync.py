"""
Synchronise Portal password hashes into tenant ledger user records.

Portal authenticates with bcrypt; ledger stores the same hash so password-gated
flows (recovery key generation, force password change) validate against Portal credentials.
"""
from typing import Optional

_ACCEPTED_HASH_PREFIXES = ("scrypt:", "pbkdf2:", "$2a$", "$2b$", "$2y$")


def is_valid_password_hash(value: str) -> bool:
    normalized = (value or "").strip()
    if not normalized or len(normalized) > 512:
        return False
    return normalized.startswith(_ACCEPTED_HASH_PREFIXES)


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
