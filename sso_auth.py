"""
sso_auth.py - Phase 4B: JWT-based SSO authentication for Nexal Legal Ledger.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Dict, Optional

SSO_SECRET_KEY = os.environ.get(
    "SSO_SECRET_KEY",
    os.environ.get("NEXAL_SSO_SECRET", "nexal-legal-dev-secret-change-in-production-2026"),
)
SSO_TOKEN_TTL = int(os.environ.get("SSO_TOKEN_TTL", "300"))
SSO_ALGORITHM = "HS256"
SSO_ISSUER = "nexal-portal"
SSO_AUDIENCE = "nexal-ledger"

REQUIRED_CLAIMS = ("sub", "email", "firm_id", "role")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(value: str) -> bytes:
    padding = 4 - len(value) % 4
    if padding != 4:
        value += "=" * padding
    return base64.urlsafe_b64decode(value)


def _sign(header_b64: str, payload_b64: str, secret: str) -> str:
    message = (header_b64 + "." + payload_b64).encode()
    sig = hmac.new(secret.encode(), message, hashlib.sha256).digest()
    return _b64url_encode(sig)


def generate_sso_token(
    user_id: str,
    email: str,
    firm_id: str,
    role: str = "firm_admin",
    username: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate a signed JWT for portal-to-ledger SSO."""
    now = int(time.time())
    header = {"alg": SSO_ALGORITHM, "typ": "JWT"}
    payload: Dict[str, Any] = {
        "iss": SSO_ISSUER,
        "aud": SSO_AUDIENCE,
        "iat": now,
        "exp": now + SSO_TOKEN_TTL,
        "sub": user_id,
        "email": email,
        "firm_id": firm_id,
        "role": role,
        "jti": secrets.token_hex(16),
    }
    if username:
        payload["username"] = username
    if extra:
        payload.update(extra)
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signature = _sign(header_b64, payload_b64, SSO_SECRET_KEY)
    return header_b64 + "." + payload_b64 + "." + signature


def validate_sso_token(token: str) -> Dict[str, Any]:
    """Validate JWT signature, expiry, issuer, audience, and required claims."""
    if not token or not isinstance(token, str):
        raise ValueError("SSO token is missing or not a string")

    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Malformed JWT: expected 3 parts")

    header_b64, payload_b64, provided_sig = parts
    expected_sig = _sign(header_b64, payload_b64, SSO_SECRET_KEY)
    if not hmac.compare_digest(expected_sig, provided_sig):
        raise ValueError("JWT signature invalid")

    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception as exc:
        raise ValueError("JWT payload decode error: " + str(exc)) from exc

    now = int(time.time())
    if int(payload.get("exp", 0)) < now:
        raise ValueError("JWT expired")
    if payload.get("aud") != SSO_AUDIENCE:
        raise ValueError("JWT audience mismatch")
    if payload.get("iss") != SSO_ISSUER:
        raise ValueError("JWT issuer mismatch")

    for claim in REQUIRED_CLAIMS:
        if claim not in payload or payload[claim] in (None, ""):
            raise ValueError("JWT missing required claim: " + claim)

    return payload


def extract_firm_from_token(token: str) -> str:
    """Validate token and return firm_id claim (portal firm id)."""
    payload = validate_sso_token(token)
    return str(payload["firm_id"])


def is_token_valid(token: str) -> bool:
    try:
        validate_sso_token(token)
        return True
    except ValueError:
        return False


def enforce_firm_status(platform_firm_id: str) -> Dict[str, Any]:
    """Resolve platform firm and ensure it is active."""
    from nexal_platform.platform_db import PlatformDatabase

    platform = PlatformDatabase()
    firm = platform.get_firm_by_portal_firm_id(platform_firm_id)
    if firm is None:
        firm = _safe_get_firm(platform, platform_firm_id)
    if firm is None:
        raise ValueError("Firm not found for portal firm id: " + platform_firm_id)
    if firm["status"] != "active":
        raise ValueError("Firm is not active (status: " + str(firm["status"]) + ")")
    workspace = platform.get_workspace_for_firm(firm["id"])
    if workspace["status"] != "active":
        raise ValueError("Workspace is not active for firm: " + firm["id"])
    return firm


def _safe_get_firm(platform, firm_id: str) -> Optional[Dict[str, Any]]:
    try:
        return platform.get_firm(firm_id)
    except KeyError:
        return None


def map_portal_role_to_ledger(role: str) -> str:
    """Map portal roles to ledger roles (admin|staff)."""
    normalized = (role or "staff").strip().lower()
    if normalized in ("firm_admin", "admin"):
        return "admin"
    return "staff"


def build_session_from_token(payload: Dict[str, Any], ledger_user_id: int, platform_firm_id: str) -> Dict[str, Any]:
    """Build Flask session dict after portal user is resolved in ledger DB."""
    username = payload.get("username") or payload["email"].split("@")[0]
    return {
        "user_id": ledger_user_id,
        "username": username,
        "email": payload["email"],
        "firm_id": platform_firm_id,
        "role": map_portal_role_to_ledger(payload.get("role", "staff")),
        "portal_user_id": payload["sub"],
        "portal_role": payload.get("role", "staff"),
        "sso_login": True,
        "logged_in": True,
    }
