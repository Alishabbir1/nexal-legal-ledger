"""
sso_auth.py - Phase 4B: JWT-based SSO Authentication
Nexal Legal Ledger

Token flow:
  Portal generates JWT with generate_sso_token()
    Ledger validates JWT with validate_sso_token()
      Session populated with build_session_from_token()
      """

import hmac
import hashlib
import base64
import json
import time
import os
import secrets

SSO_SECRET_KEY = os.environ.get(
      "SSO_SECRET_KEY",
      "nexal-legal-dev-secret-change-in-production-2026"
)

SSO_TOKEN_TTL = int(os.environ.get("SSO_TOKEN_TTL", "300"))
SSO_ALGORITHM = "HS256"


def _b64url_encode(data):
      return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s):
      padding = 4 - len(s) % 4
      if padding != 4:
                s += "=" * padding
            return base64.urlsafe_b64decode(s)


def _sign(header_b64, payload_b64, secret):
      message = (header_b64 + "." + payload_b64).encode()
    sig = hmac.new(secret.encode(), message, hashlib.sha256).digest()
    return _b64url_encode(sig)


def generate_sso_token(portal_user_id, email, firm_id, username, role="staff", extra=None):
      """Generate a signed JWT for SSO from portal to ledger."""
    now = int(time.time())
    header = {"alg": SSO_ALGORITHM, "typ": "JWT"}
    payload = {
              "iss": "nexal-portal",
              "aud": "nexal-ledger",
              "iat": now,
              "exp": now + SSO_TOKEN_TTL,
              "sub": portal_user_id,
              "email": email,
              "firm_id": firm_id,
              "username": username,
              "role": role,
              "jti": secrets.token_hex(16),
    }
    if extra:
              payload.update(extra)
          header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signature = _sign(header_b64, payload_b64, SSO_SECRET_KEY)
    return header_b64 + "." + payload_b64 + "." + signature


def validate_sso_token(token):
      """Validate a JWT and return its payload. Raises ValueError on failure."""
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
        raise ValueError("JWT payload decode error: " + str(exc))
    now = int(time.time())
    if payload.get("exp", 0) < now:
              raise ValueError("JWT expired")
          if payload.get("aud") != "nexal-ledger":
                    raise ValueError("JWT audience mismatch")
                if payload.get("iss") != "nexal-portal":
                          raise ValueError("JWT issuer mismatch")
                      for claim in ("sub", "email", "firm_id", "username", "role"):
                                if claim not in payload:
                                              raise ValueError("JWT missing required claim: " + claim)
                                      return payload


def extract_firm_from_token(token):
      """Validate token and return firm_id."""
    payload = validate_sso_token(token)
    firm_id = payload.get("firm_id")
    if not firm_id:
              raise ValueError("JWT has no firm_id claim")
    return firm_id


def get_token_username(token):
      """Return the ledger username embedded in a validated JWT."""
    return validate_sso_token(token)["username"]


def is_token_valid(token):
      """Return True if token validates without raising; False otherwise."""
    try:
              validate_sso_token(token)
        return True
except ValueError:
        return False


def enforce_firm_status(firm_id):
      """Check platform.db that the firm is active. Raises ValueError on failure."""
    try:
              from platform_db import PlatformDB
        pdb = PlatformDB()
        firm = pdb.get_firm(firm_id)
        if firm is None:
                      raise ValueError("Firm not found: " + firm_id)
        if firm["status"] != "active":
                      raise ValueError("Firm " + firm_id + " is not active (status: " + firm["status"] + ")")
except ImportError:
        pass


def build_session_from_token(payload):
      """Convert a validated JWT payload into a Flask session dict."""
    return {
              "user_id":        payload["sub"],
              "username":       payload["username"],
              "email":          payload["email"],
              "firm_id":        payload["firm_id"],
              "role":           payload["role"],
              "portal_user_id": payload["sub"],
              "sso_login":      True,
        "logged_in":      True,
    }
