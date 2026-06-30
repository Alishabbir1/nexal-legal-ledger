"""
firm_middleware.py - Phase 4B: Flask SSO routes and firm-aware helpers.
"""
import logging
import sqlite3
from functools import wraps
from typing import Any, Dict, Tuple

from flask import jsonify, redirect, request, session, url_for

from db_router import get_db_for_firm
from nexal_platform.session_security import safe_redirect_target
from portal_bridge import clear_sso_session, establish_sso_session, log_sso_audit
from sso_auth import validate_sso_token

logger = logging.getLogger(__name__)


def handle_sso_login(token: str, flask_session) -> Tuple[str, str]:
    """Process SSO token and establish authenticated session."""
    flask_session.clear()
    payload = validate_sso_token(token)
    session_data = establish_sso_session(flask_session, payload)
    firm_db = get_db_for_firm(session_data["firm_id"])
    log_sso_audit(
        firm_db,
        session_data["username"],
        session_data["role"],
        "SSO Login",
        "Portal SSO login for firm " + session_data["firm_id"],
    )
    return session_data["username"], session_data["firm_id"]


def _sso_error_response(exc: Exception, status: int = 401):
    logger.warning("SSO login failed: %s", exc)
    return jsonify({"error": "Sign-in failed. Please launch again from the Portal.", "code": "SSO_FAILED"}), status


def _sso_browser_error_response(exc: Exception, status: int = 401):
    """HTML response for browser form POST launches (never leave a blank JSON page)."""
    logger.warning("SSO login failed: %s", exc)
    portal_url = "https://nexal-legal.vercel.app/portal"
    try:
        from lib.portal_auth import get_portal_dashboard_url

        portal_url = get_portal_dashboard_url()
    except Exception:
        pass
    html = (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\" />"
        "<title>Ledger sign-in failed</title></head><body>"
        "<p>We could not open your ledger workspace. Please return to the Portal and "
        "click Launch Application again.</p>"
        f"<p><a href=\"{portal_url}\">Return to Portal</a></p>"
        "</body></html>"
    )
    return html, status, {"Content-Type": "text/html; charset=utf-8"}


def _handle_sso_exception(exc: Exception):
    """Map SSO failures to controlled HTTP responses — never leak a raw 500."""
    if isinstance(exc, (ValueError, PermissionError, KeyError, LookupError)):
        if request.method == "POST" and request.form.get("token"):
            return _sso_browser_error_response(exc)
        return _sso_error_response(exc)
    if isinstance(exc, sqlite3.Error):
        logger.exception("SSO database error")
        return jsonify(
            {
                "error": "Ledger database error during sign-in. Please contact support.",
                "code": "SSO_DB_ERROR",
            }
        ), 503
    if isinstance(exc, OSError):
        logger.exception("SSO filesystem error")
        return jsonify(
            {
                "error": "Ledger storage error during sign-in. Please contact support.",
                "code": "SSO_STORAGE_ERROR",
            }
        ), 503
    logger.exception("Unexpected SSO error")
    return jsonify(
        {
            "error": "Ledger sign-in failed. Please try again or contact support.",
            "code": "SSO_ERROR",
        }
    ), 503


def register_sso_routes(app):
    """Register SSO endpoints on the Flask application."""

    @app.route("/api/sso-login", methods=["POST"])
    def api_sso_login():
        token = None
        if request.is_json:
            token = (request.get_json(silent=True) or {}).get("token")
        token = token or request.form.get("token")
        if not token:
            return jsonify({"error": "Missing SSO token", "code": "NO_TOKEN"}), 400
        try:
            username, firm_id = handle_sso_login(token, session)
            return jsonify(
                {
                    "success": True,
                    "username": username,
                    "firm_id": firm_id,
                    "redirect": url_for("client_ledger"),
                }
            ), 200
        except Exception as exc:
            return _handle_sso_exception(exc)

    @app.route("/auth/sso", methods=["GET", "POST"])
    def sso_login():
        token = request.args.get("token") or request.form.get("token")
        if not token and request.is_json:
            token = (request.get_json(silent=True) or {}).get("token")
        if not token:
            return jsonify({"error": "Missing SSO token", "code": "NO_TOKEN"}), 400
        try:
            handle_sso_login(token, session)
            next_url = safe_redirect_target(request.args.get("next"))
            return redirect(next_url)
        except Exception as exc:
            return _handle_sso_exception(exc)

    @app.route("/auth/sso/status", methods=["GET"])
    def sso_status():
        if not session.get("user_id"):
            return jsonify({"authenticated": False}), 200
        return jsonify(
            {
                "authenticated": True,
                "username": session.get("username"),
                "firm_id": session.get("firm_id"),
                "role": session.get("role"),
                "sso_login": session.get("sso_login", False),
                "email": session.get("email"),
            }
        ), 200

    @app.route("/auth/sso/logout", methods=["POST", "GET"])
    def sso_logout():
        from lib.portal_auth import portal_logout_redirect

        clear_sso_session(session)
        session.clear()
        return portal_logout_redirect()

    return app


def get_firm_db():
    """Return routed database for current SSO session, else None."""
    firm_id = session.get("firm_id")
    if not firm_id or not session.get("sso_login"):
        return None
    return get_db_for_firm(firm_id)
