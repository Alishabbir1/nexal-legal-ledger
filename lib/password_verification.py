"""
Verify user passwords against stored hashes from Portal (bcrypt) or Ledger (werkzeug).
"""
from typing import Optional

_BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")


def verify_password(stored_hash: Optional[str], password: str) -> bool:
    """
    Check a plaintext password against a stored hash.

    Supports Portal bcrypt hashes ($2b$...) and Ledger werkzeug scrypt/pbkdf2 hashes.
    """
    if not stored_hash or password is None:
        return False

    normalized = stored_hash.strip()
    if not normalized:
        return False

    if normalized.startswith(_BCRYPT_PREFIXES):
        return _verify_bcrypt(normalized, password)

    from werkzeug.security import check_password_hash

    try:
        return check_password_hash(normalized, password)
    except ValueError:
        if normalized.startswith("$2"):
            return _verify_bcrypt(normalized, password)
        return False


def _verify_bcrypt(stored_hash: str, password: str) -> bool:
    try:
        import bcrypt

        return bcrypt.checkpw(
            password.encode("utf-8"),
            stored_hash.encode("utf-8"),
        )
    except ImportError:
        return False
    except (ValueError, TypeError):
        return False
