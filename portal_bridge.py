"""
portal_bridge.py - Phase 4B: Portal to Ledger Identity Bridge
Nexal Legal Ledger
"""

import os
from datetime import datetime


def resolve_portal_user(email, portal_user_id, firm_id):
      """Find ledger user record for a portal user by portal_user_id or email."""
      from platform_db import PlatformDB
      from db_router import get_db_for_firm
      pdb = PlatformDB()
      firm = pdb.get_firm(firm_id)
      if firm is None:
                raise ValueError("Firm not found: " + firm_id)
            if firm["status"] != "active":
                      raise ValueError("Firm " + firm_id + " is not active")
                  db = get_db_for_firm(firm_id)
    conn = db.get_connection()
    try:
              row = conn.execute(
                            "SELECT id, username, role, firm_id FROM users WHERE portal_user_id = ?",
                            (portal_user_id,)
              ).fetchone()
              if row:
                            return {"user_id": row[0], "username": row[1], "role": row[2], "firm_id": row[3] or firm_id}
                        if email:
                                      row = conn.execute(
                                                        "SELECT id, username, role, firm_id FROM users WHERE email = ? AND (firm_id = ? OR firm_id IS NULL)",
                                                        (email, firm_id)
                                      ).fetchone()
                                      if row:
                                                        conn.execute(
                                                                              "UPDATE users SET portal_user_id = ?, firm_id = ? WHERE id = ?",
                                                                              (portal_user_id, firm_id, row[0])
                                                        )
                                                        conn.commit()
                                                        return {"user_id": row[0], "username": row[1], "role": row[2], "firm_id": firm_id}
                                                return None
finally:
        conn.close()


def detect_firm_from_request(jwt_payload=None, session=None, request_args=None):
      """Detect firm_id from JWT payload, session, or request args (in priority order)."""
    if jwt_payload and "firm_id" in jwt_payload:
              return jwt_payload["firm_id"]
    if session and session.get("firm_id"):
              return session["firm_id"]
    if request_args and "firm_id" in request_args:
              return request_args["firm_id"]
    return None


def validate_firm_access(firm_id, username):
      """Verify a user belongs to the specified firm. Raises ValueError on cross-firm access."""
    from db_router import get_db_for_firm
    db = get_db_for_firm(firm_id)
    conn = db.get_connection()
    try:
              row = conn.execute(
                            "SELECT firm_id FROM users WHERE username = ?", (username,)
              ).fetchone()
        if row is None:
                      raise ValueError("User " + username + " not found in firm " + firm_id)
        user_firm = row[0]
        if user_firm and user_firm != firm_id:
                      raise ValueError(
                                        "Cross-firm access denied: user " + username +
                                        " belongs to " + str(user_firm) +
                                        " but requested " + firm_id
                      )
finally:
        conn.close()


def resolve_workspace(firm_id):
      """Return the active workspace record for a firm."""
    from platform_db import PlatformDB
    pdb = PlatformDB()
    ws = pdb.get_workspace_for_firm(firm_id)
    if ws is None:
              raise ValueError("No workspace found for firm: " + firm_id)
    if ws.get("status") != "active":
              raise ValueError("Workspace for firm " + firm_id + " is not active")
    return ws


def get_db_path_for_firm(firm_id):
      """Resolve the database file path for a firm."""
    ws = resolve_workspace(firm_id)
    db_path = ws.get("db_path")
    if not db_path or not os.path.exists(db_path):
              raise ValueError("Database not found for firm " + firm_id + " at: " + str(db_path))
    return db_path


def get_routed_db(firm_id):
      """Get a Database instance for the specified firm, fully routed."""
    from db_router import get_db_for_firm
    return get_db_for_firm(firm_id)


def verify_cross_firm_isolation(firm_id_a, firm_id_b):
      """Verify that two firms use completely separate databases."""
    from db_router import verify_isolation
    return verify_isolation(firm_id_a, firm_id_b)


def establish_sso_session(flask_session, jwt_payload):
      """Populate Flask session from validated JWT payload. Enforces firm status."""
    from sso_auth import enforce_firm_status, build_session_from_token
    enforce_firm_status(jwt_payload["firm_id"])
    session_data = build_session_from_token(jwt_payload)
    for key, value in session_data.items():
              flask_session[key] = value
    flask_session["sso_established_at"] = datetime.utcnow().isoformat()
    return session_data


def clear_sso_session(flask_session):
      """Remove all SSO-related keys from the session."""
    for key in ["user_id", "username", "email", "firm_id", "role",
                                "portal_user_id", "sso_login", "logged_in", "sso_established_at"]:
                                          flask_session.pop(key, None)


def validate_sso_request(token, expected_audience="nexal-ledger"):
      """Full security validation: validate JWT, check firm status. Returns payload."""
    from sso_auth import validate_sso_token, enforce_firm_status
    payload = validate_sso_token(token)
    if payload.get("aud") != expected_audience:
              raise ValueError("Token not intended for this service")
    enforce_firm_status(payload["firm_id"])
    return payload


def ensure_portal_user_in_ledger(jwt_payload):
      """Ensure portal user has a ledger record. Returns username or None."""
    portal_user_id = jwt_payload["sub"]
    email = jwt_payload["email"]
    firm_id = jwt_payload["firm_id"]
    username = jwt_payload.get("username")
    result = resolve_portal_user(email, portal_user_id, firm_id)
    if result:
              return result["username"]
    if username:
              from db_router import get_db_for_firm
        db = get_db_for_firm(firm_id)
        conn = db.get_connection()
        try:
                      row = conn.execute(
                                        "SELECT id, username FROM users WHERE username = ?", (username,)
                      ).fetchone()
            if row:
                              conn.execute(
                                                    "UPDATE users SET portal_user_id = ?, email = ?, firm_id = ? WHERE id = ?",
                                                    (portal_user_id, email, firm_id, row[0])
                              )
                conn.commit()
                return row[1]
finally:
            conn.close()
    return None
