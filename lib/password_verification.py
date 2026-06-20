"""
Verify user passwords against stored hashes from Portal (bcrypt) or Ledger (werkzeug).
"""
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")
_bcrypt_available: Optional[bool] = None


def bcrypt_available() -> bool:
    """Return whether the bcrypt library is installed (required for Portal passwords)."""
    global _bcrypt_available
    if _bcrypt_available is None:
        try:
            import bcrypt  # noqa: F401

            _bcrypt_available = True
        except ImportError:
            _bcrypt_available = False
            logger.error(
                "bcrypt package is not installed; Portal password verification will fail. "
                "Run: pip install bcrypt"
            )
    return _bcrypt_available


def verify_password(stored_hash: Optional[str], password: str) -> bool:
    """
    Check a plaintext password against a stored hash.

    Supports Portal bcrypt hashes ($2b$...) and Ledger werkzeug scrypt/pbkdf2 hashes.
    """
    ok, _ = verify_password_detailed(stored_hash, password)
    return ok


def verify_password_detailed(stored_hash: Optional[str], password: str) -> Tuple[bool, Optional[str]]:
    """
    Verify password and return (success, system_error_message).

    system_error_message is set when failure is due to server misconfiguration,
    not a wrong password — callers should not increment lockout counters.
    """
    if not stored_hash or password is None:
        return False, None

    normalized = stored_hash.strip()
    if not normalized:
        return False, None

    if normalized.startswith(_BCRYPT_PREFIXES):
        if not bcrypt_available():
            return False, (
                "Password verification is unavailable on this server (bcrypt not installed). "
                "Please contact support."
            )
        return _verify_bcrypt(normalized, password), None

    from werkzeug.security import check_password_hash

    try:
        return check_password_hash(normalized, password), None
    except ValueError:
        if normalized.startswith("$2"):
            if not bcrypt_available():
                return False, (
                    "Password verification is unavailable on this server (bcrypt not installed). "
                    "Please contact support."
                )
            return _verify_bcrypt(normalized, password), None
        return False, None


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
