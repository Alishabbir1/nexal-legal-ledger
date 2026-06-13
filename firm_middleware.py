"""
firm_middleware.py - Phase 4B: Flask Firm Routing Middleware
Nexal Legal Ledger

Flask decorators and SSO route registration for firm-aware auth.
"""

from functools import wraps
from flask import session, request, redirect, url_for, jsonify, g


def handle_sso_login(token, flask_session):
      """Process an incoming SSO token, establish session. Returns (username, firm_id)."""
      from sso_auth import validate_sso_token
      from portal_bridge import establish_sso_session, ensure_portal_user_in_ledger
      payload = validate_sso_token(token)
      username = ensure_portal_user_in_ledger(payload)
      if username:
                payload["username"] = username
            establish_sso_session(flask_session, payload)
    return payload["username"], payload["firm_id"]


def require_login(f):
      """Decorator: require any valid login (SSO or username/password)."""
    @wraps(f)
    def decorated(*args, **kwargs):
              if not session.get("logged_in"):
                            if request.is_json:
                                              return jsonify({"error": "Authentication required"}), 401
                                          return redirect(url_for("login"))
                        return f(*args, **kwargs)
    return decorated


def require_sso_login(f):
      """Decorator: require SSO login with firm context."""
    @wraps(f)
    def decorated(*args, **kwargs):
              if not session.get("logged_in"):
                            if request.is_json:
                                              return jsonify({"error": "Authentication required"}), 401
                                          return redirect(url_for("login"))
        if not session.get("firm_id"):
                      if request.is_json:
                                        return jsonify({"error": "Firm context required"}), 403
                                    return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def require_firm_context(f):
      """Decorator: set g.firm_id and g.firm_db for route handler."""
    @wraps(f)
    def decorated(*args, **kwargs):
              if not session.get("logged_in"):
                            if request.is_json:
                                              return jsonify({"error": "Authentication required"}), 401
                                          return redirect(url_for("login"))
        firm_id = session.get("firm_id")
        if not firm_id:
                      if request.is_json:
                                        return jsonify({"error": "No firm context in session"}), 403
                                    return redirect(url_for("login"))
        try:
                      from db_router import get_db_for_firm
            g.firm_id = firm_id
            g.firm_db = get_db_for_firm(firm_id)
except Exception as e:
            if request.is_json:
                              return jsonify({"error": "Firm database unavailable: " + str(e)}), 503
                          return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def require_role(*allowed_roles):
      """Decorator factory: require specific role(s). @require_role('admin', 'firm_admin')"""
    def decorator(f):
              @wraps(f)
        def decorated(*args, **kwargs):
                      if not session.get("logged_in"):
                                        if request.is_json:
                                                              return jsonify({"error": "Authentication required"}), 401
                                                          return redirect(url_for("login"))
            if session.get("role", "") not in allowed_roles:
                              if request.is_json:
                                                    return jsonify({"error": "Insufficient permissions"}), 403
                                                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)
        return decorated
    return decorator


def get_current_firm_id():
      """Return current user firm_id from session."""
    return session.get("firm_id")


def get_current_username():
      """Return current username from session."""
    return session.get("username")


def get_current_role():
      """Return current role from session."""
    return session.get("role")


def is_sso_session():
      """Return True if session was established via SSO."""
    return bool(session.get("sso_login"))


def get_firm_db():
      """Get Database instance for current session firm. Returns None if no firm."""
    firm_id = session.get("firm_id")
    if not firm_id:
              return None
    try:
              from db_router import get_db_for_firm
        return get_db_for_firm(firm_id)
except Exception:
        return None


def register_sso_routes(app):
      """
          Register SSO routes on Flask app:
                GET/POST /auth/sso?token=JWT   - SSO entry from portal
                      GET      /auth/sso/status      - Session status JSON
                            GET/POST /auth/sso/logout      - Clear SSO session
                                """

    @app.route("/auth/sso", methods=["GET", "POST"])
    def sso_login():
              token = request.args.get("token") or request.form.get("token")
        if not token:
                      return jsonify({"error": "Missing SSO token", "code": "NO_TOKEN"}), 400
        try:
                      username, firm_id = handle_sso_login(token, session)
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)
except ValueError as e:
            return jsonify({"error": str(e), "code": "SSO_FAILED"}), 401

    @app.route("/auth/sso/status", methods=["GET"])
    def sso_status():
              if not session.get("logged_in"):
                            return jsonify({"authenticated": False}), 200
        return jsonify({
                      "authenticated": True,
                      "username": session.get("username"),
                      "firm_id": session.get("firm_id"),
                      "role": session.get("role"),
                      "sso_login": session.get("sso_login", False),
                      "email": session.get("email"),
        }), 200

    @app.route("/auth/sso/logout", methods=["POST", "GET"])
    def sso_logout():
              from portal_bridge import clear_sso_session
        clear_sso_session(session)
        return redirect(url_for("login"))

    return app
